# Install on Windows 10 or 11

Needs Python 3.11 or newer from python.org (tick "Add python.exe to PATH" or
use the `py` launcher) and the radio on USB. No admin rights needed for
radio-web-remote itself.

## 1. Icom's USB driver (required)

The radios need Icom's USB driver, from Icom's site:

| Radio | Driver | Page |
|-------|--------|------|
| IC-7300, IC-9700 | USB Driver Version 1.30 (2018/06/07) | [icomjapan.com/support/firmware_driver/1974](https://www.icomjapan.com/support/firmware_driver/1974/) |
| IC-705 | USB Driver Version 1.80 (2026/08/26), Windows 11 except Windows on ARM | [icomjapan.com/support/firmware_driver/4685](https://www.icomjapan.com/support/firmware_driver/4685/) |

(Read 2026-11-13.) Icom asks you to accept its download terms and to read
its installation guide first; the guides are on the same site
([1.30](https://www.icomjapan.com/support/manual/1768/),
[1.80](https://www.icomjapan.com/support/manual/3193/)). Install the driver
**before** connecting the radio.

Then confirm the port: run `radio-web-remote doctor` (step 3), which lists
every COM port with the radio's name. In Device Manager the radio's ports
appear under "Ports (COM & LPT)". TODO(verify): the exact names Icom's driver
gives them (the IC-705 guide calls port A "IC-705 Serial Port A (CI-V)").
The IC-705 and IC-9700 each add two ports; port A carries CI-V. COM numbers
change when you move the cable, which is why radio-web-remote matches the
radio by USB serial number.

## 2. Install

In PowerShell:

```powershell
irm https://raw.githubusercontent.com/kidvelvet/radio-web-remote/main/packaging/install.ps1 | iex
```

This installs into `%LOCALAPPDATA%\Programs\radio-web-remote` and adds it to
your PATH. Open a new terminal afterwards. Run it again to upgrade.

## 3. First run

```powershell
radio-web-remote init
```

```powershell
radio-web-remote doctor
```

`doctor` finds the radio and prints a `radios:` entry for
`%LOCALAPPDATA%\radio-web-remote\config.yaml`. Set up the radio's menus first:
[radio-setup.md](radio-setup.md). On Windows the codec shows as a generic
"USB Audio CODEC"; with more than one radio, set each radio's audio devices
by index (`radio-web-remote audio devices`). An index is a plain number, such
as `input_device: 4`; a number in quotes is read as an index too, never as
part of a name. (On macOS and Linux, `auto` tells radios apart by USB hub;
on Windows it cannot yet.)

## 4. Start at logon (optional)

Download `install.ps1` and run:

```powershell
.\install.ps1 -Service
```

This registers a Task Scheduler task, `radio-web-remote`, that starts the
server when you log on and restarts it if it stops
([Register-ScheduledTask](https://learn.microsoft.com/en-us/powershell/module/scheduledtasks/register-scheduledtask),
[New-ScheduledTaskTrigger -AtLogOn](https://learn.microsoft.com/en-us/powershell/module/scheduledtasks/new-scheduledtasktrigger)).
If Windows Firewall asks whether to let Python accept connections, allow it
on private networks, or other devices cannot reach the page.

## Uninstall

```powershell
.\install.ps1 -Uninstall
```

Settings stay in `%LOCALAPPDATA%\radio-web-remote`.
