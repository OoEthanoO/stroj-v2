<#
    deploy.ps1 - lay out one commit's frontend and make it live. Called by
    tick.ps1 after it has checked out the commit in <Root>\repo; can also be
    run by hand:

        powershell -ExecutionPolicy Bypass -File C:\Users\ethan\stroj\repo\deploy\home\deploy.ps1 -Sha <commit>

    The same layout scripts/build-static.sh produced for Vercel: index.html
    stamped with the commit, the three static files under static\, and a
    version.json the page compares with the judge's own commit.

    The live site only changes at the junction switch, so a failure at any
    earlier step leaves the previous release serving.
#>
[CmdletBinding()]
param(
    [string]$Root = 'C:\Users\ethan\stroj',
    [Parameter(Mandatory = $true)][string]$Sha
)

$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
. (Join-Path $PSScriptRoot 'common.ps1')

$paths = Get-Paths $Root
$config = Read-ServerConfig $paths
$short = $Sha.Substring(0, 7)
$started = Get-Date
$utf8 = New-Object Text.UTF8Encoding $false

function Step([string]$Message) { Write-Log $paths "deploy $short" $Message }
function Fail([string]$Message) {
    Step "FAILED: $Message"
    Save-State $paths @{ status = 'failed'; error = $Message; finishedAt = (Get-Date).ToString('o') }
    exit 1
}

try {
    # 1. Lay the release out next to its final name, then move it into place,
    #    so a half-copied release is never what `current` points at.
    New-Item -ItemType Directory -Force -Path $paths.Releases | Out-Null
    $release = Join-Path $paths.Releases $Sha
    if (Test-Path $release) {
        Step "releases\$short already built"
    } else {
        $web = Join-Path $paths.Repo 'stroj\web'
        $partial = "$release.partial"
        if (Test-Path $partial) { Remove-Item $partial -Recurse -Force }
        New-Item -ItemType Directory -Force -Path (Join-Path $partial 'static') | Out-Null

        $index = [IO.File]::ReadAllText((Join-Path $web 'index.html'), $utf8)
        if ($index -notmatch '__STROJ_COMMIT__') { Fail 'index.html has no __STROJ_COMMIT__ placeholder' }
        [IO.File]::WriteAllText((Join-Path $partial 'index.html'), $index.Replace('__STROJ_COMMIT__', $Sha), $utf8)
        foreach ($name in 'style.css', 'latex.js', 'app.js') {
            Copy-Item (Join-Path $web $name) (Join-Path $partial "static\$name") -ErrorAction Stop
        }
        $builtAt = (Get-Date).ToUniversalTime().ToString("yyyy-MM-dd'T'HH:mm:ss'Z'", [Globalization.CultureInfo]::InvariantCulture)
        $version = '{{"commit":"{0}","short":"{1}","built_at":"{2}"}}' -f $Sha, $short, $builtAt
        [IO.File]::WriteAllText((Join-Path $partial 'version.json'), $version + "`n", $utf8)
        [IO.Directory]::Move($partial, $release)
        Step "release laid out in releases\$short"
    }

    # 2. Caddy site block: apply repo changes only if Caddy accepts them.
    $candidate = Join-Path $paths.Repo 'deploy\home\Caddyfile'
    $caddyError = $null
    $changed = (-not (Test-Path $paths.SiteCaddy)) -or ((Get-FileHash $candidate).Hash -ne (Get-FileHash $paths.SiteCaddy).Hash)
    $imported = Confirm-CaddyImport $config $paths
    if ($changed -or $imported) {
        $backup = "$($paths.SiteCaddy).previous"
        if (Test-Path $paths.SiteCaddy) { Copy-Item $paths.SiteCaddy $backup -Force }
        Copy-Item $candidate $paths.SiteCaddy -Force
        $caddyError = Update-Caddy $config
        if ($caddyError) {
            Step "Caddy rejected deploy\home\Caddyfile - keeping the previous site config. $caddyError"
            if (Test-Path $backup) {
                Copy-Item $backup $paths.SiteCaddy -Force
                Update-Caddy $config | Out-Null
            }
            Fail 'Caddy configuration was rejected; release was not switched.'
        }
        Step 'Caddy config updated and reloaded'
    }

    # 3. Go live.
    $previous = Get-CurrentRelease $paths
    Set-CurrentRelease $paths $release
    Step "live: current -> releases\$short"

    # 4. Verify through Caddy itself, on the loopback copy of the site: the
    #    page, and the judge behind it.
    $served = Get-ServedJson "$($script:LocalCheck)/version.json"
    if (-not $served -or $served.commit -ne $Sha) {
        if ($previous) { Set-CurrentRelease $paths $previous }
        Fail "Caddy serves $(if ($served) { $served.commit } else { 'nothing' }) at /version.json - rolled back"
    }
    $judge = Get-ServedJson "$($script:LocalCheck)/api/version"
    $judgeNote = if ($judge -and $judge.commit) { "judge on $($judge.short)" } else { 'judge NOT reachable through the proxy' }
    Step "served through Caddy: $judgeNote"

    # 5. Each public name over HTTPS, asking this Caddy directly (bypassing
    #    DNS). Fails until DNS points here and the certificate is issued.
    $https = @()
    foreach ($domain in $script:Domains) {
        $seen = Get-ServedJson "https://$domain/version.json" @('--resolve', "${domain}:443:127.0.0.1")
        $https += if ($seen -and $seen.commit -eq $Sha) { "$domain ok" } else { "$domain no certificate yet" }
    }
    Step "https: $($https -join '; ')"

    # 6. Keep the newest releases (and whatever is live).
    Get-ChildItem $paths.Releases -Directory |
        Where-Object { $_.Name -notlike '*.partial' } |
        Sort-Object LastWriteTime -Descending |
        Select-Object -Skip $script:KeepReleases |
        Where-Object { $_.FullName -ne $release } |
        ForEach-Object { Remove-Item $_.FullName -Recurse -Force; Step "pruned releases\$($_.Name.Substring(0, 7))" }

    $seconds = [int]((Get-Date) - $started).TotalSeconds
    Save-State $paths @{
        status     = 'ok'
        deployed   = $Sha
        deployedAt = (Get-Date).ToString('o')
        finishedAt = (Get-Date).ToString('o')
        error      = $null
        judge      = $judgeNote
        https      = ($https -join '; ')
        seconds    = $seconds
    }
    Step "done in ${seconds}s"
}
catch {
    Fail $_.Exception.Message
}
