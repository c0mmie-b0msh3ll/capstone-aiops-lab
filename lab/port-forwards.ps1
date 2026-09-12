#requires -Version 7.0
param([ValidateSet('start','stop','run','status')][string]$Action='start')
$ErrorActionPreference='Stop'
$repoRoot=Split-Path $PSScriptRoot -Parent
$stateDir=Join-Path $repoRoot '.lab-state/port-forwards'
New-Item -ItemType Directory -Force -Path $stateDir | Out-Null
$pidFile=Join-Path $stateDir 'supervisor.pid'
$stopFile=Join-Path $stateDir 'stop.request'
$lockFile=Join-Path $stateDir 'supervisor.lock'
$scriptFile=$PSCommandPath
$targets=@(
    @{Name='web';Namespace='lab-app';Port=18081;Remote=8080},
    @{Name='grafana';Namespace='lab-observe';Port=13000;Remote=3000},
    @{Name='jaeger';Namespace='lab-observe';Port=16686;Remote=16686},
    @{Name='prometheus';Namespace='lab-observe';Port=19090;Remote=9090}
)
function Get-Supervisor {
    if (Test-Path -LiteralPath $pidFile) {
        $supervisorId=0
        if ([int]::TryParse((Get-Content -LiteralPath $pidFile -Raw).Trim(),[ref]$supervisorId)) {
            $p=Get-CimInstance Win32_Process -Filter "ProcessId=$supervisorId" -ErrorAction SilentlyContinue
            if ($p -and $p.CommandLine -like "*$scriptFile*" -and $p.CommandLine -like '*-Action run*') { return $p }
        }
    }
    return $null
}
if ($Action -eq 'stop') {
    if (Get-Supervisor) {
        Set-Content -LiteralPath $stopFile -Value 'stop'
        Write-Host 'Stop requested. Owned tunnels close within about 5 seconds.'
    } else { Write-Host 'Supervisor is not running.' }
    return
}
if ($Action -eq 'status') {
    $supervisor=Get-Supervisor
    Write-Host "Supervisor running: $([bool]$supervisor)"
    foreach ($t in $targets) {
        $listener=Get-NetTCPConnection -State Listen -LocalPort $t.Port -ErrorAction SilentlyContinue
        [pscustomobject]@{Service=$t.Name;URL="http://localhost:$($t.Port)";Listening=[bool]$listener}
    }
    return
}
if ($Action -eq 'start') {
    if (Get-Supervisor) { Write-Host 'Supervisor already running. Use -Action status to inspect.'; return }
    if (Test-Path -LiteralPath $stopFile) { Remove-Item -LiteralPath $stopFile }
    $pwsh=(Get-Process -Id $PID).Path
    Start-Process -FilePath $pwsh -ArgumentList @('-NoProfile','-File',('"'+$scriptFile+'"'),'-Action','run') -WindowStyle Hidden -RedirectStandardOutput (Join-Path $stateDir 'supervisor.out.log') -RedirectStandardError (Join-Path $stateDir 'supervisor.err.log') | Out-Null
    Write-Host "Starting background tunnels. Logs: $stateDir"
    Write-Host 'Use -Action status to check listeners; pages may take several seconds to connect.'
    return
}
# One supervisor owns its children; the lock prevents concurrent launch races.
try { $lock=[System.IO.File]::Open($lockFile,'OpenOrCreate','ReadWrite','None') }
catch { Write-Host 'Another supervisor owns the lock.'; return }
$children=@{}
try {
    Set-Content -LiteralPath $pidFile -Value $PID
    $env:KUBECONFIG=Join-Path $repoRoot '.lab-state/kubeconfig'
    if (-not (Test-Path -LiteralPath $env:KUBECONFIG)) { throw 'Operator kubeconfig missing.' }
    $kubectl=(Get-Command kubectl -ErrorAction Stop).Source
    while (-not (Test-Path -LiteralPath $stopFile)) {
        foreach ($t in $targets) {
            $child=$children[$t.Name]
            if ($child -and -not $child.HasExited) { continue }
            # Leave pre-existing listeners alone. Only children started here are stopped.
            if (Get-NetTCPConnection -State Listen -LocalPort $t.Port -ErrorAction SilentlyContinue) { continue }
            $stamp=Get-Date -Format 'yyyyMMdd-HHmmss-fff'
            $argsList=@('--context','capstone-aiops-lab','-n',$t.Namespace,'port-forward',('service/'+$t.Name),("$($t.Port):$($t.Remote)"),'--address','127.0.0.1','--pod-running-timeout=30s')
            $children[$t.Name]=Start-Process -FilePath $kubectl -ArgumentList $argsList -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $stateDir "$($t.Name)-$stamp.out.log") -RedirectStandardError (Join-Path $stateDir "$($t.Name)-$stamp.err.log")
            Write-Output "$(Get-Date -Format o) Started $($t.Name), PID $($children[$t.Name].Id)"
        }
        Start-Sleep -Seconds 5
    }
} finally {
    foreach ($child in $children.Values) {
        if (-not $child.HasExited) { $child.Kill(); $child.WaitForExit() }
        $child.Dispose()
    }
    if (Test-Path -LiteralPath $pidFile) { Remove-Item -LiteralPath $pidFile }
    if (Test-Path -LiteralPath $stopFile) { Remove-Item -LiteralPath $stopFile }
    $lock.Dispose()
}
