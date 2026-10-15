# Chạy AI Service đã nối sẵn về backend.  Dùng:  .\start_ai.ps1
# Phải trùng LAB_SERVICE_TOKEN của backend (mặc định dev-service-token) và backend phải đang chạy ở cổng 8080.
param(
    [string]$Backend = "http://localhost:8080",
    [string]$Token = "dev-service-token",
    [string]$Camera = "0"
)

$env:AI_BACKEND_EVENT_URL = "$Backend/api/v1/access/verify"
$env:AI_SERVICE_TOKEN = $Token
$env:AI_CAMERA = $Camera

Write-Host "Backend : $env:AI_BACKEND_EVENT_URL"
Write-Host "Camera  : $Camera"
Set-Location $PSScriptRoot
python main.py
