# SKILL: Security Review and Cyber Hardening

## Mô tả
Quy chuẩn thắt chặt an ninh mạng (Cyber Security), chống lại các lỗ hổng mã nguồn phổ biến trong quá trình xử lý database và cấu hình hệ thống.

## Các Quy tắc vàng
1. **Chống SQL Injection**:
   - Tuyệt đối KHÔNG ghép chuỗi (`f-string`, `%`, `+`) để sinh câu lệnh SQL động với dữ liệu từ người dùng.
   - Sử dụng Parameterized Queries (Truy vấn tham số hóa) của thư viện kết nối (MySQL: `%s`, PostgreSQL: `%s` hoặc `psycopg2.sql`).
   - Mọi định danh tên bảng hoặc tên cột do người dùng cấu hình phải đi qua hàm sanitize để whitelist chỉ chứa ký tự an toàn `[a-zA-Z0-9_-]`.

2. **Quản lý thông tin nhạy cảm (Credentials)**:
   - Tuyệt đối KHÔNG lưu mật khẩu, token, khóa API dưới dạng văn bản thuần (plain text) trong các file cấu hình Git hoặc database.
   - Sử dụng `CredentialVault` để mã hóa đối xứng Fernet các cấu hình lưu dưới dạng file.
   - Ưu tiên đọc từ biến môi trường (Environment variables) hoặc file `.env`.

3. **Input Validation (Kiểm thực đầu vào)**:
   - Kiểm tra định dạng đầu vào của tất cả API endpoint trong Flask (sử dụng middleware validate dữ liệu JSON).
   - Kiểm tra giới hạn kích thước file và ngăn chặn lỗ hổng Path Traversal khi người dùng chỉ định đường dẫn file SQL để parse.

4. **Nhật ký hệ thống (Audit Trail)**:
   - Ghi lại các hoạt động di trú dữ liệu nhạy cảm bao gồm: Ai thực hiện, lúc nào, thực hiện trên bảng nào, kết quả ra sao (thành công/lỗi).
   - Đảm bảo ghi log bảo mật dạng JSON Lines an toàn, không chứa thông tin nhạy cảm như mật khẩu hay API key.
