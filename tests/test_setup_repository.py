import os
import shutil
import subprocess

import pytest

from scripts import setup_lab


pytestmark = pytest.mark.skipif(not shutil.which("git"), reason="Git is required")


@pytest.fixture
def repository(tmp_path, monkeypatch):
    root = tmp_path / "lab"
    root.mkdir()
    (root / ".gitignore").write_text(".env\n.venv/\n.azure/\nevidence/\n")
    (root / "README.md").write_text("Fixture source\n")
    (root / ".env").write_text("Fixture private settings\n")
    (root / ".venv").mkdir()
    (root / ".venv/local.txt").write_text("Fixture environment\n")
    config = tmp_path / "global.gitconfig"
    config.write_text("[safe]\n\tdirectory = /fixture/existing\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setattr(setup_lab, "ROOT", root)
    return root, config


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def test_initializes_neutral_baseline_without_environment_or_remotes(repository, monkeypatch):
    root, _ = repository
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Fixture contributor")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Fixture contributor")
    setup_lab.initialize_repository()
    assert git(root, "branch", "--show-current") == "main"
    assert git(root, "rev-list", "--count", "HEAD") == "1"
    assert git(root, "status", "--porcelain") == ""
    assert git(root, "remote") == ""
    assert set(git(root, "ls-files").splitlines()) == {".gitignore", "README.md"}
    assert git(root, "log", "-1", "--format=%an|%ae|%cn|%ce") == (
        "Lab baseline|lab@example.invalid|Lab baseline|lab@example.invalid")
    assert os.environ["GIT_AUTHOR_NAME"] == "Fixture contributor"
    assert os.environ["GIT_COMMITTER_NAME"] == "Fixture contributor"


def test_repeated_initialization_preserves_history_branch_and_local_edits(repository):
    root, config = repository
    setup_lab.initialize_repository()
    head = git(root, "rev-parse", "HEAD")
    subprocess.run(["git", "-C", str(root), "switch", "-c", "contributor"], check=True, capture_output=True)
    (root / "README.md").write_text("Authored edit\n")
    before = config.read_bytes()
    setup_lab.initialize_repository()
    assert git(root, "rev-parse", "HEAD") == head
    assert git(root, "branch", "--show-current") == "contributor"
    assert (root / "README.md").read_text() == "Authored edit\n"
    assert config.read_bytes() == before
    assert git(root, "config", "--global", "--get-all", "safe.directory").splitlines() == [
        "/fixture/existing", root.resolve().as_posix(),
    ]


def test_missing_ignore_rules_cannot_commit_local_environment(repository):
    root, _ = repository
    (root / ".gitignore").unlink()
    with pytest.raises(setup_lab.SetupError, match="gitignore"):
        setup_lab.initialize_repository()
    assert not (root / ".git").exists()


def test_unwritable_user_git_configuration_stops_before_initializing(repository, monkeypatch):
    root, config = repository
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config.parent / "missing" / "gitconfig"))
    with pytest.raises(setup_lab.SetupError):
        setup_lab.initialize_repository()
    assert not (root / ".git").exists()
