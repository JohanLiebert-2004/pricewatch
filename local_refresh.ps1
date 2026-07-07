# Local Big W refresh — Big W's bot protection blocks datacenter IPs (GitHub
# Actions) but allows polite residential traffic, so this lane runs from a
# home machine on a schedule instead. Reads DATABASE_URL from .env (gitignored).
# Register with Task Scheduler; see README/DEPLOY notes.
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot

# load every KEY=VALUE from .env (DATABASE_URL, TELEGRAM_* alert config, ...)
Get-Content "$PSScriptRoot\.env" | Where-Object { $_ -match '^[A-Z_]+=' } | ForEach-Object {
    $k, $v = $_ -split '=', 2
    Set-Item -Path "env:$k" -Value $v
}
if (-not $env:DATABASE_URL) { Write-Error "No DATABASE_URL in .env"; exit 1 }

$log = "$PSScriptRoot\local_refresh.log"
"=== $(Get-Date -Format o) bigw local refresh ===" | Out-File $log -Encoding utf8
python run.py refresh bigw --budget 700 2>&1 | Out-File $log -Append -Encoding utf8
python run.py crawl bigw --batch 20 2>&1 | Out-File $log -Append -Encoding utf8
python run.py detect 2>&1 | Out-File $log -Append -Encoding utf8
"=== $(Get-Date -Format o) done ===" | Out-File $log -Append -Encoding utf8
