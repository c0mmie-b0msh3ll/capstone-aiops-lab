param([string]$Context='capstone-aiops-lab', [string]$Region='us-east-1', [string]$Helm='helm', [string]$Images='lab/environments/aws-lab.yaml')
$ErrorActionPreference='Stop'
$PSNativeCommandUseErrorActionPreference=$true
$labRoot=Split-Path $PSScriptRoot -Parent
Set-Location $labRoot
New-Item -ItemType Directory -Force .lab-state | Out-Null
$env:KUBECONFIG=Join-Path $labRoot '.lab-state/kubeconfig'
aws eks update-kubeconfig --region $Region --name $Context --alias $Context
foreach($ns in @('lab-app','lab-observe')) {
  $exists=kubectl --context $Context get namespace $ns --ignore-not-found -o name
  if(-not $exists) { kubectl --context $Context create namespace $ns }
}
kubectl --context $Context apply -f lab/k8s/storage.yaml
foreach($entry in @(@{ns='lab-app';name='lab-db'},@{ns='lab-observe';name='grafana-admin'})) {
  $exists=kubectl --context $Context -n $entry.ns get secret $entry.name --ignore-not-found -o name
  if(-not $exists) {
    $bytes=New-Object byte[] 24
    [System.Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
    $password=[Convert]::ToHexString($bytes).ToLower()
    $secret=@{apiVersion='v1';kind='Secret';metadata=@{name=$entry.name;namespace=$entry.ns};type='Opaque';stringData=@{password=$password}}
    $secret | ConvertTo-Json -Depth 5 | kubectl --context $Context apply -f -
    $password=$null; $secret=$null
  }
}
& $Helm upgrade --install lab-observe lab/charts/lab --kube-context $Context -n lab-observe -f lab/observe-values.yaml --wait --timeout 5m
& $Helm upgrade --install lab-app lab/charts/lab --kube-context $Context -n lab-app -f lab/app-values.yaml -f $Images --wait --timeout 5m
kubectl --context $Context apply -f lab/k8s/kube-state-metrics.yaml
kubectl --context $Context -n lab-app get pods
Write-Host "Ready for smoke check. Set KUBECONFIG=$env:KUBECONFIG"
Write-Host "kubectl --context $Context -n lab-app port-forward service/web 18081:8080"
