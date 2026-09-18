[CmdletBinding()]
param(
    [ValidateSet("auto-recommended", "local-only", "local-max", "ollama-only", "ollama-max")]
    [string]$InstallProfile = "auto-recommended",
    [string]$VoolHome = "",
    [string]$PackageVersion = "",
    [int]$BenchmarkTimeoutSeconds = 240,
    [switch]$SkipInstall,
    [switch]$SkipBenchmark,
    [switch]$SkipPackageBuild,
    [switch]$RequireOpenClaw,
    [switch]$Json
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$StartedAt = Get-Date
$ReportDir = Join-Path $ProjectRoot "dist\windows-gauntlet"
$Checks = New-Object System.Collections.Generic.List[object]
New-Item -ItemType Directory -Path $ReportDir -Force | Out-Null

function Add-Check {
    param(
        [string]$Name,
        [string]$Status,
        [Nullable[int]]$ExitCode,
        [double]$DurationSeconds,
        [string]$Detail
    )

    $Checks.Add([ordered]@{
        name = $Name
        status = $Status
        exitCode = $ExitCode
        durationSeconds = [Math]::Round($DurationSeconds, 2)
        detail = $Detail
    }) | Out-Null
}

function Resolve-Tool {
    param([string]$Name)

    $tools = @(Get-Command $Name -All -ErrorAction SilentlyContinue)
    if ($tools.Count -eq 0) {
        return ""
    }

    $selected = $tools |
        Where-Object { $_.Source -match "\.(cmd|exe)$" } |
        Select-Object -First 1
    if (-not $selected) {
        $selected = $tools | Select-Object -First 1
    }

    return [string]$selected.Source
}

function Resolve-Python {
    $venvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPython) {
        return $venvPython
    }

    $python = Resolve-Tool "python"
    if (-not [string]::IsNullOrWhiteSpace($python)) {
        return $python
    }

    $py = Resolve-Tool "py"
    if (-not [string]::IsNullOrWhiteSpace($py)) {
        return $py
    }

    throw "Python was not found. Install Python 3.10 or newer and rerun."
}

function Convert-ToSafeLogName {
    param([string]$Name)
    return (($Name -replace "[^A-Za-z0-9_.-]", "_").Trim("_"))
}

function Invoke-Checked {
    param(
        [string]$Name,
        [string]$FilePath,
        [string[]]$Arguments,
        [switch]$AllowSkip
    )

    if ([string]::IsNullOrWhiteSpace($FilePath)) {
        if ($AllowSkip) {
            Add-Check -Name $Name -Status "skipped" -ExitCode $null -DurationSeconds 0 -Detail "Required command was not found."
            return
        }
        throw "Required command for '$Name' was not found."
    }

    $started = Get-Date
    $logName = (Convert-ToSafeLogName $Name) + "-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".log"
    $logPath = Join-Path $ReportDir $logName
    Push-Location $ProjectRoot
    try {
        if (-not $Json) {
            Write-Host "==> $Name"
        }
        & $FilePath @Arguments *> $logPath
        $exitCode = $LASTEXITCODE
    } finally {
        Pop-Location
    }

    $duration = ((Get-Date) - $started).TotalSeconds
    $detail = "$FilePath $($Arguments -join ' ') | log=$logPath"
    Add-Check -Name $Name -Status ($(if ($exitCode -eq 0) { "passed" } else { "failed" })) -ExitCode $exitCode -DurationSeconds $duration -Detail $detail
    if ($exitCode -ne 0) {
        if (Test-Path -LiteralPath $logPath) {
            $tail = (Get-Content -LiteralPath $logPath -Tail 60 -ErrorAction SilentlyContinue) -join "`n"
            if (-not [string]::IsNullOrWhiteSpace($tail)) {
                Write-Host $tail
            }
        }
        throw "$Name failed with exit code $exitCode."
    }
}

function Invoke-Install {
    if ($SkipInstall) {
        Add-Check -Name "VOOL installer" -Status "skipped" -ExitCode $null -DurationSeconds 0 -Detail "Skipped by flag."
        return
    }

    $installer = Join-Path $ProjectRoot "Install_And_Run_VOOL.ps1"
    $args = @("-InstallProfile", $InstallProfile, "-AutoYes", "-NoStart")
    if (-not [string]::IsNullOrWhiteSpace($VoolHome)) {
        $args += @("-VoolHome", $VoolHome)
    }
    if ($SkipBenchmark) {
        $args += "-SkipBenchmark"
    }

    Invoke-Checked -Name "VOOL installer" -FilePath "powershell" -Arguments (@("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $installer) + $args)
}

function Invoke-FocusedRegression {
    $python = Resolve-Python
    $tests = @(
        "tests\test_hardware_tier.py",
        "tests\test_model_store_planner.py",
        "tests\test_provider_probe.py",
        "tests\test_provider_probe_contract.py",
        "tests\test_install_script_contract.py",
        "tests\test_install_surface_contracts.py",
        "tests\test_local_only_policy.py",
        "tests\test_validate_install_profile.py"
    )
    Invoke-Checked -Name "Focused Windows regression tests" -FilePath $python -Arguments (@("-m", "pytest") + $tests + @("-q", "--tb=short"))
}

function Invoke-ProviderProbe {
    $python = Resolve-Python
    Invoke-Checked -Name "Provider probe JSON" -FilePath $python -Arguments @("installer\provider_probe.py", "--json")

    if ($SkipBenchmark) {
        Add-Check -Name "Live local model benchmark" -Status "skipped" -ExitCode $null -DurationSeconds 0 -Detail "Skipped by flag."
    } else {
        Invoke-Checked -Name "Live local model benchmark" -FilePath $python -Arguments @("installer\provider_probe.py", "--benchmark", "--benchmark-timeout", [string]$BenchmarkTimeoutSeconds)
    }
}

function Invoke-PackageBuild {
    if ($SkipPackageBuild) {
        Add-Check -Name "Windows package build" -Status "skipped" -ExitCode $null -DurationSeconds 0 -Detail "Skipped by flag."
        return
    }

    $version = $PackageVersion
    if ([string]::IsNullOrWhiteSpace($version)) {
        $version = "gauntlet-" + (Get-Date -Format "yyyyMMdd-HHmmss")
    }

    Invoke-Checked -Name "Windows package build" -FilePath "powershell" -Arguments @(
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        "installer\build_windows_package.ps1",
        "-PackageVersion",
        $version
    )
}

function Invoke-OpenClawChecks {
    $openclaw = Resolve-Tool "openclaw"
    if ([string]::IsNullOrWhiteSpace($openclaw)) {
        if ($RequireOpenClaw) {
            throw "OpenClaw CLI was not found on PATH."
        }
        Add-Check -Name "OpenClaw CLI" -Status "skipped" -ExitCode $null -DurationSeconds 0 -Detail "OpenClaw CLI was not found on PATH."
        return
    }

    Invoke-Checked -Name "OpenClaw config validate" -FilePath $openclaw -Arguments @("config", "validate")
    Invoke-Checked -Name "OpenClaw doctor" -FilePath $openclaw -Arguments @("doctor", "--non-interactive", "--no-workspace-suggestions")
    Invoke-Checked -Name "OpenClaw gateway health" -FilePath $openclaw -Arguments @("gateway", "health")
    Invoke-Checked -Name "OpenClaw agents list" -FilePath $openclaw -Arguments @("agents", "list")
    Invoke-Checked -Name "OpenClaw memory status" -FilePath $openclaw -Arguments @("memory", "status", "--deep")
    Invoke-Checked -Name "OpenClaw VOOL exact response" -FilePath $openclaw -Arguments @("agent", "--agent", "vool", "--message", "Reply exactly OPENCLAW_VOOL_OK", "--json", "--timeout", "240")
}

function Complete-Run {
    param(
        [string]$Status,
        [string]$Message
    )

    $summary = [ordered]@{
        repository = "vool-local"
        schema = "vool.windows_fresh_host_gauntlet.v1"
        status = $Status
        message = $Message
        startedAt = $StartedAt.ToString("o")
        completedAt = (Get-Date).ToString("o")
        installProfile = $InstallProfile
        voolHome = $VoolHome
        checks = $Checks
    }
    $reportPath = Join-Path $ReportDir ("gauntlet-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".json")
    $summary | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $reportPath -Encoding UTF8

    if ($Json) {
        $summary | ConvertTo-Json -Depth 8
    } else {
        Write-Host ""
        Write-Host "Windows fresh-host gauntlet $Status."
        Write-Host $Message
        Write-Host "Report: $reportPath"
    }
}

try {
    Invoke-Install
    Invoke-FocusedRegression
    Invoke-ProviderProbe
    Invoke-PackageBuild
    Invoke-OpenClawChecks
    Complete-Run -Status "passed" -Message "All requested fresh-host checks passed."
    exit 0
} catch {
    Add-Check -Name "Failure" -Status "failed" -ExitCode 1 -DurationSeconds 0 -Detail $_.Exception.Message
    Complete-Run -Status "failed" -Message $_.Exception.Message
    exit 1
}
