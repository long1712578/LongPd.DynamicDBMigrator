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
        $env:PYTHONIOENCODING = "utf-8"
        python -m bandit -r db_migrator web -f json | python -c "import sys,json; data=json.load(sys.stdin); errors=[i for i in data.get('results',[]) if i['issue_severity'] in ('HIGH','MEDIUM')]; print(f'Bandit: {len(errors)} issues found'); [print(f\"  {i['filename']}:{i['line_number']} {i['test_id']} {i['issue_text']}\") for i in errors]; sys.exit(1 if errors else 0)"
    }
    "format" {
        Write-Host "🧹 Định dạng lại mã nguồn (ruff format)..." -ForegroundColor Cyan
        python -m ruff format .
    }
    "security" {
        Write-Host "🛡️ Kiểm tra lỗ hổng bảo mật (bandit)..." -ForegroundColor Cyan
        $env:PYTHONIOENCODING = "utf-8"
        python -m bandit -r db_migrator web -f json | python -c "import sys,json; data=json.load(sys.stdin); errors=[i for i in data.get('results',[]) if i['issue_severity'] in ('HIGH','MEDIUM')]; print(f'Bandit: {len(errors)} security issues'); [print(f\"  {i['filename']}:{i['line_number']} [{i['issue_severity']}] {i['test_id']} {i['issue_text']}\") for i in errors]; sys.exit(1 if errors else 0)"
    }
    "all" {
        Write-Host "=== Chạy toàn bộ quy trình kiểm tra ===" -ForegroundColor Yellow
        Write-Host "1. Kiểm tra chất lượng mã nguồn (ruff)..." -ForegroundColor Cyan
        python -m ruff check .
        Write-Host "2. Quét lỗ hổng bảo mật (bandit)..." -ForegroundColor Cyan
        $env:PYTHONIOENCODING = "utf-8"
        python -m bandit -r db_migrator web -f json | python -c "import sys,json; data=json.load(sys.stdin); errors=[i for i in data.get('results',[]) if i['issue_severity'] in ('HIGH','MEDIUM')]; print(f'Bandit: {len(errors)} issues found'); [print(f\"  {i['filename']}:{i['line_number']} [{i['issue_severity']}] {i['test_id']} {i['issue_text']}\") for i in errors]; sys.exit(1 if errors else 0)"
        Write-Host "3. Chạy unit tests (pytest)..." -ForegroundColor Cyan
        python -m pytest
    }
}
