[CmdletBinding()]
param(
    [ValidateSet("auto-recommended", "local-only", "local-max", "ollama-only", "ollama-max")]
    [string]$InstallProfile = "auto-recommended",
    [string]$VoolHome = "",
    [switch]$NoStart,
    [switch]$AutoYes,
    [switch]$SkipBenchmark
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$InstallerBat = Join-Path $ScriptDir "install_vool.bat"
$ProbeBat = Join-Path $ProjectRoot "Probe_VOOL_Stack.bat"
$LogPath = Join-Path $env:TEMP ("vool-windows-install-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".log")

function Write-InstallLog {
    param([string]$Message)
    $line = ("[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $Message)
    Write-Host $line
    Add-Content -LiteralPath $LogPath -Value $line -Encoding UTF8
}

function Convert-ProfileForBatch {
    param([string]$Profile)
    switch ($Profile) {
        "ollama-only" { return "local-only" }
        "ollama-max" { return "local-max" }
        default { return $Profile }
    }
}

function New-InstallerArgs {
    param([bool]$StartAfter)
    return @()
}

function Invoke-VoolBatchInstaller {
    param([bool]$StartAfter)
    if (-not (Test-Path -LiteralPath $InstallerBat)) {
        throw "Missing installer: $InstallerBat"
    }
    $installerArgs = New-InstallerArgs -StartAfter:$StartAfter
    $batchProfile = Convert-ProfileForBatch $InstallProfile
    Write-InstallLog "Running installer: $InstallerBat $($installerArgs -join ' ')"
    $previousInstallProfile = $env:VOOL_INSTALL_PROFILE
    $previousNullaHome = $env:VOOL_HOME
    $previousHeadless = $env:VOOL_HEADLESS
    $previousAutoStart = $env:VOOL_AUTO_START
    try {
        $env:VOOL_INSTALL_PROFILE = $batchProfile
        $env:VOOL_HEADLESS = "1"
        $env:VOOL_AUTO_START = if ($StartAfter) { "1" } else { "0" }
        if (-not [string]::IsNullOrWhiteSpace($VoolHome)) {
            $env:VOOL_HOME = $VoolHome
        }
        & $InstallerBat @installerArgs
        $installerExitCode = if ($null -ne $LASTEXITCODE) { $LASTEXITCODE } else { 0 }
    }
    finally {
        if ($null -eq $previousInstallProfile) {
            Remove-Item Env:VOOL_INSTALL_PROFILE -ErrorAction SilentlyContinue
        }
        else {
            $env:VOOL_INSTALL_PROFILE = $previousInstallProfile
        }
        if ($null -eq $previousNullaHome) {
            Remove-Item Env:VOOL_HOME -ErrorAction SilentlyContinue
        }
        else {
            $env:VOOL_HOME = $previousNullaHome
        }
        if ($null -eq $previousHeadless) {
            Remove-Item Env:VOOL_HEADLESS -ErrorAction SilentlyContinue
        }
        else {
            $env:VOOL_HEADLESS = $previousHeadless
        }
        if ($null -eq $previousAutoStart) {
            Remove-Item Env:VOOL_AUTO_START -ErrorAction SilentlyContinue
        }
        else {
            $env:VOOL_AUTO_START = $previousAutoStart
        }
    }
    Write-InstallLog "Installer exited with code $installerExitCode. Log: $LogPath"
    if ($installerExitCode -ne 0) {
        throw "VOOL installer failed with exit code $installerExitCode."
    }
}

function Invoke-ProviderProbe {
    param([bool]$Benchmark)
    $python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    $probe = Join-Path $ProjectRoot "installer\provider_probe.py"
    if ($Benchmark -and (Test-Path -LiteralPath $python) -and (Test-Path -LiteralPath $probe)) {
        Write-InstallLog "Running live local model check: $probe --benchmark --benchmark-timeout 240"
        & $python $probe --benchmark --benchmark-timeout 240 | Tee-Object -FilePath $LogPath -Append
        return
    }
    if (Test-Path -LiteralPath $ProbeBat) {
        Write-InstallLog "Running provider probe: $ProbeBat"
        & $ProbeBat | Tee-Object -FilePath $LogPath -Append
        return
    }
    if ((Test-Path -LiteralPath $python) -and (Test-Path -LiteralPath $probe)) {
        Write-InstallLog "Running provider probe: $probe"
        & $python $probe | Tee-Object -FilePath $LogPath -Append
    }
}

if ($AutoYes -or $env:VOOL_HEADLESS -eq "1") {
    Invoke-VoolBatchInstaller -StartAfter:(-not $NoStart)
    Invoke-ProviderProbe -Benchmark:(-not $SkipBenchmark)
    exit 0
}

try {
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing
}
catch {
    Write-InstallLog "Windows Forms is unavailable; falling back to headless install."
    Invoke-VoolBatchInstaller -StartAfter:(-not $NoStart)
    Invoke-ProviderProbe -Benchmark:(-not $SkipBenchmark)
    exit 0
}

[System.Windows.Forms.Application]::EnableVisualStyles()

$form = New-Object System.Windows.Forms.Form
$form.Text = "VOOL Windows Installer"
$form.StartPosition = "CenterScreen"
$form.Width = 620
$form.Height = 390
$form.FormBorderStyle = "FixedDialog"
$form.MaximizeBox = $false

$title = New-Object System.Windows.Forms.Label
$title.Text = "VOOL + OpenClaw local installer"
$title.Font = New-Object System.Drawing.Font("Segoe UI", 14, [System.Drawing.FontStyle]::Bold)
$title.AutoSize = $true
$title.Left = 24
$title.Top = 22
$form.Controls.Add($title)

$profileLabel = New-Object System.Windows.Forms.Label
$profileLabel.Text = "Install profile"
$profileLabel.Left = 24
$profileLabel.Top = 76
$profileLabel.Width = 140
$form.Controls.Add($profileLabel)

$profileBox = New-Object System.Windows.Forms.ComboBox
$profileBox.Left = 170
$profileBox.Top = 72
$profileBox.Width = 220
$profileBox.DropDownStyle = "DropDownList"
[void]$profileBox.Items.AddRange(@("auto-recommended", "local-only", "local-max", "ollama-only", "ollama-max"))
$profileBox.SelectedItem = $InstallProfile
$form.Controls.Add($profileBox)

$homeLabel = New-Object System.Windows.Forms.Label
$homeLabel.Text = "Runtime home"
$homeLabel.Left = 24
$homeLabel.Top = 116
$homeLabel.Width = 140
$form.Controls.Add($homeLabel)

$homeBox = New-Object System.Windows.Forms.TextBox
$homeBox.Left = 170
$homeBox.Top = 112
$homeBox.Width = 330
$homeBox.Text = $VoolHome
$form.Controls.Add($homeBox)

$browseButton = New-Object System.Windows.Forms.Button
$browseButton.Text = "Browse"
$browseButton.Left = 510
$browseButton.Top = 110
$browseButton.Width = 70
$browseButton.Add_Click({
    $dialog = New-Object System.Windows.Forms.FolderBrowserDialog
    $dialog.Description = "Choose VOOL runtime home"
    if ($dialog.ShowDialog($form) -eq [System.Windows.Forms.DialogResult]::OK) {
        $homeBox.Text = $dialog.SelectedPath
    }
})
$form.Controls.Add($browseButton)

$startCheck = New-Object System.Windows.Forms.CheckBox
$startCheck.Text = "Start VOOL and OpenClaw after install"
$startCheck.Left = 170
$startCheck.Top = 150
$startCheck.Width = 320
$startCheck.Checked = (-not $NoStart)
$form.Controls.Add($startCheck)

$benchmarkCheck = New-Object System.Windows.Forms.CheckBox
$benchmarkCheck.Text = "Run live local model check after install"
$benchmarkCheck.Left = 170
$benchmarkCheck.Top = 172
$benchmarkCheck.Width = 340
$benchmarkCheck.Checked = (-not $SkipBenchmark)
$form.Controls.Add($benchmarkCheck)

$statusBox = New-Object System.Windows.Forms.TextBox
$statusBox.Left = 24
$statusBox.Top = 202
$statusBox.Width = 556
$statusBox.Height = 68
$statusBox.Multiline = $true
$statusBox.ReadOnly = $true
$statusBox.Text = "Ready. Logs will be written to $LogPath"
$form.Controls.Add($statusBox)

$probeButton = New-Object System.Windows.Forms.Button
$probeButton.Text = "Probe PC"
$probeButton.Left = 24
$probeButton.Top = 300
$probeButton.Width = 100
$probeButton.Add_Click({
    try {
        Invoke-ProviderProbe -Benchmark:([bool]$benchmarkCheck.Checked)
        $statusBox.Text = "Probe complete. Log: $LogPath"
    }
    catch {
        $statusBox.Text = "Probe failed: $($_.Exception.Message)"
    }
})
$form.Controls.Add($probeButton)

$installButton = New-Object System.Windows.Forms.Button
$installButton.Text = "Install"
$installButton.Left = 350
$installButton.Top = 300
$installButton.Width = 100
$installButton.Add_Click({
    $script:InstallProfile = [string]$profileBox.SelectedItem
    $script:VoolHome = [string]$homeBox.Text
    $statusBox.Text = "Installing. This can take several minutes. Log: $LogPath"
    $form.Refresh()
    try {
        Invoke-VoolBatchInstaller -StartAfter:([bool]$startCheck.Checked)
        Invoke-ProviderProbe -Benchmark:([bool]$benchmarkCheck.Checked)
        $statusBox.Text = "Install complete. Desktop shortcut should be available. Log: $LogPath"
        [System.Windows.Forms.MessageBox]::Show($form, "VOOL install completed.", "VOOL Windows Installer") | Out-Null
    }
    catch {
        $statusBox.Text = "Install failed: $($_.Exception.Message)`r`nLog: $LogPath"
        [System.Windows.Forms.MessageBox]::Show($form, $_.Exception.Message, "VOOL install failed") | Out-Null
    }
})
$form.Controls.Add($installButton)

$closeButton = New-Object System.Windows.Forms.Button
$closeButton.Text = "Close"
$closeButton.Left = 480
$closeButton.Top = 300
$closeButton.Width = 100
$closeButton.Add_Click({ $form.Close() })
$form.Controls.Add($closeButton)

[void]$form.ShowDialog()
