param(
    [ValidateSet('small', 'big')]
    [string]$Mode = 'small',
    [switch]$TriggerRun,
    [int]$Port = 8000
)

$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$pythonExe = 'C:\Users\y545\.conda\envs\aiagency\python.exe'
$baseUrl = "http://127.0.0.1:$Port"

Write-Host '[1/6] Stopping old Python processes...'
Get-Process -Name python -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue

Write-Host '[2/6] Freeing backend port if needed...'
Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique |
    ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }

Write-Host '[3/6] Cleaning stuck running records...'
& $pythonExe (Join-Path $repoRoot 'scripts\cleanup_stuck.py') | Out-Null

Write-Host '[4/6] Starting backend in background...'
$uvicornArgs = @('-m', 'uvicorn', 'orchestrator:app', '--host', '127.0.0.1', '--port', "$Port")
$backendProc = Start-Process -FilePath $pythonExe -ArgumentList $uvicornArgs -WorkingDirectory $repoRoot -PassThru

Write-Host '[5/6] Waiting for health check...'
$healthy = $false
for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Milliseconds 750
    try {
        $health = Invoke-RestMethod "$baseUrl/api/health" -Method Get
        if ($health.status -eq 'ok') {
            $healthy = $true
            break
        }
    }
    catch {
        # keep waiting
    }
}

if (-not $healthy) {
    Write-Error "Backend did not become healthy on $baseUrl"
}

Write-Host '[6/6] Setting mode and optionally triggering run...'
$setModeBody = @{ mode = $Mode } | ConvertTo-Json
$setMode = Invoke-RestMethod "$baseUrl/api/pipeline/project-size" -Method Post -ContentType 'application/json' -Body $setModeBody
Write-Host "Mode: $($setMode.mode)"

if ($TriggerRun) {
    $trigger = Invoke-RestMethod "$baseUrl/api/pipeline/trigger" -Method Post
    Write-Host "Run started with id: $($trigger.run_id)"
}

Write-Host "Backend PID: $($backendProc.Id)"
Write-Host 'Single-process setup complete.'
