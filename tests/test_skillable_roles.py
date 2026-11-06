import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "infra/skillable/assign-learner.ps1"
CLEANUP_SCRIPT = SCRIPT.with_name("remove-setup-roles.ps1")
PWSH = shutil.which("pwsh")
pytestmark = pytest.mark.skipif(not PWSH, reason="PowerShell is required")
JSON_RUNNER = """
try {
    $result = @(& $env:ROLE_HARNESS)
    @{ Success = $true; Result = $result } | ConvertTo-Json -Compress
} catch {
    @{ Success = $false; Message = $_.Exception.Message } | ConvertTo-Json -Compress
    exit 1
}
"""


@pytest.mark.parametrize(
    ("script", "failure", "expected"),
    [
        (SCRIPT, "context", "No Skillable Azure context"),
        (SCRIPT, "resources", "Expected one Microsoft.CognitiveServices/accounts resource"),
        (SCRIPT, "http", "failed (HTTP 400). FixtureError"),
        (SCRIPT, "transport", "Fixture connection failed [redacted]"),
        (SCRIPT, "success", "Role assignments completed."),
        (CLEANUP_SCRIPT, "context", "No Skillable Azure context"),
        (CLEANUP_SCRIPT, "http", "failed (HTTP 400). FixtureError"),
        (CLEANUP_SCRIPT, "transport", "Fixture connection failed [redacted]"),
        (CLEANUP_SCRIPT, "success", "Role cleanup completed."),
    ],
)
def test_role_result_preserves_runner_json_without_credentials(tmp_path, script, failure, expected):
    harness = tmp_path / "roles.ps1"
    log = tmp_path / "roles.log"
    harness.write_text("""
$ErrorActionPreference = 'Stop'
function Get-AzContext {
    if ($env:FAILURE -eq 'context') { return $null }
    return @{ Subscription = @{ Id = '00000000-0000-0000-0000-000000000001' } }
}
function Get-AzAccessToken { return @{ Token = 'fixture-secret' } }
function Invoke-WebRequest {
    param($Uri, $Method, $Headers, $SkipHttpErrorCheck, $MaximumRedirection,
          $TimeoutSec, $ErrorAction, $Body, $ContentType)
    if ($env:FAILURE -eq 'transport') { throw 'Fixture connection failed fixture-secret' }
    if ($env:FAILURE -eq 'http') {
        $body = @{ error = @{ code = 'FixtureError'; message = ('detail ' * 500) } }
        return @{ StatusCode = 400; Content = ($body | ConvertTo-Json -Depth 3) }
    }
    if ($env:FAILURE -eq 'success') {
        $identity = @{ principalId = '00000000-0000-0000-0000-000000000002' }
        if ($Uri -like '*/resources?*') {
            $resources = @(
                @{ type = 'Microsoft.CognitiveServices/accounts'; id = '/fixture/foundry' },
                @{ type = 'Microsoft.Search/searchServices'; id = '/fixture/search' },
                @{ type = 'Microsoft.WebIQ/accounts'; id = '/fixture/webiq' },
                @{ type = 'Microsoft.ApiManagement/service'; id = '/fixture/apim' }
            )
            $body = @{ value = $resources }
        } elseif ($Uri -like '*/roleAssignments?*') {
            $body = @{ value = @() }
        } else {
            $body = @{ identity = $identity }
        }
        return @{ StatusCode = 200; Content = ($body | ConvertTo-Json -Depth 5) }
    }
    return @{ StatusCode = 200; Content = '{"value":[]}' }
}
$parameters = @{ ResourceGroup = 'fixture-rg'; LogPath = $env:ROLE_LOG }
if ((Split-Path $env:ROLE_SCRIPT -Leaf) -eq 'assign-learner.ps1') {
    $parameters.LearnerPrincipalId = '00000000-0000-0000-0000-000000000003'
}
& $env:ROLE_SCRIPT @parameters
""", encoding="utf-8")
    result = subprocess.run(
        [PWSH, "-NoProfile", "-NonInteractive", "-Command", JSON_RUNNER],
        env=dict(os.environ, ROLE_SCRIPT=str(script), ROLE_HARNESS=str(harness),
                 ROLE_LOG=str(log), FAILURE=failure),
        capture_output=True, text=True, timeout=30,
    )

    response = json.loads(result.stdout)
    assert result.stderr == ""
    assert "fixture-secret" not in result.stdout + result.stderr + log.read_text()
    if failure == "success":
        assert result.returncode == 0, result.stdout + result.stderr
        assert response == {"Success": True, "Result": [True]}
        assert expected in log.read_text()
        if script == SCRIPT:
            assert log.read_text().count("Assigned:") == 8
        return
    assert result.returncode != 0
    assert response["Success"] is False
    summary = response["Message"]
    action = "setup" if script == SCRIPT else "teardown"
    assert summary.startswith(f"Skillable {action} failed during '")
    assert len(summary.splitlines()) == 1
    assert expected in summary
    assert len(summary) <= 1512
    assert expected in log.read_text()
    if failure == "http":
        assert summary.endswith("[truncated]")
        assert log.read_text().count("detail ") == 500


@pytest.mark.parametrize("script", [SCRIPT, CLEANUP_SCRIPT])
def test_log_creation_failure_preserves_runner_json(tmp_path, script):
    log_parent = tmp_path / "not-a-directory"
    log_parent.write_text("Existing file")
    harness = tmp_path / "roles.ps1"
    harness.write_text("& $env:ROLE_SCRIPT -LogPath $env:ROLE_LOG", encoding="utf-8")
    result = subprocess.run(
        [PWSH, "-NoProfile", "-NonInteractive", "-Command", JSON_RUNNER],
        env=dict(os.environ, ROLE_SCRIPT=str(script), ROLE_HARNESS=str(harness),
                 ROLE_LOG=str(log_parent / "roles.log")),
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode != 0
    response = json.loads(result.stdout)
    assert result.stderr == ""
    assert response["Success"] is False
    action, step = ("setup", "assignments") if script == SCRIPT else ("teardown", "teardown")
    assert response["Message"].startswith(f"Skillable {action} failed during 'Starting role {step}':")
    assert log_parent.read_text() == "Existing file"
