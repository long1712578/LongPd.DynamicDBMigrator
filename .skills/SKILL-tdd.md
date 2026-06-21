# SKILL: Test-Driven Development (TDD) Workflow

## Mô tả
Quy trình xây dựng tính năng dựa trên việc viết bài kiểm thử (test) trước khi viết logic thực thi mã nguồn.

## Quy trình
1. **Red**: Viết unit test cho tính năng hoặc lớp chuẩn bị thêm mới. Chạy thử test và đảm bảo test bị thất bại (do chưa có code thực thi).
2. **Green**: Viết mã nguồn tối giản nhất có thể để vượt qua bài test (chạy test pass).
3. **Refactor**: Tối ưu hóa cấu trúc code, loại bỏ mã lặp, đặt lại tên biến/hàm tường minh, nhưng vẫn giữ test pass.
4. **Verify**: Chạy lại toàn bộ test suite và đo lường độ bao phủ (Coverage) để đảm bảo không lỗi hồi quy.

## Hướng dẫn cụ thể trong Dự án
- Chạy unit test: `make test` hoặc `pytest`.
- Đo lường Coverage: Đảm bảo tỷ lệ bao phủ của code thay đổi đạt tối thiểu 80%.
- Sử dụng pytest fixtures được cấu hình sẵn trong [conftest.py](file:///d:/Projects/MY-Dragon-agent/LongPd.DynamicDBMigrator/tests/conftest.py).
- Sử dụng mocking (`unittest.mock`) để kiểm tra các tương tác với Database thực tế hoặc các API của Google Gemini.
