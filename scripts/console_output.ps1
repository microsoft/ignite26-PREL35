function Write-LabMessage {
    param(
        [Parameter(Mandatory)][string]$Text,
        [ValidateSet('Red', 'Green', 'Yellow', 'Cyan')][string]$Color = 'Cyan'
    )

    $style = @{}
    if ($Host.UI.SupportsVirtualTerminal -and -not [Console]::IsOutputRedirected -and
        $null -eq [Environment]::GetEnvironmentVariable('NO_COLOR') -and -not $env:UV_NO_COLOR -and
        $env:TERM -ne 'dumb' -and $PSStyle.OutputRendering -ne 'PlainText') {
        $style.ForegroundColor = $Color
    }
    Write-Host $Text @style
}
