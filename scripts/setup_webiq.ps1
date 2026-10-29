# Organizer setup; reuse the .env key or capture it through hidden input, never a command argument.
param(
    [Alias('Force')][switch]$UseSavedKey,
    [switch]$NoRun,
    [switch]$IfNeeded
)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$arguments = @('run', '--directory', $root, 'python', (Join-Path $PSScriptRoot 'setup_webiq.py'))
if ($UseSavedKey) { $arguments += '--use-saved-key' }
if ($NoRun) { $arguments += '--no-run' }
if ($IfNeeded) { $arguments += '--if-needed' }
& uv @arguments
if ($LASTEXITCODE) { throw 'Web IQ setup did not finish. Resolve the message above and rerun.' }
