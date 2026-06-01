"# LongPd.DynamicDBMigrator" 
# 📚 HƯỚNG DẪN CHUYỂN ĐỔI DỮ LIỆU MySQL → PostgreSQL (BẢN DYNAMIC)

## 📋 Mục lục
1. [Tổng quan](#1-tổng-quan)
2. [Cài đặt và Yêu cầu hệ thống](#2-cài-đặt-và-yêu-cầu-hệ-thống)
3. [Cấu trúc thư mục](#3-cấu-trúc-thư-mục)
4. [Sử dụng Web Interface (Khuyến nghị)](#4-sử-dụng-web-interface-khuyến-nghị)
5. [Cấu hình Mapping nâng cao (mapping_config.json)](#5-cấu-hình-mapping-nâng-cao-mapping_configjson)
6. [Xử lý lỗi thường gặp](#6-xử-lý-lỗi-thường-gặp)

---

## 1. Tổng quan

### Mục đích
Công cụ này được thiết kế theo kiến trúc Dynamic (Động), giúp chuyển đổi dữ liệu từ MySQL sang PostgreSQL một cách linh hoạt. Điểm nổi bật:
- **Auto-discovery**: Tự động phát hiện mọi bảng và cột từ file SQL hoặc trực tiếp từ MySQL. Không còn bị giới hạn ở 5 bảng cố định.
- **Dynamic Type Mapping**: Tự động nhận diện kiểu dữ liệu và chuyển đổi.
- **Rule-based Value Conversion**: Chuyển đổi dữ liệu thông qua cấu hình, không cần sửa code (Ví dụ: `deletedAt=NULL` -> `false`).
- **Giao diện Web trực quan**: Cho phép người dùng kết nối database, gợi ý ánh xạ (mapping), và chạy tiến trình (Migration) ngay trên trình duyệt.

### 2 luồng chuyển đổi chính
1. **File SQL → MySQL → PostgreSQL** (Được dùng phổ biến nhất khi không có quyền truy cập trực tiếp vào DB nguồn).
2. **MySQL → PostgreSQL** (Khi đã có sẵn kết nối với MySQL local hoặc từ xa).

---

## 2. Cài đặt và Yêu cầu hệ thống

### Yêu cầu
- **Python**: >= 3.8
- **MySQL Server**: Để trung chuyển dữ liệu nếu đọc từ file SQL.
- **PostgreSQL Server**: Database đích.

### Cài đặt
Mở Terminal/PowerShell và chạy:
```bash
cd chuyendulieumysql_postgre_v_5
pip install -r requirements.txt
```

---

## 3. Cấu trúc thư mục

```
chuyendulieumysql_postgre_v_5/
│
├── alldatapostgre/                    # 📁 ĐẶT FILE SQL BACKUP VÀO ĐÂY
│
├── db_migrator/                       # 📦 Thư viện Core xử lý chuyển đổi
│   ├── config.py                      # Quản lý cấu hình mapping
│   ├── discovery.py                   # Phát hiện schema tự động
│   ├── migrator.py                    # Engine thực thi migration
│   ├── sql_parser.py                  # Đọc và parse file SQL
│   ├── type_mapper.py                 # Ánh xạ kiểu dữ liệu
│   └── value_converter.py             # Chuyển đổi giá trị dựa trên Rule
│
├── web/                               # 🌐 Web Server & UI
│   ├── app.py                         # Flask API routes
│   └── templates/
│       └── index.html                 # Giao diện chính (SPA)
│
├── _legacy/                           # 📁 Backup source code cũ (v1)
│
├── mapping_config.json                # ⚙️ File lưu cấu hình ánh xạ bảng/cột/kiểu dữ liệu
├── run_web.py                         # ▶️ File chạy Web App
└── requirements.txt                   # 📦 Dependencies
```

---

## 4. Sử dụng Web Interface (Khuyến nghị)

Giao diện Web là phương pháp nhanh chóng và dễ dàng nhất.

### Bước 1: Khởi động Web App
```bash
python run_web.py
```
Mở trình duyệt truy cập: **http://localhost:5000**

### Bước 2: Setup Connection (Kết nối)
1. Chọn luồng chuyển đổi ở mục **Migration Flow**.
   - Nếu bạn có file `.sql`, chọn *SQL File -> MySQL -> PostgreSQL*. 
   - Sao chép file `.sql` của bạn vào thư mục `alldatapostgre/` và điền tên file.
2. Điền thông tin kết nối tới MySQL local (để import tạm) và PostgreSQL (đích).
3. Nhấn **Discover Schemas** để công cụ phân tích cấu trúc của file SQL/MySQL và PostgreSQL.

### Bước 3: Select Tables (Chọn bảng)
- Sau khi Discovery thành công, công cụ sẽ liệt kê MỌI BẢNG có trong hệ thống nguồn.
- Đánh dấu tick chọn những bảng bạn muốn đồng bộ.
- Nhấn **Auto-Suggest Mappings**.

### Bước 4: Review Mappings (Xác nhận ánh xạ)
- Công cụ sẽ tự động đối chiếu các trường dựa vào tên và một số quy ước ngầm (ví dụ `deletedAt` -> `is_deleted`).
- Kiểm tra các ánh xạ xem đã đúng ý chưa. Có thể tuỳ chỉnh lại bảng cột ngay trên giao diện (Dropdown menu).
- Bấm **Save Configuration**. Thiết lập này sẽ được lưu vào file `mapping_config.json`.

### Bước 5: Execute Migration (Thực thi)
- Chọn chiến lược chạy (Truncate + Insert, Upsert, hoặc Append).
- Nhấn **START MIGRATION**.
- Quan sát thanh tiến trình (Progress Bar) và log chạy bên dưới.

---

## 5. Cấu hình Mapping nâng cao (mapping_config.json)

Mặc dù giao diện web giúp bạn cấu hình bảng và cột, những phép biến đổi dữ liệu chuyên sâu (Value Transforms) có thể được khai báo trực tiếp trong file `mapping_config.json`.

Cấu trúc file `mapping_config.json`:
```json
{
  "version": "2.0",
  "target_schema": "hrm-services",
  
  "type_overrides": {
    "user.types": "jsonb"
  },
  
  "value_transforms": {
    "*.deletedAt -> *.is_deleted": { "type": "null_to_bool" },
    "user_movement.type -> *.Type": {
      "type": "enum_to_int",
      "mapping": { "Bổ nhiệm": 1, "Điều chuyển": 2, "Miễn nhiệm": 3 }
    },
    "*.createdAt -> *.SyncCreatedDate": { "type": "timestamp" }
  },
  
  "table_mapping": {
    "user": "office-employee"
  },
  
  "column_mapping": {
    "user": {
      "id": "Id",
      "deletedAt": "is_deleted"
    }
  }
}
```

### Các Rules biến đổi phổ biến (Value Transforms):
- `null_to_bool`: Nếu nguồn là NULL -> `false`, có giá trị -> `true`. (Thường dùng cho `deletedAt`).
- `enum_to_int`: Chuyển chuỗi (text) thành số nguyên dựa theo danh sách `mapping`.
- `timestamp`: Chuyển chuỗi datetime về chuẩn PostgreSQL, tự động chuẩn hóa định dạng.
- `json_normalize`: Validate và làm sạch chuỗi JSON.
- Dùng dấu hoa thị `*` làm ký tự đại diện (Wildcard). Ví dụ: `*.createdAt -> *.SyncCreatedDate` sẽ áp dụng cho cột `createdAt` ở TẤT CẢ CÁC BẢNG mà được mapping sang `SyncCreatedDate`.

---

## 6. Xử lý lỗi thường gặp

### Lỗi 1: "Không tìm thấy file backup.sql trong thư mục alldatapostgre/"
- Bạn phải đảm bảo copy đúng file SQL vào thư mục `alldatapostgre/` và gõ đúng tên file trong ô input của giao diện web.

### Lỗi 2: Bảng PostgreSQL thiếu cột hoặc báo "Column does not exist"
- Nếu bạn bổ sung dữ liệu/cột trên cơ sở dữ liệu mới, hãy chạy lại **Discover Schemas** để công cụ phát hiện cột mới và Mapping lại. Đừng quên lưu (Save) trước khi Migrate.

### Lỗi 3: Unicode/Tiếng Việt bị lỗi khi Import MySQL
- Hãy thiết lập `character set utf8mb4` trên MySQL server cục bộ của bạn, engine migrate tự xử lý chèn chuỗi theo chuẩn UTF-8.

### Lỗi 4: Sai mật khẩu hoặc Timeout (Connection Refused)
- Kiểm tra kĩ cổng (Port), tường lửa, và mật khẩu truy cập của cả MySQL lẫn PostgreSQL. Nếu server dùng IP Lan, đảm bảo máy bạn ping được server đó.