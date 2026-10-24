# Start the GeneMatch demo on Windows: relay :8000, wallets :8101 (partner A) and
# :8102 (partner B), lab :8200, trial verifier :8300.
# Usage (from the repo root):  .\scripts\run_demo.ps1 [-Couple 1|2|3] [-Empty alex,jordan]
#   -Empty starts those wallets with no results (re-seeds every wallet).
# macOS/Linux: use scripts/run_demo.sh. Press Enter (or Ctrl-C) to stop everything.
param(
    [ValidateSet(1, 2, 3)][int]$Couple = $(if ($env:COUPLE) { [int]$env:COUPLE } else { 1 }),
    [string[]]$Empty = @()
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$env:PYTHONPATH = "."
$env:PYTHONIOENCODING = "utf-8"

$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }

$pair = @{ 1 = @("alex", "sam"); 2 = @("jordan", "riley"); 3 = @("casey", "morgan") }[$Couple]
$a, $b = $pair[0], $pair[1]

$seedArgs = @("scripts\seed_demo.py")
foreach ($p in $Empty) { $seedArgs += @("--empty", $p) }
if ($Empty.Count -gt 0 -or -not (Test-Path "var\wallets\alex.json") -or -not (Test-Path "var\accounts.json") -or -not (Test-Path "var\lab_accounts.json")) {
    & $py @seedArgs
}

function Start-Service([string]$label, [string[]]$pyArgs, [string]$person = "") {
    $env:GENEMATCH_PERSON = $person
    $p = Start-Process -FilePath $py -ArgumentList $pyArgs -NoNewWindow -PassThru
    Write-Host "started $label (pid $($p.Id))"
    return $p
}

$procs = @(
    (Start-Service "lab :8200"   @("-m", "uvicorn", "lab.service:app", "--port", "8200")),
    (Start-Service "relay :8000" @("-m", "uvicorn", "relay.service:app", "--port", "8000")),
    (Start-Service "trial :8300" @("-m", "uvicorn", "trial.service:app", "--port", "8300")),
    (Start-Service "$a :8101" @("-m", "uvicorn", "--factory", "wallet.service:create_app", "--port", "8101") $a),
    (Start-Service "$b :8102" @("-m", "uvicorn", "--factory", "wallet.service:create_app", "--port", "8102") $b)
)
Remove-Item Env:GENEMATCH_PERSON -ErrorAction SilentlyContinue

Write-Host "Couple ${Couple}: $a on http://localhost:8101, $b on http://localhost:8102"
Write-Host "Trial verifier on http://localhost:8300"
try {
    Read-Host "Press Enter to stop"
} finally {
    $procs | ForEach-Object { Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue }
    Write-Host "stopped"
}
