param([Parameter(Mandatory=$true)][string]$RoleArn, [string]$SourceProfile='default', [string]$Region='us-east-1', [string]$Cluster='capstone-aiops-lab')
$ErrorActionPreference='Stop'
$PSNativeCommandUseErrorActionPreference=$true
$labRoot=Split-Path $PSScriptRoot -Parent
New-Item -ItemType Directory -Force (Join-Path $labRoot '.lab-state') | Out-Null
$env:KUBECONFIG=Join-Path $labRoot '.lab-state/ai-kubeconfig'
# AWS CLI obtains/refreshes EKS tokens using the source profile and role.
# No temporary access keys are written to this repository or printed.
aws eks update-kubeconfig --region $Region --name $Cluster --profile $SourceProfile --assume-role-arn $RoleArn --role-arn $RoleArn --alias lab-investigator
kubectl --context lab-investigator -n lab-app auth can-i get pods
kubectl --context lab-investigator -n lab-app auth can-i patch deployments
Write-Host "Expected: yes for read, no for patch. Kubeconfig: $env:KUBECONFIG"
Write-Host 'kubectl --context lab-investigator -n lab-observe port-forward service/prometheus 19090:9090'
Write-Host 'kubectl --context lab-investigator -n lab-observe port-forward service/jaeger 16686:16686'
