<#
.SYNOPSIS
  Guarantee a usable CPython (>= MinMajor.MinMinor) is present, installing one if needed,
  and write its absolute path to -OutFile so the .bat installer can build its venv with it.

.DESCRIPTION
  A true one-click install cannot assume Python is already present. A fresh Windows host
  frequently has no Python, an old Python (3.9-), or only the Microsoft Store execution-alias
  stub (a 0-byte reparse point under \WindowsApps that opens the Store instead of running).

  This script:
    1. Searches for a real interpreter >= the required version (skipping the Store stub),
       version-checking each candidate by actually executing it.
    2. If none is found, installs Python per-user (no admin needed): winget first
       (Python.Python.3.12), falling back to the official python.org silent installer.
    3. Re-scans and writes the resolved python.exe path to -OutFile (progress goes to the
       console; only the path goes to the file, so the caller reads it deterministically).

  Exit code 0 = a usable Python path was written to -OutFile. Non-zero = give up.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$OutFile,
    [int]$MinMajor = 3,
    [int]$MinMinor = 10,
    # Pinned python.org fallback (used only when winget is unavailable/fails).
    [string]$FallbackVersion = "3.12.7"
)

$ErrorActionPreference = "Stop"

function Test-PythonCandidate {
    # Return the resolved absolute path if $Exe is a real interpreter >= the minimum, else $null.
    param([string]$Exe)
    if ([string]::IsNullOrWhiteSpace($Exe)) { return $null }
    # The Store execution-alias stub lives under \WindowsApps and must never be used: running it
    # non-interactively either opens the Store or hangs. Reject by path.
    if ($Exe -like "*\WindowsApps\*") { return $null }
    try {
        # -I (isolated) suppresses site/user startup so a sitecustomize banner can't precede
        # the value; take the last non-empty line to be safe regardless.
        $ver = (& $Exe -I -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null |
            Where-Object { $_ } | Select-Object -Last 1)
        if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($ver)) { return $null }
        $parts = ([string]$ver).Trim().Split('.')
        if ($parts.Count -lt 2) { return $null }
        $maj = [int]$parts[0]; $min = [int]$parts[1]
        if ($maj -gt $MinMajor -or ($maj -eq $MinMajor -and $min -ge $MinMinor)) {
            $resolved = (& $Exe -I -c "import sys; print(sys.executable)" 2>$null |
                Where-Object { $_ } | Select-Object -Last 1)
            if (-not [string]::IsNullOrWhiteSpace($resolved)) { return ([string]$resolved).Trim() }
            return $Exe
        }
    }
    catch { }
    return $null
}

function Find-Python {
    # 1) py launcher, newest requested first (respects real installs, not the Store stub).
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($v in "3.13", "3.12", "3.11", "3.10") {
            try {
                $path = (& py "-$v" -I -c "import sys; print(sys.executable)" 2>$null |
                    Where-Object { $_ } | Select-Object -Last 1)
                if ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace($path)) {
                    $ok = Test-PythonCandidate ([string]$path).Trim()
                    if ($ok) { return $ok }
                }
            }
            catch { }
        }
    }
    # 2) bare python / python3 on PATH. Use -All: without it Get-Command returns only the FIRST
    # match, so if the Microsoft Store stub is earlier on PATH than a real python, filtering the
    # stub out would leave nothing and wrongly miss the real interpreter behind it.
    foreach ($name in "python", "python3") {
        $cmds = Get-Command $name -All -ErrorAction SilentlyContinue |
            Where-Object { $_.Source -and $_.Source -notlike "*\WindowsApps\*" }
        foreach ($cmd in $cmds) {
            $ok = Test-PythonCandidate $cmd.Source
            if ($ok) { return $ok }
        }
    }
    # 3) Well-known install roots (covers a freshly-installed Python not yet on this session's PATH).
    $roots = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Python"),
        (Join-Path $env:ProgramFiles "Python"),
        "C:\"
    )
    if (${env:ProgramFiles(x86)}) { $roots += (Join-Path ${env:ProgramFiles(x86)} "Python") }
    foreach ($root in $roots) {
        if (-not (Test-Path -LiteralPath $root)) { continue }
        Get-ChildItem -LiteralPath $root -Directory -Filter "Python3*" -ErrorAction SilentlyContinue |
            Sort-Object Name -Descending |
            ForEach-Object {
                $exe = Join-Path $_.FullName "python.exe"
                if (Test-Path -LiteralPath $exe) {
                    $ok = Test-PythonCandidate $exe
                    if ($ok) { return $ok }
                }
            }
    }
    return $null
}

function Install-ViaWinget {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) { Write-Host "winget not available."; return $false }
    Write-Host "Trying winget (Python.Python.3.12, per-user, best-effort)..."
    # winget is BEST-EFFORT only. The Python EXE installer can self-elevate (a UAC prompt) even
    # with --scope user, which would stall a headless run indefinitely. Bound it with a hard
    # timeout and kill it if it doesn't finish, so the reliable no-admin python.org fallback
    # always gets its turn. --disable-interactivity suppresses winget's own prompts.
    $wingetArgs = @(
        "install", "-e", "--id", "Python.Python.3.12", "--scope", "user", "--silent",
        "--disable-interactivity", "--accept-source-agreements", "--accept-package-agreements"
    )
    try {
        $p = Start-Process -FilePath "winget" -ArgumentList $wingetArgs -PassThru -WindowStyle Hidden
        if (-not $p.WaitForExit(180000)) {
            Write-Host "winget did not finish within 180s (likely an elevation prompt); abandoning it for the python.org fallback."
            try { $p.Kill() } catch { }
            return $false
        }
        return ($p.ExitCode -eq 0)
    }
    catch { Write-Host "winget install failed: $($_.Exception.Message)"; return $false }
}

function Install-ViaPythonOrg {
    $url = "https://www.python.org/ftp/python/$FallbackVersion/python-$FallbackVersion-amd64.exe"
    $dst = Join-Path $env:TEMP "python-$FallbackVersion-amd64.exe"
    # Windows PowerShell 5.1 on an older/un-patched host may default to TLS 1.0, which python.org
    # rejects ("Could not create SSL/TLS secure channel"). Force TLS 1.2 (and 1.3 if available)
    # before the download so the bare-host fallback actually works.
    try {
        [Net.ServicePointManager]::SecurityProtocol =
            [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    }
    catch { }
    Write-Host "Downloading Python $FallbackVersion from python.org..."
    Invoke-WebRequest -Uri $url -OutFile $dst -UseBasicParsing
    Write-Host "Installing Python $FallbackVersion (per-user, silent)..."
    # InstallAllUsers=0 keeps it per-user so no elevation is required; PrependPath makes future
    # shells find it; Include_launcher installs the py launcher; no shortcuts/file-associations.
    $proc = Start-Process -FilePath $dst -Wait -PassThru -ArgumentList @(
        "/quiet", "InstallAllUsers=0", "PrependPath=1", "Include_pip=1",
        "Include_launcher=1", "Shortcuts=0", "AssociateFiles=0"
    )
    return ($proc.ExitCode -eq 0)
}

# --- main -------------------------------------------------------------------

$found = Find-Python
if (-not $found) {
    Write-Host "No suitable Python (>= $MinMajor.$MinMinor) found. Installing one automatically..."
    $installed = Install-ViaWinget
    if (-not $installed -or -not (Find-Python)) {
        Write-Host "Falling back to the python.org installer."
        try { $installed = Install-ViaPythonOrg }
        catch { Write-Host "python.org install failed: $($_.Exception.Message)"; $installed = $false }
    }
    $found = Find-Python
    if (-not $found) {
        # Last resort: the canonical per-user install location for 3.12/3.13.
        foreach ($guess in @(
                (Join-Path $env:LOCALAPPDATA "Programs\Python\Python313\python.exe"),
                (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"))) {
            if (Test-Path -LiteralPath $guess) { $found = Test-PythonCandidate $guess; if ($found) { break } }
        }
    }
}

if (-not $found) {
    Write-Error "Could not find or install a usable Python >= $MinMajor.$MinMinor."
    exit 1
}

# Write the path in the OEM codepage so cmd's `for /f` (which reads in the console/OEM codepage)
# gets it back intact even when the path contains a non-ASCII username. NoNewline avoids a
# trailing EOL that for/f would otherwise strip anyway.
Set-Content -LiteralPath $OutFile -Value $found -Encoding Oem -NoNewline
Write-Host "Using Python: $found"
exit 0
