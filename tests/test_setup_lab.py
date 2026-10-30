import sys
from unittest.mock import Mock

import pytest

from scripts import common, configure_evidence_agent, setup_lab, setup_webiq


@pytest.fixture
def setup_steps(lab_root, azure_resources, azd_environment, monkeypatch):
    steps = []
    monkeypatch.setattr(setup_lab.create_search_index, "main", Mock(side_effect=lambda args: steps.append(("index", args))))
    monkeypatch.setattr(setup_webiq, "main", Mock(side_effect=AssertionError("Web IQ belongs to a later step")))
    monkeypatch.setattr(
        configure_evidence_agent, "configure_evidence",
        Mock(side_effect=AssertionError("Hosted-agent setup belongs to a later step")),
    )
    monkeypatch.setattr("builtins.input", Mock(side_effect=AssertionError("Initial setup must not prompt")))
    # Any unmocked external command is a test failure, never a request to the developer's subscription.
    monkeypatch.setattr(common.subprocess, "run", Mock(side_effect=AssertionError("Unexpected external command")))
    return steps


@pytest.mark.parametrize("interactive", [True, False])
def test_setup_seeds_local_corpus_and_kb_without_later_integrations(
    lab_root, lab_settings, setup_steps, azd_environment, capsys, monkeypatch, interactive,
):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: interactive)
    path = lab_root / ".env"
    path.write_text('# custom settings\nWEBIQ_API_KEY="fixture-secret"\nOBO_SCOPE=keep\n')
    assert setup_lab.main(["--resource-group", "fixture"]) == 0
    assert [step[0] for step in setup_steps] == ["index"]
    args = setup_steps[0][1]
    assert args[args.index("--data") + 1] == str(lab_root / "data/investigator/schemes.json")
    assert args[args.index("--knowledge-base") + 1] == "fincrime-research"
    assert args[args.index("--endpoint") + 1] == "https://search-fixture.search.windows.net"
    assert azd_environment["values"] == {}
    assert common.read_settings(path)["OBO_SCOPE"] == "keep"
    assert 'WEBIQ_API_KEY="fixture-secret"' in path.read_text()
    assert "# custom settings" in path.read_text()
    output = str(capsys.readouterr())
    assert "fixture-secret" not in output
    for premature_topic in ("Web IQ", "setup-webiq", "azd", "hosted", "Toolbox"):
        assert premature_topic not in output
    assert azd_environment["calls"] == []
    setup_webiq.main.assert_not_called()
    configure_evidence_agent.configure_evidence.assert_not_called()


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


def test_index_failure_does_not_report_completion(lab_root, setup_steps, monkeypatch, capsys):
    monkeypatch.setattr(setup_lab.create_search_index, "main", Mock(side_effect=RuntimeError("private response")))
    assert setup_lab.main(["--resource-group", "fixture"]) == 1
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
