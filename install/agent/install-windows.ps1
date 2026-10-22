<#
.SYNOPSIS
  Installs the Purrmox agent on Windows as a startup task (runs as SYSTEM, no window).

.EXAMPLE
  # Run in an elevated PowerShell. -AllowFrom limits the firewall rule to the Purrmox server.
  .\install-windows.ps1 -AllowFrom 192.168.1.50

.EXAMPLE
  # Install from a local checkout instead of GitHub
  .\install-windows.ps1 -Source "C:\Users\me\Projecten\Homelab\Purrmox" -AllowFrom 192.168.1.50

.EXAMPLE
  .\install-windows.ps1 -Uninstall
#>
param(
    [string]$AllowFrom = "Any",
    [int]$Port = 9100,
    [string]$SecretFile = "",  # optional: file with a ready-made shared secret (used only on first install)
    [string]$Source = "purrmox[agent] @ https://github.com/Hackercat-git/Purrmox/archive/refs/heads/main.zip",
    [switch]$Uninstall
)
$ErrorActionPreference = "Stop"
$TaskName = "PurrmoxAgent"
$Dir = Join-Path $env:ProgramData "PurrmoxAgent"
$RuleName = "Purrmox Agent"

$identity = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $identity.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this script in an elevated (administrator) PowerShell."
}

if ($Uninstall) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Remove-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue
    Write-Host "Task and firewall rule removed. Delete $Dir yourself when you no longer need it."
    return
}

# Find Python 3.9+
$python = $null
foreach ($cmd in @("py -3", "python")) {
    try {
        $exe = (& cmd /c "$cmd -c ""import sys;assert sys.version_info>=(3,9);print(sys.executable)""" 2>$null)
        if ($LASTEXITCODE -eq 0 -and $exe) { $python = ($exe | Select-Object -First 1).Trim(); break }
    } catch { }
}
if (-not $python) { throw "Python 3.9 or newer was not found. Install it from https://www.python.org/downloads/ first." }

New-Item -ItemType Directory -Force -Path $Dir | Out-Null
$venv = Join-Path $Dir "venv"
if (-not (Test-Path (Join-Path $venv "Scripts\python.exe"))) { & $python -m venv $venv }
$venvPython = Join-Path $venv "Scripts\python.exe"
# pip prints notices on stderr; PowerShell 5.1 treats those as errors under "Stop", so only the exit code counts.
$ErrorActionPreference = "Continue"
& $venvPython -m pip install --quiet --disable-pip-version-check --upgrade pip
& $venvPython -m pip install --quiet --disable-pip-version-check --upgrade $Source
& $venvPython -m pip install --quiet --disable-pip-version-check --force-reinstall --no-deps $Source
$pipExit = $LASTEXITCODE
$ErrorActionPreference = "Stop"
if ($pipExit -ne 0) { throw "Installing the agent failed." }

# Shared secret: keep an existing one so re-running the script does not break the dashboard.
$tokenFile = Join-Path $Dir "token.txt"
$newToken = $null
if (-not (Test-Path $tokenFile)) {
    if ($SecretFile) {
        Set-Content -Path $tokenFile -Value ((Get-Content -Raw $SecretFile).Trim()) -Encoding ascii -NoNewline
    } else {
        $newToken = & $venvPython -c "import secrets; print(secrets.token_urlsafe(32))"
        Set-Content -Path $tokenFile -Value $newToken -Encoding ascii -NoNewline
    }
}
# Only SYSTEM and administrators may read the token.
& icacls $tokenFile /inheritance:r /grant:r "SYSTEM:(R)" "Administrators:(F)" | Out-Null

# Firewall: private/domain networks only, optionally from one address.
Remove-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue
New-NetFirewallRule -DisplayName $RuleName -Direction Inbound -Protocol TCP -LocalPort $Port -Action Allow `
    -Profile Private, Domain -RemoteAddress $AllowFrom | Out-Null

# Startup task
$pythonw = Join-Path $venv "Scripts\pythonw.exe"
$action = New-ScheduledTaskAction -Execute $pythonw `
    -Argument "-m purrmox.agent --port $Port --token-file `"$tokenFile`"" -WorkingDirectory $Dir
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal `
    -Settings $settings -Force | Out-Null
Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
Start-ScheduledTask -TaskName $TaskName

Write-Host ""
Write-Host "Purrmox agent installed and started on port $Port (allowed from: $AllowFrom)."
if ($newToken) {
    Write-Host "Shared secret (shown once, put it in the 'hosts' section of the Purrmox config.yaml):"
    Write-Host "  $newToken"
} else {
    Write-Host "Shared secret stored in $tokenFile (administrators only)."
}
Write-Host "Check: Invoke-RestMethod http://localhost:$Port/healthz"
