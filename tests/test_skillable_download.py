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
    if ($Headers.ContainsKey('Authorization')) { throw 'Unexpected authorization' }
    [IO.File]::WriteAllText($env:REQUEST_LOG, $Uri)
    Copy-Item -LiteralPath $env:SOURCE_ZIP -Destination $OutFile
}
& $env:DOWNLOAD_SCRIPT -TargetPath $env:LAB_TARGET -Ref 'release/2026'
""", encoding="utf-8")
    target = tmp_path / "ignite26-PREL35"
    environment = dict(os.environ, SOURCE_ZIP=str(archive), REQUEST_LOG=str(tmp_path / "request.txt"),
                       DOWNLOAD_SCRIPT=str(SCRIPT), LAB_TARGET=str(target),
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


@pytest.mark.parametrize("behavior", ["success", "throw", "write-error", "exit", "missing"])
def test_optional_vm_hook_runs_from_downloaded_repo_without_failing_checkout(download, behavior):
    run, target, archive, _ = download
    if behavior != "missing":
        failure = {
            "success": "",
            "throw": "throw 'Fixture hook failure'",
            "write-error": "Write-Error 'Fixture hook failure'",
            "exit": "exit 19",
        }[behavior]
        with zipfile.ZipFile(archive, "a") as zip_file:
            zip_file.writestr(
                "microsoft-ignite26-PREL35-fixture/infra/skillable/lifecycle_setup_vm.ps1",
                "if (-not (Test-Path -LiteralPath './README.md')) { throw 'Wrong working directory' }\n"
                "Set-Content -LiteralPath './hook-ran.txt' -Value 'Hook ran after checkout'\n"
                + failure,
            )
    result = run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert (target / "README.md").is_file()
    assert (target / "hook-ran.txt").exists() is (behavior != "missing")
    assert ("Optional VM setup skipped or failed" in result.stdout) is (behavior != "success")
    assert "Wrong working directory" not in result.stdout + result.stderr
    assert "Run uv run setup-lab as LabUser" in result.stdout
    assert not list(target.parent.glob("ignite26-PREL35-download-*"))


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


def test_source_download_does_not_send_environment_token(download):
    run, target, _, _ = download
    result = run(GITHUB_TOKEN="fixture-token")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (target / "README.md").is_file()
    assert "fixture-token" not in result.stdout + result.stderr


@pytest.mark.parametrize("failure", ["download", "wrong-source", "local-state"])
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
    result = run(**extra)
    assert result.returncode != 0
    assert not target.exists()
    assert not list(target.parent.glob("ignite26-PREL35-download-*"))
    assert "fixture-token" not in result.stdout + result.stderr
