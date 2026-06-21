# SKILL: Database Migration Debugging

## Mô tả
Hướng dẫn Agent cách khoanh vùng và phân tích lỗi xảy ra trong quá trình di chuyển (migration) dữ liệu giữa MySQL và PostgreSQL.

## Các lỗi thường gặp và phương pháp giải quyết
1. **Lỗi Kiểu dữ liệu không tương thích (Type Mismatches)**:
   - *Triệu chứng*: Database đích (Postgres) báo lỗi không thể cast kiểu dữ liệu.
   - *Cách xử lý*: Sử dụng `TypeMapper` để chỉ định override kiểu cột cụ thể trong phần `type_overrides` của `mapping_config.json`.
   
2. **Lỗi Mã hóa ký tự (Encoding & Mojibake)**:
   - *Triệu chứng*: Dữ liệu tiếng Việt bị lỗi font hiển thị (vd: `Nguy?n Văn A`).
   - *Cách xử lý*: Kiểm tra mã hóa file nguồn (luôn dùng UTF-8), cấu hình encoding cho kết nối database (MySQL: `utf8mb4`, Postgres: `UTF8`).
   
3. **Lỗi Dữ liệu vượt quá giới hạn (Data Truncation)**:
   - *Triệu chứng*: Postgres báo lỗi `value too long for type character varying(N)`.
   - *Cách xử lý*: Cấu hình `string_escape` transform rule kèm `max_length` để cắt chuỗi hoặc sửa schema Postgres đích để nâng giới hạn.

4. **Lỗi Khóa ngoại hoặc Quan hệ (Constraint Violations)**:
   - *Triệu chứng*: Lỗi vi phạm khóa ngoại (foreign key constraint) hoặc khóa chính trùng lặp.
   - *Cách xử lý*: Kiểm tra thứ tự migration giữa các bảng (migrate bảng cha trước, bảng con sau) hoặc thay đổi chiến lược di trú (`upsert`, `truncate_insert`).
