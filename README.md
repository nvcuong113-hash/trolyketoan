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
- Kho tài liệu hiện lưu trong session RAM; chưa có quy trình admin publish corpus bền vững vào bản deploy.

## Kiểm thử
```bash
python -m compileall -q app.py core.py accounting_extensions.py auth_service.py billing_api.py legal_rag.py
pytest -q
```

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
- SePay/Supabase chưa thể nhận tiền thật cho đến khi chủ tài khoản cung cấp và cấu hình credentials, tài khoản ngân hàng và webhook.
- Dữ liệu tạm được xóa sau download theo mặc định và có nút xóa thủ công. Session TTL được kiểm tra khi Streamlit rerun; không đảm bảo xóa tức thì nếu browser treo/worker vẫn giữ phiên.
- CI có benchmark hồi quy 5.000 dòng/3 giây trên GitHub runner; vẫn cần benchmark với mẫu thực tế từng ngân hàng trước khi cam kết SLA production.

## Deploy
- Streamlit Community Cloud: chọn repository, branch, main file app.py, cấu hình secrets theo .env.example.
- Webhook API: deploy billing_api.py lên Render/Railway/VPS và cấu hình biến môi trường server-only.
- Static front door: Cloudflare Pages, output directory web. Trước khi public, cập nhật window.TROLY_APP_URL trong web/config.js sang URL Streamlit thực tế.
