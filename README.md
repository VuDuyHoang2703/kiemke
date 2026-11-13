# Hệ Thống Kiểm Kê Linh Kiện Robot Cho Thuê

Hệ thống quản lý trả hàng, kiểm đếm linh kiện theo từng bộ robot từ file `KIEM KE HANG THUE.xlsx`, tính toán thiếu/dư tự động và xuất báo cáo tiền bù cho khách hàng.

## Cách chạy nhanh
- Cách 1: Nhấp đúp vào file `start.bat`
- Cách 2: Mở Terminal tại thư mục `d:\KiemKeLinhKien` và chạy:
  ```bash
  python app.py
  ```
  Sau đó mở trình duyệt tại `http://127.0.0.1:5000`.

## Quy trình sử dụng
1. **Tiếp nhận trả hàng**: Nhập tên người trả, tên đội, số điện thoại và chọn các bộ robot đã thuê (có thể chọn nhiều bộ và số lượng bộ). Nhấn **Tiếp tục**.
2. **Danh sách đội thuê**: Xem toàn bộ các đội đang chờ/đang/đã kiểm kê. Nhấn **Kiểm kê** ở đội cần đếm.
3. **Kiểm kê linh kiện**:
   - **Để trống** ô `SL thực`: mặc định là **Đủ** (tiền bù = 0).
   - **Nhập số >= 0**: tự động so sánh với `SL (theo bộ)` để biết đủ, dư hay thiếu.
   - **Nhập số âm** (ví dụ `-2`): hiểu là **Thiếu 2** linh kiện và tự động nhân đơn giá tính tiền bù.
   - Nhấn `Enter` hoặc `↓` để chuyển nhanh sang dòng kế tiếp; nhấp vào ảnh để phóng to xem chi tiết.
4. **Thống kê & Báo cáo**: Nhấn **Thống kê & Báo cáo** để xem bảng tổng hợp linh kiện thiếu, đơn giá, thành tiền từng món và tổng tiền bù. Hỗ trợ **Xuất Excel (.xlsx)**, **In / Lưu PDF**, hoặc **Copy tóm tắt gửi khách**.

