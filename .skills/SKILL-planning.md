# SKILL: Planning Before Implementation

## Mô tả
Yêu cầu Agent luôn dừng lại và lập kế hoạch thực hiện rõ ràng trước khi sửa đổi bất kỳ đoạn mã nguồn nào trong kho lưu trữ này.

## Quy trình
1. **Phân tích Yêu cầu**: Xác định chính xác mong muốn của User và phạm vi ảnh hưởng trong codebase.
2. **Nghiên cứu kiến trúc**: Đọc các file liên quan và định vị các điểm thay đổi cần thiết.
3. **Thiết lập Kế hoạch thực hiện**:
   - Viết ra các bước nhỏ cần làm.
   - Xác định trước các ca kiểm thử (test cases) cần viết cho tính năng mới.
4. **Cập nhật Tiến trình**: Đồng bộ danh sách công việc vào `TASKS.md` ở root và `task.md` ở artifacts.
5. **Xin ý kiến User**: Nếu thay đổi lớn hoặc ảnh hưởng kiến trúc cốt lõi, hãy trình bày kế hoạch và chờ User phê duyệt trước khi code.

## Checklists
- [ ] Mình đã đọc hết các file liên quan chưa?
- [ ] Kế hoạch của mình đã chia nhỏ thành các tác vụ dưới 2 giờ thực hiện chưa?
- [ ] Mình đã có phương án viết test kiểm thử cho thay đổi này chưa?
