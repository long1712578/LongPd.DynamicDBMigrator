# CONTEXT.md

Domain glossary and architectural concepts for the Dynamic DB Migrator project. Critical for AI agent context alignment.

## Thuật ngữ Chuyên ngành (Domain Glossary)

| Thuật ngữ | Định nghĩa |
| :--- | :--- |
| **Source DB (MySQL)** | Cơ sở dữ liệu nguồn chứa các bảng và dữ liệu gốc cần di trú. |
| **Target DB (PostgreSQL)** | Cơ sở dữ liệu đích nhận dữ liệu được đồng bộ hóa. |
| **Schema Discovery** | Tiến trình tự động quét cấu trúc bảng, khóa chính, kiểu dữ liệu từ database thực tế hoặc từ file SQL dump. |
| **Table Mapping** | Quy tắc ánh xạ tên bảng nguồn (MySQL) sang tên bảng đích (PostgreSQL). |
| **Column Mapping** | Quy tắc ánh xạ các trường (cột) trong một bảng nguồn sang trường tương ứng ở bảng đích. |
| **Type Overrides** | Cấu hình ép kiểu thủ công cho các cột cụ thể khi kiểu mặc định tự nhận dạng không phù hợp (ví dụ: `varchar(36)` -> `uuid`). |
| **Value Transform** | Quy trình biến đổi giá trị của dữ liệu tế bào trong quá trình di chuyển dựa trên quy tắc cấu hình (ví dụ: chuyển đổi trạng thái null thành boolean, chuyển đổi định dạng ngày tháng). |

## Chiến lược Di trú (Migration Strategies)
Hỗ trợ 3 chế độ đồng bộ chính trong `DatabaseMigrator`:
1. **Truncate & Insert**: Xóa toàn bộ dữ liệu ở bảng đích (PostgreSQL) trước khi chèn toàn bộ dữ liệu mới từ nguồn.
2. **Upsert (Update/Insert)**: Cập nhật bản ghi nếu đã tồn tại khóa chính (Primary Key) ở PostgreSQL, nếu chưa có thì chèn mới.
3. **Append**: Chèn thêm dữ liệu mới vào PostgreSQL mà không xóa dữ liệu cũ, bỏ qua các bản ghi bị trùng khóa chính.

## Cấu trúc Cấu hình (`mapping_config.json`)
Hệ thống hoạt động dựa hoàn toàn vào file cấu hình. Cấu trúc chuẩn v2.0:
```json
{
  "version": "2.0",
  "target_schema": "public",
  "table_mapping": {
    "source_table": "target_table"
  },
  "column_mapping": {
    "source_table": {
      "source_col": "target_col"
    }
  },
  "type_overrides": {
    "source_table.source_col": "postgres_type"
  },
  "value_transforms": {
    "source_table.source_col -> target_table.target_col": {
      "type": "transform_name",
      "param": "value"
    }
  },
  "custom_rules": {
    "enum_mapping": {},
    "required_defaults": {},
    "ignored_source_columns": {}
  }
}
```
