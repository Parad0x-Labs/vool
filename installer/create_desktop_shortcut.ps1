[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$TargetPath,
    [Parameter(Mandatory = $true)]
    [string]$WorkingDirectory,
    [Parameter(Mandatory = $true)]
    [string]$LinkPath,
    [Parameter(Mandatory = $false)]
    [string]$IconPath = ""
)

$ErrorActionPreference = "Stop"

# Resolve the user's REAL Desktop folder instead of trusting a hardcoded %USERPROFILE%\Desktop.
# Millions of Windows users have a REDIRECTED Desktop — OneDrive ("Back up your Desktop"),
# Dropbox ("My PC" backup), or a roaming/enterprise profile move the Desktop known-folder to
# e.g. ...\OneDrive\Desktop or a Dropbox path. In that case a shortcut written to
# %USERPROFILE%\Desktop lands in a folder the user never sees (so the shortcut appears "missing"),
# and if that folder doesn't exist at all, .Save() throws and no shortcut is created — which is
# exactly why the Desktop shortcut never showed up for anyone. [Environment]::GetFolderPath('Desktop')
# returns the real, redirected location via the Windows known-folder API.
$linkName = Split-Path -Leaf $LinkPath
if ([string]::IsNullOrWhiteSpace($linkName)) { $linkName = "OpenClaw + VOOL.lnk" }

$desktopDir = [Environment]::GetFolderPath('Desktop')
if ([string]::IsNullOrWhiteSpace($desktopDir)) {
    # Fall back to the directory the caller asked for if the known-folder lookup is empty.
    $desktopDir = Split-Path -Parent $LinkPath
}
if ([string]::IsNullOrWhiteSpace($desktopDir)) {
    $desktopDir = Join-Path $env:USERPROFILE "Desktop"
}
if (-not (Test-Path -LiteralPath $desktopDir)) {
    New-Item -ItemType Directory -Path $desktopDir -Force | Out-Null
}

$resolvedLink = Join-Path $desktopDir $linkName

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($resolvedLink)
$shortcut.TargetPath = $TargetPath
$shortcut.WorkingDirectory = $WorkingDirectory
# Use the VOOL mark when it's present. The target is a .bat, which otherwise inherits a generic
# Windows console/Explorer icon regardless of the default browser -- that's why the shortcut showed
# an Explorer icon. Fall back to a shell32 glyph only if the .ico asset is missing.
if (-not [string]::IsNullOrWhiteSpace($IconPath) -and (Test-Path -LiteralPath $IconPath)) {
    $shortcut.IconLocation = "$IconPath,0"
} else {
    $shortcut.IconLocation = "$env:SystemRoot\System32\shell32.dll,220"
}
$shortcut.Save()

# Emit the REAL path so the installer can report where the shortcut actually landed.
Write-Output $resolvedLink
