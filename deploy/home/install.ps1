<#
    install.ps1 - one-time (and idempotent) setup of stroj's frontend on the
    home server. Run in an elevated PowerShell on the server:

        powershell -ExecutionPolicy Bypass -File install.ps1

    It can be run from a checkout anywhere; with no checkout yet, download this
    file and run it - it clones the repo itself.

    What it does:
      1. finds Caddy (from the running caddy.exe) and Git and records them in
         <Root>\server.json
      2. clones the repo into <Root>\repo (or leaves an existing clone alone)
      3. copies tick.ps1 to <Root>\bin (the per-minute deploy poller)
      4. adds an import of <Root>\Caddyfile to the main Caddyfile
      5. registers the scheduled task "stroj-deploy" (SYSTEM, every minute)
      6. runs the first deploy

    Re-run it after changing tick.ps1 or common.ps1's settings.
#>
[CmdletBinding()]
param(
    [string]$Root = 'C:\Users\ethan\stroj',
    # Override if Caddy is not running while you install.
    [string]$MainCaddyfile,
    [string]$CaddyExe
)

$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { throw 'Run this from an elevated (Administrator) PowerShell.' }

function Step($t) { Write-Host ''; Write-Host "==> $t" -ForegroundColor Cyan }
function Info($m) { Write-Host "    $m" }

# ---- 1. locate tools -------------------------------------------------------
Step 'Locating Caddy and Git'
$caddyProc = Get-CimInstance Win32_Process -Filter "Name='caddy.exe'" | Select-Object -First 1
if (-not $MainCaddyfile) {
    if ($caddyProc -and $caddyProc.CommandLine -match '--config\s+"?([^"]+?)"?(\s|$)') { $MainCaddyfile = $Matches[1] }
    else { throw 'Caddy is not running; pass -MainCaddyfile <path to the Caddyfile Caddy runs with>.' }
}
if (-not $CaddyExe) {
    if ($caddyProc -and $caddyProc.ExecutablePath) { $CaddyExe = $caddyProc.ExecutablePath }
    else { $CaddyExe = (Get-Command caddy -ErrorAction Stop).Source }
}
# Prefer the stable WinGet link over a versioned package path.
$wingetLink = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Links\caddy.exe'
if (($CaddyExe -match 'WinGet\\Packages') -and (Test-Path $wingetLink)) { $CaddyExe = $wingetLink }
$git = (Get-Command git -ErrorAction Stop).Source
if ($git -match '\\mingw64\\bin\\git.exe$') { $git = $git -replace '\\mingw64\\bin\\git.exe$', '\cmd\git.exe' }
Info "caddy:          $CaddyExe"
Info "main Caddyfile: $MainCaddyfile"
Info "git:            $git"

# ---- 2. layout and clone ---------------------------------------------------
Step "Preparing $Root"
foreach ($d in 'releases', 'logs', 'bin') { New-Item -ItemType Directory -Force -Path (Join-Path $Root $d) | Out-Null }
$repo = Join-Path $Root 'repo'
if (-not (Test-Path (Join-Path $repo '.git'))) {
    Info 'cloning repository'
    & $git clone --quiet https://github.com/OoEthanoO/stroj-v2.git $repo
    if ($LASTEXITCODE -ne 0) { throw 'git clone failed' }
} else {
    Info 'repository already cloned'
}
$here = if (Test-Path (Join-Path $PSScriptRoot 'common.ps1')) { $PSScriptRoot } else { Join-Path $repo 'deploy\home' }
. (Join-Path $here 'common.ps1')
$paths = Get-Paths $Root

$config = [pscustomobject]@{
    caddy         = $CaddyExe
    mainCaddyfile = $MainCaddyfile
    git           = $git
    branch        = $script:Branch
    domains       = $script:Domains
}
[IO.File]::WriteAllText($paths.Server, ($config | ConvertTo-Json), (New-Object Text.UTF8Encoding $false))
Info "wrote $($paths.Server)"

# ---- 3. poller ---------------------------------------------------------------
Step 'Installing the deploy poller'
Copy-Item (Join-Path $here 'tick.ps1') (Join-Path $paths.Bin 'tick.ps1') -Force
Info "copied tick.ps1 to $($paths.Bin)"

# ---- 4. Caddy import ---------------------------------------------------------
# The site file must exist before the main Caddyfile imports it, or the next
# reload of the shared config - for any site - fails.
Step 'Connecting Caddy'
if (-not (Test-Path $paths.SiteCaddy)) { Copy-Item (Join-Path $repo 'deploy\home\Caddyfile') $paths.SiteCaddy }
# Every site on this server shares one Caddy config, so a broken import here
# would make the next reload fail for all of them. Check the block on its own,
# then the merged config, and take the import back out if that fails.
$out = & $CaddyExe validate --config $paths.SiteCaddy --adapter caddyfile 2>&1
if ($LASTEXITCODE -ne 0) { throw "the site block does not validate on its own: $(($out | Select-Object -Last 3) -join ' | ')" }
$before = [IO.File]::ReadAllText($MainCaddyfile)
if (Confirm-CaddyImport $config $paths) {
    $out = & $CaddyExe validate --config $MainCaddyfile --adapter caddyfile 2>&1
    if ($LASTEXITCODE -ne 0) {
        [IO.File]::WriteAllText($MainCaddyfile, $before, (New-Object Text.UTF8Encoding $false))
        throw "the merged Caddy config does not validate, so the import was taken back out: $(($out | Select-Object -Last 3) -join ' | ')"
    }
    Info "added import block to $MainCaddyfile (backup saved next to it)"
}
else { Info 'import block already present' }

# ---- 5. scheduled task -------------------------------------------------------
Step "Registering scheduled task '$($script:TaskName)'"
$tick = Join-Path $paths.Bin 'tick.ps1'
$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$tick`" -Root `"$Root`"" `
    -WorkingDirectory $Root
$every = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1)
$boot = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $script:TaskName -Action $action -Trigger @($every, $boot) -Principal $principal `
    -Settings $settings -Description "Deploys stroj's frontend (github.com/OoEthanoO/stroj-v2) when main changes." -Force | Out-Null
$registered = Get-ScheduledTask -TaskName $script:TaskName
Info "state: $($registered.State); repeats every $($registered.Triggers[0].Repetition.Interval)"

# ---- 6. first deploy ---------------------------------------------------------
# The deploy validates the whole Caddy config before reloading, and rolls the
# site block back if Caddy rejects it.
Step 'Deploying the current main branch'
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $tick -Root $Root -Force
$state = Read-State $paths
Info "status: $($state.status); live commit: $($state.deployed)"
if ($state.status -ne 'ok') { Write-Host "    see $($paths.Log)" -ForegroundColor Yellow }

Write-Host ''
Write-Host "Installed. Every push to main is live about a minute later:" -ForegroundColor Green
foreach ($d in $script:Domains) { Write-Host "    https://$d" }
Write-Host "Status:   powershell -ExecutionPolicy Bypass -File `"$repo\deploy\home\status.ps1`""
