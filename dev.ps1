param (
    [Parameter(Position=0)]
    [ValidateSet("test", "lint", "format", "security", "all")]
    [string]$action = "test"
)

switch ($action) {
    "test" {
        Write-Host "🚀 Chạy bộ kiểm thử (pytest)..." -ForegroundColor Cyan
        python -m pytest
    }
    "lint" {
        Write-Host "🔍 Kiểm tra chất lượng mã nguồn (ruff + bandit)..." -ForegroundColor Cyan
        python -m ruff check .
        python -m bandit -r db_migrator web
    }
    "format" {
        Write-Host "🧹 Định dạng lại mã nguồn (ruff format)..." -ForegroundColor Cyan
        python -m ruff format .
    }
    "security" {
        Write-Host "🛡️ Kiểm tra lỗ hổng bảo mật (bandit)..." -ForegroundColor Cyan
        python -m bandit -r db_migrator web -ll
    }
    "all" {
        Write-Host "=== Chạy toàn bộ quy trình kiểm tra ===" -ForegroundColor Yellow
        Write-Host "1. Kiểm tra chất lượng mã nguồn (ruff)..." -ForegroundColor Cyan
        python -m ruff check .
        Write-Host "2. Quét lỗ hổng bảo mật (bandit)..." -ForegroundColor Cyan
        python -m bandit -r db_migrator web -ll
        Write-Host "3. Chạy unit tests (pytest)..." -ForegroundColor Cyan
        python -m pytest
    }
}
