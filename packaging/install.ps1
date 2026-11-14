# Install or upgrade radio-web-remote for the current user (Windows 10/11).
# No admin rights needed.
#
#   irm https://raw.githubusercontent.com/kidvelvet/radio-web-remote/main/packaging/install.ps1 | iex
#   .\install.ps1 -Service      also start it at logon and restart it if it stops
#   .\install.ps1 -Uninstall    remove the program and the task; keeps your settings
#
# Environment:
#   RWR_PYTHON   path to a Python 3.11+ (default: "py -3", then "python")
#   RWR_SOURCE   what to install: a release wheel URL (default), a wheel file, or a source folder
#   RWR_PREFIX   program folder (default: %LOCALAPPDATA%\Programs\radio-web-remote)
#
# Settings, accounts and certificates live in %LOCALAPPDATA%\radio-web-remote
# and are never touched by this script.
#
# The radios need Icom's USB driver from icomjapan.com (docs/install-windows.md).
[CmdletBinding()]
param([switch]$Service, [switch]$Uninstall)

# Everything runs in a script block, so under `irm | iex` nothing (such as
# $ErrorActionPreference) leaks into the user's PowerShell session.
& {
    param([bool]$Service, [bool]$Uninstall)
    $ErrorActionPreference = "Stop"

    $Version = "0.1.0"
    $Repo = "https://github.com/kidvelvet/radio-web-remote"
    $Source = if ($env:RWR_SOURCE) { $env:RWR_SOURCE } else {
        "$Repo/releases/download/v$Version/radio_web_remote-$Version-py3-none-any.whl"
    }
    $Prefix = if ($env:RWR_PREFIX) { $env:RWR_PREFIX } else { Join-Path $env:LOCALAPPDATA "Programs\radio-web-remote" }
    $Scripts = Join-Path $Prefix "venv\Scripts"
    $Exe = Join-Path $Scripts "radio-web-remote.exe"
    $TaskName = "radio-web-remote"

    function Remove-Task {
        if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
            Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
            Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
            Write-Host "Removed the scheduled task $TaskName."
        }
    }

    function Set-UserPath([string]$dir, [bool]$add) {
        # Read and write the raw registry value: GetEnvironmentVariable expands
        # %USERPROFILE% and friends, and writing that back would freeze them.
        $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey("Environment", $true)
        $raw = [string]$key.GetValue("Path", "", [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
        $parts = @($raw -split ";" | Where-Object { $_ -and $_ -ne $dir })
        if ($add) { $parts += $dir }
        $key.SetValue("Path", ($parts -join ";"), [Microsoft.Win32.RegistryValueKind]::ExpandString)
        $key.Close()
        # Tell running programs (Explorer) that the environment changed.
        [Environment]::SetEnvironmentVariable("RADIO_WEB_REMOTE_PATH_CHANGED", "1", "User")
        [Environment]::SetEnvironmentVariable("RADIO_WEB_REMOTE_PATH_CHANGED", $null, "User")
    }

    if ($Uninstall) {
        Remove-Task
        if (Test-Path $Prefix) { Remove-Item -Recurse -Force $Prefix }
        Set-UserPath $Scripts $false
        Write-Host "Removed $Prefix. Your settings were kept."
        return
    }

    # Find Python 3.11+.
    $candidates = @()
    if ($env:RWR_PYTHON) { $candidates += , @($env:RWR_PYTHON) }  # one path; may contain spaces
    $candidates += , @("py", "-3")
    $candidates += , @("python")
    $Py = $null
    foreach ($c in $candidates) {
        try {
            $exe, $rest = $c
            & $exe @rest -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" 2>$null
            if ($LASTEXITCODE -eq 0) { $Py = $c; break }
        } catch { }
    }
    if (-not $Py) { throw "Python 3.11 or newer is needed. Install it from python.org, then run this again." }
    $PyExe, $PyArgs = $Py

    Write-Host "Installing radio-web-remote into $Prefix"
    if (-not (Test-Path (Join-Path $Scripts "python.exe"))) {
        & $PyExe @PyArgs -m venv (Join-Path $Prefix "venv")
        if ($LASTEXITCODE -ne 0) { throw "could not create the virtual environment" }
    }
    & (Join-Path $Scripts "python.exe") -m pip install --quiet --upgrade pip
    & (Join-Path $Scripts "python.exe") -m pip install --quiet --upgrade $Source
    if ($LASTEXITCODE -ne 0) { throw "pip could not install $Source" }
    Set-UserPath $Scripts $true
    $env:Path = "$Scripts;$env:Path"
    & $Exe --version

    if ($Service) {
        # Start at logon, as this user; restart if it stops. ExecutionTimeLimit 0
        # turns off Task Scheduler's default 3-day limit. A key sent over CI-V
        # survives the server dying (docs/hardware-test.md, H7300-4 C); the server
        # sends TX off first thing when it starts.
        Remove-Task
        $action = New-ScheduledTaskAction -Execute $Exe -Argument "serve" -WorkingDirectory $Prefix
        $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
        $settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
            -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
        Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
            -Description "radio-web-remote server" -RunLevel Limited -Force | Out-Null
        Start-ScheduledTask -TaskName $TaskName
        Write-Host "Scheduled task '$TaskName' starts the server at logon (Task Scheduler > Task Scheduler Library)."
    }

    Write-Host ""
    Write-Host "Next (open a new terminal so the PATH change applies):"
    Write-Host "  radio-web-remote init      # first time: admin account and certificates"
    Write-Host "  radio-web-remote doctor    # finds the radio and says what to fix"
    if (-not $Service) { Write-Host "  radio-web-remote serve     # or run this again with -Service" }
} $Service.IsPresent $Uninstall.IsPresent
