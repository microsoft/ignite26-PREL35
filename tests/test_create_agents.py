import runpy
from unittest.mock import Mock

import pytest
from azure.core.exceptions import HttpResponseError

from scripts import create_agents


@pytest.fixture
def publisher(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(
        "FOUNDRY_PROJECT_ENDPOINT=https://fixture.example\nMODEL_DEPLOYMENT=gpt-5-mini\n",
        encoding="utf-8-sig",
    )
    prompts = tmp_path / "src" / "agents"
    prompts.mkdir(parents=True)
    for name in ("triage", "reviewer"):
        (prompts / f"{name}.md").write_text(f"{name} instructions", encoding="utf-8")
    (prompts / "evidence").mkdir()
    (prompts / "evidence" / "agent.md").write_text("hosted instructions", encoding="utf-8")
    monkeypatch.setattr(create_agents, "ROOT", tmp_path)
    credential = Mock()
    monkeypatch.setattr(create_agents, "DefaultAzureCredential", credential)
    client = Mock()
    client.return_value.agents.create_version.side_effect = lambda **kwargs: Mock(
        name=kwargs["agent_name"], version="1",
    )
    monkeypatch.setattr(create_agents, "AIProjectClient", client)
    return client, credential


def test_import_and_help_do_not_read_settings_or_connect(publisher, monkeypatch):
    client, credential = publisher
    with monkeypatch.context() as context:
        context.setattr("azure.ai.projects.AIProjectClient", client)
        context.setattr("azure.identity.DefaultAzureCredential", credential)
        runpy.run_path(create_agents.__file__, run_name="import_probe")
    with pytest.raises(SystemExit) as result:
        create_agents.main(["--help"])
    assert result.value.code == 0
    client.assert_not_called()
    credential.assert_not_called()


@pytest.mark.parametrize("arguments,model", [([], "gpt-5-mini"), (["override-model"], "override-model")])
def test_publishes_only_top_level_prompts_and_preserves_triage_lite(publisher, arguments, model):
    client, credential = publisher
    assert create_agents.main(arguments) == 0
    client.assert_called_once_with(
        endpoint="https://fixture.example", credential=credential.return_value,
    )
    calls = client.return_value.agents.create_version.call_args_list
    assert [call.kwargs["agent_name"] for call in calls] == ["reviewer", "triage", "triage-lite"]
    assert [call.kwargs["definition"].model for call in calls] == [model, model, "gpt-5-nano"]
    assert [call.kwargs["definition"].instructions for call in calls] == [
        "reviewer instructions", "triage instructions", "triage instructions",
    ]
    assert all(call.kwargs["definition"].reasoning.effort == "low" for call in calls)
    assert all(call.kwargs["definition"].as_dict()["text"]["format"] == {"type": "json_object"} for call in calls)


@pytest.mark.parametrize("status,exhausted", [(403, False), (403, True), (401, False), (500, False)])
def test_retries_only_forbidden_errors_and_stops_after_ten_attempts(publisher, monkeypatch, status, exhausted):
    client, _ = publisher
    publish = client.return_value.agents.create_version
    error = HttpResponseError("fixture failure")
    error.status_code = status
    success = Mock(name="fixture-agent", version="1")
    publish.side_effect = [error] * 10 if exhausted else [error, success, success, success]
    sleep = Mock()
    monkeypatch.setattr(create_agents.time, "sleep", sleep)
    if status != 403 or exhausted:
        with pytest.raises(HttpResponseError):
            create_agents.main([])
        assert publish.call_count == (10 if exhausted else 1)
        assert sleep.call_count == (9 if exhausted else 0)
    else:
        assert create_agents.main([]) == 0
        assert publish.call_count == 4
        sleep.assert_called_once_with(30)
