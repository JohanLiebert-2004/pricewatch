# Local Big W refresh — Big W's bot protection blocks datacenter IPs (GitHub
# Actions) but allows polite residential traffic, so this lane runs from a
# home machine on a schedule instead. Reads DATABASE_URL from .env (gitignored).
# Register with Task Scheduler; see README/DEPLOY notes.
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot

$envLine = Get-Content "$PSScriptRoot\.env" | Where-Object { $_ -match '^DATABASE_URL=' } | Select-Object -First 1
if (-not $envLine) { Write-Error "No DATABASE_URL in .env"; exit 1 }
$env:DATABASE_URL = $envLine -replace '^DATABASE_URL=', ''

$log = "$PSScriptRoot\local_refresh.log"
"=== $(Get-Date -Format o) bigw local refresh ===" | Set-Content $log
python run.py refresh bigw --budget 700 *>> $log
python run.py crawl bigw --batch 20 *>> $log
python run.py detect *>> $log
"=== $(Get-Date -Format o) done ===" | Add-Content $log
