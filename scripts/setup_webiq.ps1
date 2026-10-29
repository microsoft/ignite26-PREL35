# Organizer setup; the API key is entered through Python's hidden terminal prompt, never a command argument.
param([switch]$UseSavedKey)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$arguments = @('run', '--directory', $root, 'python', (Join-Path $PSScriptRoot 'setup_webiq.py'))
if ($UseSavedKey) { $arguments += '--use-saved-key' }
& uv @arguments
if ($LASTEXITCODE) { throw 'Web IQ setup did not finish. Resolve the message above and rerun.' }
