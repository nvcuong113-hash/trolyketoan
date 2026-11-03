# Trợ lý Kế toán SME

MVP Streamlit theo SRS v1.0.

## Chạy local

```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## Module MVP
- FR-01: đọc sao kê Excel/CSV, khớp hóa đơn 1-1 và tìm tổ hợp trả gộp.
- FR-02: đọc XML/ZIP hóa đơn, kiểm tra một số trường thông tin và dấu hiệu chữ ký.
- FR-03: chuẩn hóa staging, gợi ý định khoản theo từ khóa, xuất hai workbook.
- FR-04: phân nhóm tuổi nợ và tạo nội dung nhắc việc.
- FR-05: kiểm tra âm quỹ, thiếu tài khoản và đối chiếu thuế cơ bản.
- FR-06: tổng hợp thu/chi và báo cáo dòng tiền.
- FR-07: giao diện chuẩn bị tư vấn; chưa bật RAG/AI do chưa có kho văn bản pháp luật đã kiểm chứng.
- FR-08: hiển thị gói dịch vụ; chưa tích hợp xác thực và webhook thanh toán.

## Triển khai Streamlit Community Cloud
1. Chọn repository và branch `main`.
2. Main file path: `app.py`.
3. Deploy; các chức năng xử lý file cơ bản không cần secrets.

## Bảo mật và giới hạn
- File tải lên được đọc từ bộ nhớ và workbook xuất ra bằng `BytesIO`; ứng dụng không ghi file nghiệp vụ xuống ổ đĩa.
- Dữ liệu có thể nằm trong session RAM. Nút xóa phiên và TTL được kiểm tra khi có tương tác/rerun; không bảo đảm xóa đúng thời điểm nếu phiên treo hoặc tiến trình được nền tảng giữ lại.
- Chưa có đăng nhập, phân quyền, subscription DB, webhook VietQR, tích hợp MISA/VACOM chính thức, xác minh chữ ký số XML hoặc RAG pháp luật.
- File import hiện là staging Excel tổng quát, chưa thể tuyên bố tương thích 100% khi chưa có mẫu import chính thức theo phiên bản MISA/VACOM.
- Mọi kết quả ghép và định khoản cần kế toán duyệt.
