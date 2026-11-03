import os
from pathlib import Path
import shutil
import subprocess
import zipfile

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "infra/skillable/lifecycle_github_repo_download.ps1"
PWSH = shutil.which("pwsh")
pytestmark = pytest.mark.skipif(not PWSH, reason="PowerShell is required")


@pytest.fixture
def download(tmp_path):
    archive = tmp_path / "source.zip"
    with zipfile.ZipFile(archive, "w") as zip_file:
        for name in ("README.md", "pyproject.toml", "azure.yaml", "src/decide.py",
                     "instructions/skillable/content.md", ".github/workflows/example.yml"):
            zip_file.writestr("microsoft-ignite26-PREL35-fixture/" + name, "Fixture source\n")
    harness = tmp_path / "download.ps1"
    harness.write_text("""
$ErrorActionPreference = 'Stop'
function Invoke-WebRequest {
    param($Uri, $Headers, $OutFile, [switch]$UseBasicParsing)
    if ($env:FAIL_DOWNLOAD -eq '1') { throw 'Fixture download error' }
    if ($Headers.Authorization -ne 'Bearer fixture-token') { throw 'Missing authorization' }
    [IO.File]::WriteAllText($env:REQUEST_LOG, $Uri)
    Copy-Item -LiteralPath $env:SOURCE_ZIP -Destination $OutFile
}
& $env:DOWNLOAD_SCRIPT -TargetPath $env:LAB_TARGET -Ref 'release/2026' -GitHubToken $env:TEST_TOKEN
""", encoding="utf-8")
    target = tmp_path / "ignite26-PREL35"
    environment = dict(os.environ, SOURCE_ZIP=str(archive), REQUEST_LOG=str(tmp_path / "request.txt"),
                       DOWNLOAD_SCRIPT=str(SCRIPT), LAB_TARGET=str(target), TEST_TOKEN="fixture-token",
                       GIT_CONFIG_GLOBAL=str(tmp_path / "global.gitconfig"), GIT_CONFIG_NOSYSTEM="1")

    def run(**extra):
        return subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-File", str(harness)],
                              env=environment | extra, capture_output=True, text=True, timeout=30)

    return run, target, archive, Path(environment["REQUEST_LOG"])


@pytest.mark.parametrize("existing_empty", [False, True])
def test_source_download_preserves_hidden_files_without_initializing_git(download, existing_empty):
    run, target, _, request = download
    if existing_empty:
        target.mkdir()
        directory_id = target.stat().st_ino
    result = run(GIT_AUTHOR_NAME="Unwanted identity", GIT_COMMITTER_NAME="Unwanted identity")
    assert result.returncode == 0, result.stdout + result.stderr
    if existing_empty:
        assert target.stat().st_ino == directory_id
    assert request.read_text() == "https://api.github.com/repos/microsoft/ignite26-PREL35/zipball/release%2F2026"
    assert (target / ".github/workflows/example.yml").exists()

    assert not (target / ".git").exists()
    assert not (target.parent / "global.gitconfig").exists()
    assert not list(target.parent.glob("ignite26-PREL35-download-*"))


def test_existing_checkout_is_never_overwritten(download):
    run, target, _, request = download
    target.mkdir()
    existing = target / ".env"
    existing.write_text("Fixture existing state")
    result = run()
    assert result.returncode != 0
    assert "already exists" in result.stderr
    assert existing.read_text() == "Fixture existing state"
    assert not request.exists()


def test_source_download_does_not_require_a_writable_git_profile(download):
    run, target, _, _ = download
    config = target.parent / "missing-profile" / "gitconfig"
    result = run(GIT_CONFIG_GLOBAL=str(config))
    assert result.returncode == 0, result.stdout + result.stderr
    assert (target / "README.md").is_file()
    assert not (target / ".git").exists()
    assert not config.exists()



def test_failed_download_preserves_existing_empty_directory(download):
    run, target, _, _ = download
    target.mkdir()
    directory_id = target.stat().st_ino
    result = run(FAIL_DOWNLOAD="1")
    assert result.returncode != 0
    assert target.is_dir()
    assert target.stat().st_ino == directory_id
    assert list(target.iterdir()) == []
    assert not list(target.parent.glob("ignite26-PREL35-download-*"))


def test_unedited_placeholder_can_use_action_scoped_environment_token(download):
    run, target, _, _ = download
    result = run(TEST_TOKEN="PASTE_YOUR_PAT_HERE", GITHUB_TOKEN="fixture-token")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (target / "README.md").is_file()


@pytest.mark.parametrize("failure", ["download", "wrong-source", "local-state", "missing-token", "placeholder"])
def test_failed_setup_does_not_install_partial_checkout_and_cleans_staging(download, failure):
    run, target, archive, _ = download
    extra = {}
    if failure == "download":
        extra["FAIL_DOWNLOAD"] = "1"
    elif failure == "wrong-source":
        with zipfile.ZipFile(archive, "w") as zip_file:
            zip_file.writestr("wrong-repository/README.md", "Wrong source")
    elif failure == "local-state":
        with zipfile.ZipFile(archive, "a") as zip_file:
            zip_file.writestr("microsoft-ignite26-PREL35-fixture/.env", "Fixture forbidden state")
    elif failure == "missing-token":
        extra["TEST_TOKEN"] = ""
    else:
        extra.update(TEST_TOKEN="PASTE_YOUR_PAT_HERE", GITHUB_TOKEN="")
    result = run(**extra)
    assert result.returncode != 0
    assert not target.exists()
    assert not list(target.parent.glob("ignite26-PREL35-download-*"))
    assert "fixture-token" not in result.stdout + result.stderr
