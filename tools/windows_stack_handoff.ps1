[CmdletBinding()]
param(
    [ValidateSet("fast", "release")]
    [string]$Profile = "fast",
    [string]$StackRoot = "",
    [switch]$Json
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
if ([string]::IsNullOrWhiteSpace($StackRoot)) {
    $StackRoot = Split-Path -Parent $ProjectRoot
}
$StackRoot = (Resolve-Path -LiteralPath $StackRoot).Path
$StartedAt = Get-Date
$ReportDir = Join-Path $ProjectRoot "dist\windows-stack-handoff"
$Checks = New-Object System.Collections.Generic.List[object]
New-Item -ItemType Directory -Path $ReportDir -Force | Out-Null

function Convert-ToQuotedArgument {
    param([string]$Argument)

    if ($Argument -eq "") {
        return '""'
    }
    if ($Argument -notmatch '[\s"]') {
        return $Argument
    }
    return '"' + ($Argument -replace '"', '\"') + '"'
}

function Convert-ToSafeLogName {
    param([string]$Name)
    return (($Name -replace "[^A-Za-z0-9_.-]", "_").Trim("_"))
}

function Add-Check {
    param(
        [string]$Name,
        [string]$Repository,
        [string]$Status,
        [Nullable[int]]$ExitCode,
        [double]$DurationSeconds,
        [string]$Detail
    )

    $Checks.Add([ordered]@{
        name = $Name
        repository = $Repository
        status = $Status
        exitCode = $ExitCode
        durationSeconds = [Math]::Round($DurationSeconds, 2)
        detail = $Detail
    }) | Out-Null
}

function Invoke-Captured {
    param(
        [string]$Name,
        [string]$Repository,
        [string]$WorkingDirectory,
        [string]$FilePath,
        [string[]]$Arguments
    )

    $started = Get-Date
    $logName = (Convert-ToSafeLogName "$Repository-$Name") + "-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".log"
    $logPath = Join-Path $ReportDir $logName
    $stdoutPath = "$logPath.out"
    $stderrPath = "$logPath.err"

    if (-not $Json) {
        Write-Host "==> $Repository :: $Name"
    }

    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $FilePath
    $startInfo.Arguments = (($Arguments | ForEach-Object { Convert-ToQuotedArgument $_ }) -join " ")
    $startInfo.WorkingDirectory = $WorkingDirectory
    $startInfo.UseShellExecute = $false
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true

    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $startInfo
    $null = $process.Start()
    $stdout = $process.StandardOutput.ReadToEnd()
    $stderr = $process.StandardError.ReadToEnd()
    $process.WaitForExit()

    $stdout | Set-Content -LiteralPath $stdoutPath -Encoding UTF8
    $stderr | Set-Content -LiteralPath $stderrPath -Encoding UTF8
    $combined = (($stdout, $stderr) -join "`n").Trim()
    $combined | Set-Content -LiteralPath $logPath -Encoding UTF8

    $duration = ((Get-Date) - $started).TotalSeconds
    $detail = "$FilePath $($Arguments -join ' ') | log=$logPath"
    $status = if ($process.ExitCode -eq 0) { "passed" } else { "failed" }
    Add-Check -Name $Name -Repository $Repository -Status $status -ExitCode $process.ExitCode -DurationSeconds $duration -Detail $detail

    if ($process.ExitCode -ne 0) {
        $tail = ($combined -split "`r?`n" | Select-Object -Last 80) -join "`n"
        if (-not [string]::IsNullOrWhiteSpace($tail)) {
            Write-Host $tail
        }
        throw "$Repository $Name failed with exit code $($process.ExitCode)."
    }
}

function New-CommandSpec {
    param(
        [string]$FilePath,
        [string[]]$Arguments
    )
    return [ordered]@{
        file = $FilePath
        args = $Arguments
    }
}

$Repositories = @(
    [ordered]@{
        name = "vool-local"
        path = "vool-local"
        fast = New-CommandSpec "cmd.exe" @("/c", "Test_VOOL_Windows_Gauntlet.cmd", "-SkipInstall", "-SkipBenchmark", "-SkipPackageBuild")
        release = New-CommandSpec "cmd.exe" @("/c", "Test_VOOL_Windows_Gauntlet.cmd", "-SkipInstall", "-SkipBenchmark")
    },
    [ordered]@{
        name = "openclaw-skills"
        path = "openclaw-skills"
        fast = New-CommandSpec "powershell.exe" @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "tools\windows_validate.ps1", "-SkipSetup", "-SkipFullPython", "-SkipNode")
        release = New-CommandSpec "powershell.exe" @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "tools\windows_validate.ps1")
    },
    [ordered]@{
        name = "openclaw"
        path = "openclaw"
        fast = New-CommandSpec "powershell.exe" @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "scripts\windows-validate.ps1", "-Json", "-SkipDoctor")
        release = New-CommandSpec "cmd.exe" @("/c", "pnpm", "test:windows:ci")
    },
    [ordered]@{
        name = "liquefy"
        path = "liquefy"
        fast = New-CommandSpec "powershell.exe" @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "tools\windows_validate.ps1", "-SkipPytest", "-Json")
        release = New-CommandSpec "cmd.exe" @("/c", "Install_Liquefy_Windows.cmd", "-NoPath")
    },
    [ordered]@{
        name = "dna-x402"
        path = "dna-x402"
        fast = New-CommandSpec "powershell.exe" @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "scripts\windows_validate.ps1", "-Json")
        release = New-CommandSpec "cmd.exe" @("/c", "Install_DNA_X402_Windows.cmd", "-FullTests")
    },
    [ordered]@{
        name = "dna-x402-builders"
        path = "dna-x402-builders"
        fast = New-CommandSpec "powershell.exe" @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "scripts\windows_validate.ps1", "-SkipNpmCi", "-SkipTests", "-SkipAudit", "-Json")
        release = New-CommandSpec "cmd.exe" @("/c", "Install_DNA_X402_Builders_Windows.cmd")
    },
    [ordered]@{
        name = "web0-resolver"
        path = "web0-resolver"
        fast = New-CommandSpec "cmd.exe" @("/c", "Validate_Windows.cmd", "-Json")
        release = New-CommandSpec "cmd.exe" @("/c", "Validate_Windows.cmd", "-Json")
    },
    [ordered]@{
        name = "agent-null"
        path = "agent-null"
        fast = New-CommandSpec "powershell.exe" @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "scripts\windows_validate.ps1", "-SkipNpmInstall", "-SkipTests", "-Json")
        release = New-CommandSpec "cmd.exe" @("/c", "Install_Agent_Null_Windows.cmd")
    },
    [ordered]@{
        name = "Dark-Null-Protocol"
        path = "Dark-Null-Protocol"
        fast = New-CommandSpec "powershell.exe" @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "scripts\windows_validate.ps1", "-SkipNpmCi", "-SkipChecks", "-Json")
        release = New-CommandSpec "cmd.exe" @("/c", "Install_Dark_Null_Windows.cmd")
    },
    [ordered]@{
        name = "nebula-media"
        path = "nebula-media"
        fast = New-CommandSpec "powershell.exe" @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "scripts\windows_validate.ps1", "-SkipPytest", "-SkipVideoSmoke", "-Json")
        release = New-CommandSpec "cmd.exe" @("/c", "Install_Nebula_Media_Windows.cmd", "-SkipVideoSmoke")
    },
    [ordered]@{
        name = "web0"
        path = "web0"
        fast = New-CommandSpec "cmd.exe" @("/c", "Validate_Windows.cmd", "-Json")
        release = New-CommandSpec "cmd.exe" @("/c", "Validate_Windows.cmd", "-Json")
    },
    [ordered]@{
        name = "Parad0x-Command"
        path = "Parad0x-Command"
        fast = New-CommandSpec "powershell.exe" @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "scripts\windows_validate.ps1", "-SkipPytest", "-SkipCliSmoke", "-Json")
        release = New-CommandSpec "cmd.exe" @("/c", "Install_Parad0x_Command_Windows.cmd")
    },
    [ordered]@{
        name = "parad0x-media-engine"
        path = "parad0x-media-engine"
        fast = New-CommandSpec "powershell.exe" @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "scripts\windows_validate.ps1", "-SkipPytest", "-SkipImageSmoke", "-SkipFfmpegCheck", "-Json")
        release = New-CommandSpec "cmd.exe" @("/c", "Install_Parad0x_Media_Engine_Windows.cmd", "-SkipImageSmoke")
    }
)

function Complete-Run {
    param(
        [string]$Status,
        [string]$Message
    )

    $summary = [ordered]@{
        schema = "vool.windows_stack_handoff.v1"
        status = $Status
        message = $Message
        profile = $Profile
        stackRoot = $StackRoot
        startedAt = $StartedAt.ToString("o")
        completedAt = (Get-Date).ToString("o")
        checks = $Checks
    }

    $reportPath = Join-Path $ReportDir ("stack-handoff-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".json")
    $summary | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $reportPath -Encoding UTF8

    if ($Json) {
        $summary | ConvertTo-Json -Depth 8
    } else {
        Write-Host ""
        Write-Host "Windows stack handoff $Status."
        Write-Host $Message
        Write-Host "Report: $reportPath"
    }
}

try {
    foreach ($repo in $Repositories) {
        $repoPath = Join-Path $StackRoot $repo.path
        if (-not (Test-Path -LiteralPath $repoPath)) {
            Add-Check -Name "repository present" -Repository $repo.name -Status "failed" -ExitCode 1 -DurationSeconds 0 -Detail "Missing repository path: $repoPath"
            throw "Missing repository path: $repoPath"
        }

        $command = if ($Profile -eq "release") { $repo.release } else { $repo.fast }
        Invoke-Captured -Name $Profile -Repository $repo.name -WorkingDirectory $repoPath -FilePath $command.file -Arguments $command.args
    }

    Complete-Run -Status "passed" -Message "All Windows stack handoff checks passed."
    exit 0
} catch {
    Add-Check -Name "failure" -Repository "stack" -Status "failed" -ExitCode 1 -DurationSeconds 0 -Detail $_.Exception.Message
    Complete-Run -Status "failed" -Message $_.Exception.Message
    exit 1
}
