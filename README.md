# Trợ lý Kế toán SME

## Kiến trúc
- Streamlit: xử lý tệp kế toán trong bộ nhớ phiên, không lưu sao kê/hóa đơn/sổ kế toán vào database.
- Supabase: chỉ lưu user, profile, subscription, payment metadata và văn bản pháp luật công khai trong private Storage; không lưu sổ sách khách hàng.
- FastAPI: nhận webhook SePay độc lập.
- Static GEO site: thư mục web/, deploy Cloudflare Pages với output directory web.
- Legal RAG: nạp văn bản chính thức theo metadata hiệu lực; chỉ tài liệu được quản trị viên xác nhận mới được truy xuất.

## Chạy Streamlit local
1. Sao chép .env.example thành .env và điền cấu hình local; .env đã được .gitignore.
```bash
python -m venv .venv
pip install -r requirements.txt
pip install -r requirements-dev.txt
streamlit run app.py
```

## Chạy webhook backend
```bash
uvicorn billing_api:app --host 0.0.0.0 --port 8000
```

## Cấu hình Supabase
1. Tạo project Supabase.
2. Chạy toàn bộ supabase/schema.sql trong SQL Editor.
3. Lấy Project URL và Publishable Key; đặt SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY cho Streamlit.
4. Lấy Secret Key chỉ dùng phía server; đặt SUPABASE_SECRET_KEY cho webhook service và bộ nạp kho pháp lý trong Streamlit server.
5. Không bao giờ đưa Secret Key vào repo hoặc biến môi trường client-side.

## Cấu hình SePay / VietQR
- Khuyến nghị đặt SEPAY_WEBHOOK_HMAC_SECRET và chọn HMAC-SHA256 trên SePay; API Key (SEPAY_WEBHOOK_API_KEY) là phương án thay thế.
- URL webhook: /webhooks/sepay.
- Chọn sự kiện tiền vào, bật xác thực HMAC-SHA256 hoặc API Key tương ứng với biến môi trường server.
- Đặt PAYMENT_BANK, PAYMENT_ACCOUNT, PAYMENT_ACCOUNT_HOLDER; nếu tài khoản VietinBank thuộc nhóm yêu cầu prefix, bật PAYMENT_REQUIRE_SEVQR=true.
- SePay yêu cầu HTTP 200/201 và JSON {"success": true}; webhook được ghi vào hàng đợi bền vững trước khi phản hồi, xử lý nền và cron worker thử lại. DB chống trùng theo transaction ID.
- Dùng tính năng gửi thử webhook trước khi nhận tiền thật.

## Cấu hình AI RAG
- Đặt OPENAI_API_KEY phía server Streamlit; OPENAI_MODEL mặc định gpt-4o-mini.
- Nạp file PDF có text / DOCX / TXT / MD của văn bản pháp luật chính thức.
- Điền số hiệu, ngày hiệu lực và URL nguồn; chỉ chọn "Còn hiệu lực" khi đã kiểm chứng.
- Tài khoản quản trị được allowlist bằng LEGAL_ADMIN_USER_IDS có thể xuất bản văn bản vào private Supabase Storage; mọi phiên người dùng chỉ tải các văn bản còn hiệu lực để truy xuất. Cần SUPABASE_SECRET_KEY phía server.

## AI OCR chứng từ & mapping (FR-09)
- Cấu hình `OPENAI_API_KEY` và tùy chọn `OPENAI_MODEL` (mặc định `gpt-4o-mini`) trong Streamlit Secrets.
- Hỗ trợ PDF, PNG, JPG/JPEG tối đa 25 MB/tệp; tối đa 15 tệp/lượt. PDF được gửi dưới dạng `input_file` để mô hình đọc text và ảnh trang; ảnh dùng `input_image`.
- Nội dung được gửi trực tiếp trong request Responses API dưới dạng base64; ứng dụng không tạo bản ghi Files API, không ghi file lên ổ đĩa và không lưu dữ liệu kế toán vào Supabase. Người dùng phải chủ động tick xác nhận trước khi OCR.
- Sau OCR, kiểm tra/sửa dữ liệu nhận diện trước khi mapping. Engine giữ nguyên hậu tố MST chi nhánh, chuẩn hóa số 0 đầu số hóa đơn, gộp dòng thuế suất và không tự ghép khi MST xung đột hoặc có nhiều ứng viên.
- Mapping hóa đơn–sao kê/UNC chỉ là đề xuất trong phiên. Kế toán xác nhận thủ công; không tự hạch toán hoặc cập nhật sổ kế toán.
- Chi phí API phụ thuộc số trang, độ chi tiết PDF và model. Dùng `low` để giảm token khi chứng từ rõ; dùng `high` cho scan/chữ nhỏ.

## Kiểm thử
```bash
python -m compileall -q app.py core.py accounting_extensions.py auth_service.py billing_api.py legal_rag.py legal_storage.py process_billing_queue.py document_ai.py sample_data.py
pytest -q
```

## File mẫu dùng thử
Mỗi phân hệ có nút tải file mẫu trực tiếp trong giao diện. FR-02 có ZIP XML mẫu với chữ ký placeholder (không phải chữ ký số thật); FR-07 có file câu hỏi mẫu, không giả làm văn bản pháp luật. Bộ mẫu được sinh bằng `BytesIO` trong RAM, không ghi file lên server.

## Các nghiệp vụ đã có
- FR-01: đối soát tiền vào, ghép hóa đơn trả gộp cùng đối tác; gợi ý phân loại tiền ra.
- FR-02: đọc XML/ZIP; phân loại Đủ chứng từ, Thiếu hóa đơn (Nợ chứng từ), Hóa đơn treo dựa trên khoản chi, nhà cung cấp và số tiền.
- FR-03: staging; xuất báo cáo nội bộ; điền file mẫu import MISA/VACOM do người dùng tải lên. Thu tiền gửi, Chi tiền gửi và Chuyển tiền nội bộ là ba loại file riêng; chuyển nội bộ bắt buộc có tài khoản đi/đến.
- FR-04: tuổi nợ và mẫu nhắc công nợ/nhắc hóa đơn/hoàn ứng.
- FR-05: 6 nhóm cảnh báo sơ bộ gồm âm quỹ, thiếu tài khoản, rà soát tiền mặt, lệch VAT, tài khoản bất thường/dòng trùng, cân đối phát sinh và kết chuyển.
- FR-06: báo cáo dòng tiền; P&L ước tính và Top 5 phải thu/phải trả khi có sổ chi tiết.
- FR-07: legal RAG cục bộ theo văn bản đã nạp, trích đoạn nguồn và phiếu checklist.
- FR-08: Supabase Auth, gói thuê bao, tạo payment order, QR, SePay webhook và kích hoạt subscription.

## Giới hạn / việc cần nghiệm thu với dữ liệu thật
- MISA: Thu tiền gửi, Chi tiền gửi và Chuyển tiền nội bộ dùng ba mẫu Excel riêng; cần tải mẫu chính xác từ phiên bản đang dùng để mapping. MISA SME 2026 nêu các cột bắt buộc cho Thu tiền gửi và Chi tiền gửi gồm ngày hạch toán, ngày chứng từ, số chứng từ. Chương trình không hỗ trợ nhập trực tiếp file sao kê ngân hàng; cần tách Thu tiền gửi, Chi tiền gửi và Chuyển tiền nội bộ thành từng file mẫu riêng.
- VACOM: chưa có file mẫu thực tế nên chỉ hỗ trợ mapping theo header của file mẫu do người dùng tải lên, chưa xác nhận khả năng import end-to-end.
- Chữ ký số XML chưa được xác thực mật mã; chưa kết nối tra cứu trạng thái hóa đơn trên cổng Thuế.
- Legal corpus chưa được đóng gói sẵn; cần người quản trị nạp văn bản, kiểm tra hiệu lực và xác nhận metadata. Tệp được lưu ở Supabase Storage private; chỉ URL nguồn chính thức được chấp nhận. Không bật kết luận nếu không có văn bản phù hợp.
- P&L là ước tính theo tài khoản và cần map theo hệ thống tài khoản / chế độ kế toán doanh nghiệp.
- 20 triệu không được hard-code làm ngưỡng thuế cố định; cần xác định điều kiện theo từng loại thuế, kỳ và văn bản áp dụng.
- SePay/Supabase chưa thể nhận tiền thật cho đến khi chủ tài khoản cung cấp và cấu hình credentials, tài khoản ngân hàng và webhook. HMAC theo raw body + timestamp; API Key là phương án dự phòng. Cấu hình IP allowlist và kiểm tra đối soát giao dịch định kỳ trước production.
- Dữ liệu tạm được xóa sau download theo mặc định và có nút xóa thủ công. Session TTL được kiểm tra khi Streamlit rerun; không đảm bảo xóa tức thì nếu browser treo/worker vẫn giữ phiên.
- CI có benchmark hồi quy 5.000 dòng/3 giây trên GitHub runner; vẫn cần benchmark với mẫu thực tế từng ngân hàng trước khi cam kết SLA production.

## Deploy
- Streamlit Community Cloud: chọn repository, branch, main file app.py, cấu hình secrets theo .env.example.
- Webhook API: deploy billing_api.py lên Render/Railway/VPS và cấu hình biến môi trường server-only.
- Static front door: Cloudflare Pages, output directory web. Trước khi public, cập nhật window.TROLY_APP_URL trong web/config.js sang URL Streamlit thực tế.
