[CmdletBinding()]
param(
    [string]$RepoOwner = $env:VOOL_GITHUB_OWNER,
    [string]$RepoName = $env:VOOL_GITHUB_REPO,
    [string]$Ref = $env:VOOL_GITHUB_REF,
    [string]$InstallDir = $env:VOOL_INSTALL_DIR,
    [string]$ArchiveUrl = $env:VOOL_ARCHIVE_URL,
    [string]$ArchiveSha256 = $env:VOOL_ARCHIVE_SHA256,
    [string]$SourceCommit = $env:VOOL_BUILD_COMMIT,
    [string]$SourceDirtyState = $env:VOOL_BUILD_DIRTY_STATE,
    [string]$InstallProfile = $env:VOOL_INSTALL_PROFILE,
    [switch]$NoStart
)

$ErrorActionPreference = "Stop"

function Resolve-DefaultInstallDir {
    # NULLA -> VOOL compatibility: reuse a pre-rename install directory instead of creating a second one.
    $legacyDir = Join-Path $HOME "nulla-local"
    $homeDefault = if ((Test-Path $legacyDir) -and -not (Test-Path (Join-Path $HOME "vool-local"))) { $legacyDir } else { Join-Path $HOME "vool-local" }
    try {
        $systemDriveRoot = ($env:SystemDrive + "\")
        $fixedDrives = [System.IO.DriveInfo]::GetDrives() |
            Where-Object { $_.IsReady -and $_.DriveType -eq [System.IO.DriveType]::Fixed }
        # Prefer a non-OS drive with the most free space; only fall back to the OS drive
        # if it's the sole fixed drive on the machine.
        $bestDrive = $fixedDrives |
            Where-Object { $_.RootDirectory.FullName -ne $systemDriveRoot } |
            Sort-Object AvailableFreeSpace -Descending |
            Select-Object -First 1
        if (-not $bestDrive) {
            $bestDrive = $fixedDrives | Sort-Object AvailableFreeSpace -Descending | Select-Object -First 1
        }
        if ($bestDrive) {
            return (Join-Path $bestDrive.RootDirectory.FullName "VOOL\vool-local")
        }
    }
    catch {
        return $homeDefault
    }
    return $homeDefault
}

if ([string]::IsNullOrWhiteSpace($RepoOwner)) { $RepoOwner = "Parad0x-Labs" }
if ([string]::IsNullOrWhiteSpace($RepoName)) { $RepoName = "vool-local" }
if ([string]::IsNullOrWhiteSpace($Ref)) { $Ref = "main" }
if ([string]::IsNullOrWhiteSpace($InstallDir)) { $InstallDir = Resolve-DefaultInstallDir }
if ([string]::IsNullOrWhiteSpace($ArchiveUrl)) { $ArchiveUrl = "https://github.com/$RepoOwner/$RepoName/archive/refs/heads/$Ref.zip" }

function Write-Info {
    param([string]$Message)
    Write-Host $Message
}

function Test-InstallDir {
    if (-not (Test-Path -LiteralPath $InstallDir)) {
        New-Item -ItemType Directory -Path $InstallDir | Out-Null
        return
    }

    if ((Test-Path -LiteralPath (Join-Path $InstallDir "Install_And_Run_VOOL.bat")) -or
        (Test-Path -LiteralPath (Join-Path $InstallDir "installer\\install_vool.bat")) -or
        (Test-Path -LiteralPath (Join-Path $InstallDir "install_vool.bat"))) {
        Write-Info "Existing VOOL install detected at $InstallDir"
        return
    }

    $items = Get-ChildItem -LiteralPath $InstallDir -Force
    if ($items.Count -gt 0) {
        throw "$InstallDir exists and is not an existing VOOL install. Use -InstallDir with an empty folder."
    }
}

function Download-And-Extract {
    $tmpDir = Join-Path ([System.IO.Path]::GetTempPath()) ("vool-bootstrap-" + [System.Guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $tmpDir | Out-Null
    try {
        $archivePath = Join-Path $tmpDir "vool.zip"
        $expandDir = Join-Path $tmpDir "expanded"
        Write-Info "Downloading VOOL from $ArchiveUrl"
        Invoke-WebRequest -Uri $ArchiveUrl -OutFile $archivePath -UseBasicParsing
        if ([string]::IsNullOrWhiteSpace($ArchiveSha256)) {
            Write-Info "WARNING: Downloaded archive is not checksum-verified. Set -ArchiveSha256 or VOOL_ARCHIVE_SHA256 to verify it."
        }
        else {
            $expected = $ArchiveSha256.Trim().ToLowerInvariant()
            $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $archivePath).Hash.ToLowerInvariant()
            if ($actual -ne $expected) {
                throw "Archive checksum mismatch. Expected $expected but got $actual."
            }
            Write-Info "Archive checksum verified."
        }

        Write-Info "Extracting to $InstallDir"
        Expand-Archive -LiteralPath $archivePath -DestinationPath $expandDir -Force
        $root = Get-ChildItem -LiteralPath $expandDir | Select-Object -First 1
        if (-not $root) {
            throw "Downloaded archive did not contain project files."
        }
        Get-ChildItem -LiteralPath $root.FullName -Force | ForEach-Object {
            Move-Item -LiteralPath $_.FullName -Destination $InstallDir -Force
        }
    }
    finally {
        Remove-Item -LiteralPath $tmpDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}

function Resolve-ArchiveCommit {
    if (-not [string]::IsNullOrWhiteSpace($SourceCommit)) {
        return $SourceCommit
    }
    if (($ArchiveUrl -notlike "https://github.com/$RepoOwner/$RepoName/archive/refs/*") -and
        ($ArchiveUrl -notlike "https://codeload.github.com/$RepoOwner/$RepoName/tar.gz/*")) {
        return ""
    }
    try {
        $payload = Invoke-RestMethod -Uri "https://api.github.com/repos/$RepoOwner/$RepoName/commits/$Ref" -UseBasicParsing
        return [string]$payload.sha
    }
    catch {
        return ""
    }
}

function Resolve-DirtyState {
    if ([string]::IsNullOrWhiteSpace($SourceDirtyState)) {
        return $null
    }
    switch ($SourceDirtyState.Trim().ToLowerInvariant()) {
        "1" { return $true }
        "true" { return $true }
        "yes" { return $true }
        "on" { return $true }
        "0" { return $false }
        "false" { return $false }
        "no" { return $false }
        "off" { return $false }
        default { return $null }
    }
}

function Write-BuildMetadata {
    param([string]$Commit)

    $configDir = Join-Path $InstallDir "config"
    if (-not (Test-Path -LiteralPath $configDir)) {
        New-Item -ItemType Directory -Path $configDir | Out-Null
    }
    $metadataPath = Join-Path $configDir "build-source.json"
    $dirtyState = Resolve-DirtyState
    @{
        ref = $Ref
        branch = $Ref
        commit = $Commit
        dirty_state = $dirtyState
        source_kind = "archive"
        source_url = $ArchiveUrl
    } | ConvertTo-Json | Set-Content -LiteralPath $metadataPath -Encoding UTF8
}

function Run-Installer {
    $psLauncher = Join-Path $InstallDir "Install_And_Run_VOOL.ps1"
    $launcher = Join-Path $InstallDir "Install_And_Run_VOOL.bat"
    $guided = Join-Path $InstallDir "Install_VOOL.bat"
    $canonical = Join-Path $InstallDir "installer\\install_vool.bat"
    if (-not (Test-Path -LiteralPath $canonical)) {
        $canonical = Join-Path $InstallDir "install_vool.bat"
    }

    Write-Info "Running VOOL installer..."
    $profileArgs = @()
    if (-not [string]::IsNullOrWhiteSpace($InstallProfile)) {
        $profileArgs = @("/INSTALLPROFILE=$InstallProfile")
    }
    if (Test-Path -LiteralPath $psLauncher) {
        $psArgs = @("-AutoYes")
        if (-not [string]::IsNullOrWhiteSpace($InstallProfile)) {
            $psArgs += @("-InstallProfile", $InstallProfile)
        }
        if ($NoStart) {
            $psArgs += "-NoStart"
        }
        & powershell -NoProfile -ExecutionPolicy Bypass -File $psLauncher @psArgs
        return
    }
    if ($NoStart) {
        if (Test-Path -LiteralPath $guided) {
            & $guided /Y "/OPENCLAW=default" @profileArgs
            return
        }
        if (Test-Path -LiteralPath $canonical) {
            & $canonical /Y "/OPENCLAW=default" @profileArgs
            return
        }
    }
    else {
        if (Test-Path -LiteralPath $launcher) {
            & $launcher @profileArgs
            return
        }
        if (Test-Path -LiteralPath $canonical) {
            & $canonical /Y /START "/OPENCLAW=default" @profileArgs
            return
        }
    }

    if (-not (Test-Path -LiteralPath $launcher) -and -not (Test-Path -LiteralPath $guided) -and -not (Test-Path -LiteralPath $canonical)) {
        throw "Bootstrap download succeeded, but no usable installer entrypoint was found."
    }
    if ($NoStart) {
        throw "Bootstrap download succeeded, but no guided installer entrypoint was found."
    }
    elseif (Test-Path -LiteralPath $launcher) {
        & $launcher @profileArgs
    }
    elseif (Test-Path -LiteralPath $canonical) {
        & $canonical /Y /START "/OPENCLAW=default" @profileArgs
    }
    else {
        throw "Bootstrap download succeeded, but no auto-start installer entrypoint was found."
    }
}

Test-InstallDir
Download-And-Extract
$resolvedCommit = Resolve-ArchiveCommit
Write-BuildMetadata -Commit $resolvedCommit
Run-Installer
