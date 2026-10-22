# Chạy AI Service đã nối sẵn về backend.  Dùng:  .\start_ai.ps1
# Phải trùng LAB_SERVICE_TOKEN của backend (mặc định dev-service-token) và backend phải đang chạy ở cổng 8080.
# Gửi thêm kết quả lên ThingsBoard (tùy chọn):  .\start_ai.ps1 -ThingsBoard "http://127.0.0.1:9080/api/v1/<TOKEN>/telemetry"
param(
    # 127.0.0.1 chứ không phải localhost: Windows thử IPv6 trước, mỗi lần gửi chậm thêm ~2 giây khi backend chỉ nghe IPv4
    [string]$Backend = "http://127.0.0.1:8080",
    [string]$Token = "dev-service-token",
    [string]$Camera = "0",
    [string]$ThingsBoard = ""
)

$env:AI_BACKEND_EVENT_URL = "$Backend/api/v1/access/verify"
$env:AI_SERVICE_TOKEN = $Token
$env:AI_CAMERA = $Camera
if ($ThingsBoard) { $env:AI_THINGSBOARD_URL = $ThingsBoard }

Write-Host "Backend : $env:AI_BACKEND_EVENT_URL"
Write-Host "Camera  : $Camera"
if ($env:AI_THINGSBOARD_URL) { Write-Host ("ThingsBoard: " + ($env:AI_THINGSBOARD_URL -replace '/api/v1/[^/]+/telemetry', '/api/v1/***/telemetry')) }
Set-Location $PSScriptRoot
python main.py
