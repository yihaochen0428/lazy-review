$ErrorHandling = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "首次运行：正在创建虚拟环境..." -ForegroundColor Yellow
    python -m venv .venv
    & ".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
}

Write-Host ""
Write-Host "  懒得复盘 已启动" -ForegroundColor Green
Write-Host "  浏览器访问：http://127.0.0.1:8000" -ForegroundColor Cyan
Write-Host "  按 Ctrl+C 停止服务" -ForegroundColor DarkGray
Write-Host ""

& ".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
