from unittest.mock import Mock

import pytest

from scripts import common, setup_lab


@pytest.fixture
def setup_steps(lab_root, azure_resources, azd_environment, monkeypatch):
    steps = []
    monkeypatch.setattr(setup_lab.shutil, "which", lambda name: name)
    monkeypatch.setattr(setup_lab.create_search_index, "main", Mock(side_effect=lambda args: steps.append(("index", args))))
    monkeypatch.setattr(setup_lab, "offer_webiq", Mock(side_effect=lambda: steps.append(("webiq", None))))
    # Any unmocked external command is a test failure, never a request to the developer's subscription.
    monkeypatch.setattr(common.subprocess, "run", Mock(side_effect=AssertionError("Unexpected external command")))
    return steps


def test_setup_seeds_local_corpus_and_kb_without_publishing_agents(
    lab_root, lab_settings, setup_steps, azd_environment, capsys,
):
    path = lab_root / ".env"
    path.write_text('# custom settings\nWEBIQ_API_KEY="fixture-secret"\nOBO_SCOPE=keep\n')
    assert setup_lab.main(["--resource-group", "fixture"]) == 0
    assert [step[0] for step in setup_steps] == ["index", "webiq"]
    args = setup_steps[0][1]
    assert args[args.index("--data") + 1] == str(lab_root / "data/investigator/schemes.json")
    assert args[args.index("--knowledge-base") + 1] == "fincrime-research"
    assert args[args.index("--endpoint") + 1] == "https://search-fixture.search.windows.net"
    assert azd_environment["values"]["AZD_AGENT_SKIP_ROLE_ASSIGNMENTS"] == "true"
    assert azd_environment["values"]["AZURE_AI_PROJECT_ID"].endswith("/foundry-fixture/projects/fincrime")
    assert common.read_settings(path)["OBO_SCOPE"] == "keep"
    assert 'WEBIQ_API_KEY="fixture-secret"' in path.read_text()
    assert "# custom settings" in path.read_text()
    assert "fixture-secret" not in str(capsys.readouterr())
    assert all(call[:2] == ["azd", "env"] for call in azd_environment["calls"])


@pytest.mark.parametrize("scenario", ["target", "model", "gateway", "monitoring", "ambiguous-foundry", "missing-search"])
def test_invalid_existing_lab_stops_before_writing_or_data_plane_work(
    scenario, lab_root, azure_resources, setup_steps, azd_environment,
):
    path = lab_root / ".env"
    before = "AZURE_RESOURCE_GROUP=another-lab\n" if scenario == "target" else "# keep\n"
    path.write_text(before)
    if scenario == "model":
        azure_resources["models"].pop()
    elif scenario in ("gateway", "monitoring"):
        azure_resources[scenario] = {"properties": {}}
    elif scenario == "ambiguous-foundry":
        azure_resources["resources"].append({**azure_resources["resources"][0], "name": "other-foundry"})
    elif scenario == "missing-search":
        azure_resources["resources"].pop(1)
    assert setup_lab.main(["--resource-group", "fixture"]) == 1
    assert path.read_text() == before
    assert setup_steps == []
    assert azd_environment["calls"] == []


def test_name_overrides_disambiguate_resources(azure_resources):
    azure_resources["resources"].append({**azure_resources["resources"][0], "name": "other-foundry"})
    settings = setup_lab.discover_settings("fixture", "FOUNDRY-fixture", "SEARCH-fixture")
    assert settings["FOUNDRY_NAME"] == "foundry-fixture"


def test_setup_without_azd_still_seeds_search(lab_root, setup_steps, monkeypatch, capsys):
    monkeypatch.setattr(setup_lab.shutil, "which", lambda name: None)
    assert setup_lab.main(["--resource-group", "fixture"]) == 0
    assert [step[0] for step in setup_steps] == ["index", "webiq"]
    assert "azd is not installed" in capsys.readouterr().out


def test_index_failure_stops_before_webiq(lab_root, setup_steps, monkeypatch, capsys):
    monkeypatch.setattr(setup_lab.create_search_index, "main", Mock(side_effect=RuntimeError("private response")))
    assert setup_lab.main(["--resource-group", "fixture"]) == 1
    setup_lab.offer_webiq.assert_not_called()
    output = capsys.readouterr()
    assert "Data-plane setup failed" in output.err
    assert "private response" not in str(output)
    assert "Lab setup finished" not in output.out


def test_missing_search_output_stops_before_data_plane(lab_settings, monkeypatch):
    lab_settings.pop("INVESTIGATOR_EMBEDDING_ENDPOINT")
    seed = Mock()
    monkeypatch.setattr(setup_lab.create_search_index, "main", seed)
    with pytest.raises(common.SetupError, match="INVESTIGATOR_EMBEDDING_ENDPOINT"):
        setup_lab.setup_search(lab_settings)
    seed.assert_not_called()


@pytest.mark.parametrize("answer,expected", [("y", True), ("YES", True), ("", False), ("n", False)])
def test_webiq_is_offered_and_runs_only_with_consent(answer, expected, monkeypatch):
    monkeypatch.setattr(setup_lab.sys.stdin, "isatty", lambda: True)
    prompt = Mock(return_value=answer)
    setup = Mock(return_value=0)
    monkeypatch.setattr("builtins.input", prompt)
    monkeypatch.setattr(setup_lab.setup_webiq, "main", setup)
    setup_lab.offer_webiq()
    prompt.assert_called_once_with("Set up Web IQ now? [y/N]: ")
    if expected:
        setup.assert_called_once_with(["--if-needed"])
    else:
        setup.assert_not_called()


def test_noninteractive_setup_never_waits_for_input(monkeypatch, capsys):
    monkeypatch.setattr(setup_lab.sys.stdin, "isatty", lambda: False)
    prompt = Mock(side_effect=AssertionError("Must not prompt"))
    monkeypatch.setattr("builtins.input", prompt)
    setup_lab.offer_webiq()
    assert "uv run setup-webiq" in capsys.readouterr().out
    prompt.assert_not_called()


def test_webiq_failure_is_not_reported_as_success(monkeypatch):
    monkeypatch.setattr(setup_lab.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _: "y")
    monkeypatch.setattr(setup_lab.setup_webiq, "main", lambda _: 1)
    with pytest.raises(common.SetupError, match="Web IQ setup did not finish"):
        setup_lab.offer_webiq()
