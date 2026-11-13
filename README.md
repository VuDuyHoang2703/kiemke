# Hệ Thống Kiểm Kê Linh Kiện Robot Cho Thuê

Ứng dụng web quản lý tiếp nhận trả hàng, kiểm kê chi tiết linh kiện robot, phân tích dữ liệu đơn hàng CRM, tự động tính toán thiếu/dư, tính tiền bù và xuất báo cáo hoàn trả thiết bị.

---

## 📁 Cấu trúc thư mục dự án

```text
KiemKeLinhKien/
├── api/
│   └── index.py            # Entry point chạy Serverless Function trên Vercel
├── data/
│   ├── catalog.json        # Danh mục linh kiện & bộ chuẩn (đã biên soạn sẵn)
│   ├── inventory.db        # Cơ sở dữ liệu SQLite
│   ├── KIEM KE HANG THUE.xlsx                   # File gốc cấu trúc bộ & linh kiện
│   ├── CRM_Saleorder_12.11.2026_13.57.52_512.xlsx # File dữ liệu mẫu CRM
│   └── BGCT07-BẢNG GIÁ CHI TIẾT BỘ MỞ RỘNG 2681.xlsx # Bảng giá chi tiết tham khảo
├── Image/                  # Ảnh bìa đại diện cho các bộ kit
├── static/                 # Tài nguyên tĩnh (CSS, JS, 600+ ảnh linh kiện, icons)
├── templates/
│   └── index.html          # Giao diện người dùng duy nhất (Single-Page App)
├── app.py                  # Ứng dụng máy chủ Flask chính (API & logic nghiệp vụ)
├── crm_parser.py           # Bộ đọc và đối soát đơn hàng từ file Excel CRM
├── database.py             # Quản lý schema, kết nối SQLite & nghiệp vụ lưu trữ
├── excel_parser.py         # Bộ trích xuất linh kiện & ảnh từ file Excel gốc
├── requirements.txt        # Danh sách thư viện Python phụ thuộc
├── vercel.json             # Cấu hình định tuyến và triển khai trên Vercel
├── start.bat               # File chạy nhanh 1-click trên Windows
├── .gitignore              # Danh sách loại trừ khi đẩy lên Git
└── README.md               # Tài liệu hướng dẫn sử dụng & triển khai
```

---

## 🚀 1. Chạy trên máy tính cá nhân (Local)

### Yêu cầu
- Python 3.9+ đã cài đặt trên máy.

### Cách chạy nhanh (Windows):
- Nhấp đúp chuột vào file **`start.bat`**.

### Hoặc chạy qua dòng lệnh:
1. Cài đặt các thư viện phụ thuộc:
   ```bash
   pip install -r requirements.txt
   ```
2. Khởi chạy server:
   ```bash
   python app.py
   ```
3. Mở trình duyệt và truy cập: [http://127.0.0.1:5000](http://127.0.0.1:5000)

---

## 🌐 2. Hướng dẫn Đẩy lên GitHub / GitLab

Mở Terminal (Command Prompt hoặc PowerShell) tại thư mục dự án:

1. **Khởi tạo Git repo và kiểm tra trạng thái**:
   ```bash
   git init
   git add .
   git commit -m "Initial commit: He thong kiem ke linh kien robot cho thue"
   ```

2. **Tạo repository mới trên GitHub / GitLab** (ví dụ: `kiem-ke-linh-kien`).

3. **Liên kết và đẩy mã nguồn lên**:
   ```bash
   git branch -M main
   git remote add origin https://github.com/<tai-khoan-cua-ban>/<ten-repo>.git
   git push -u origin main
   ```

---

## ⚡ 3. Hướng dẫn Triển khai lên Vercel (Vercel Deploy)

Dự án đã được cấu hình sẵn file **`vercel.json`** và **`api/index.py`** tương thích hoàn toàn với nền tảng Vercel Serverless.

### Cách 1: Triển khai qua giao diện Web Vercel (Khuyên dùng - Nhanh nhất)
1. Đăng nhập vào [vercel.com](https://vercel.com).
2. Nhấn nút **Add New...** -> **Project**.
3. Chọn Repository GitHub vừa tạo ở bước trên và nhấn **Import**.
4. Tại mục **Configure Project**:
   - Framework Preset: Chọn **Other** (hoặc để Vercel tự nhận).
   - Root Directory: Để mặc định `./`.
   - Build and Output Settings: Để mặc định.
5. Nhấn **Deploy**. Sau 1-2 phút, bạn sẽ nhận được đường link website chính thức (ví dụ: `https://ten-du-an.vercel.app`).

### Cách 2: Triển khai bằng Vercel CLI
```bash
npm install -g vercel
vercel login
vercel --prod
```

> [!NOTE] Lưu ý về Database trên môi trường Serverless Vercel
> - Môi trường máy chủ serverless của Vercel là **Read-Only** (chỉ cho phép ghi vào thư mục tạm `/tmp`).
> - Mã nguồn trong `app.py` đã được cấu hình tự động nhận diện biến môi trường `VERCEL` để nạp cơ sở dữ liệu `inventory.db` vào `/tmp/` giúp hệ thống hoạt động bình thường trên Vercel mà không bị lỗi ghi file.
> - Tuy nhiên, do tính chất của Serverless Function (các bản sao container sẽ tự giải phóng sau một khoảng thời gian không dùng), dữ liệu ghi trong `/tmp` sẽ tự làm mới theo chu kỳ sống của container. Nếu cần lưu trữ dài hạn cho nhiều nhân viên cùng làm việc đồng thời trực tuyến, bạn có thể kết nối với cơ sở dữ liệu đám mây (như Supabase, Neon PostgreSQL, hoặc Turso libSQL).
