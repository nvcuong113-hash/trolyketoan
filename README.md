# Trợ lý Kế toán SME

## Kiến trúc
- Streamlit: xử lý tệp kế toán trong bộ nhớ phiên, không lưu sao kê/hóa đơn/sổ kế toán vào database.
- Supabase: chỉ lưu user, profile, subscription và payment metadata.
- FastAPI: nhận webhook SePay độc lập.
- Static GEO site: thư mục web/, deploy Cloudflare Pages với output directory web.
- Legal RAG: nạp văn bản chính thức theo metadata hiệu lực; chỉ tài liệu được quản trị viên xác nhận mới được truy xuất.

## Chạy Streamlit local
\`\`\`bash
python -m venv .venv
pip install -r requirements.txt
streamlit run app.py
\`\`\`

## Chạy webhook backend
\`\`\`bash
uvicorn billing_api:app --host 0.0.0.0 --port 8000
\`\`\`

## Cấu hình Supabase
1. Tạo project Supabase.
2. Chạy toàn bộ supabase/schema.sql trong SQL Editor.
3. Lấy Project URL và Publishable Key; đặt SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY cho Streamlit.
4. Lấy Secret Key chỉ dùng phía server; đặt SUPABASE_SECRET_KEY cho webhook service.
5. Không bao giờ đưa Secret Key vào repo hoặc biến môi trường client-side.

## Cấu hình SePay / VietQR
- Đặt SEPAY_WEBHOOK_API_KEY ở backend và cấu hình cùng API Key trên SePay Webhook.
- URL webhook: /webhooks/sepay.
- Chọn sự kiện tiền vào, bật xác thực API Key.
- Đặt PAYMENT_BANK, PAYMENT_ACCOUNT, PAYMENT_ACCOUNT_HOLDER; nếu tài khoản VietinBank thuộc nhóm yêu cầu prefix, bật PAYMENT_REQUIRE_SEVQR=true.
- SePay yêu cầu HTTP 200/201 và JSON {"success": true}; webhook có thể retry, do đó DB xử lý idempotent theo transaction ID.
- Dùng tính năng gửi thử webhook trước khi nhận tiền thật.

## Cấu hình AI RAG
- Đặt OPENAI_API_KEY phía server Streamlit; OPENAI_MODEL mặc định gpt-4o-mini.
- Nạp file PDF có text / DOCX / TXT / MD của văn bản pháp luật chính thức.
- Điền số hiệu, ngày hiệu lực và URL nguồn; chỉ chọn "Còn hiệu lực" khi đã kiểm chứng.
- Kho tài liệu hiện lưu trong session RAM; chưa có quy trình admin publish corpus bền vững vào bản deploy.

## Kiểm thử
\`\`\`bash
python -m compileall -q app.py core.py accounting_extensions.py auth_service.py billing_api.py legal_rag.py
pytest -q
\`\`\`

## Các nghiệp vụ đã có
- FR-01: đối soát tiền vào, ghép hóa đơn trả gộp cùng đối tác; gợi ý phân loại tiền ra.
- FR-02: đọc XML/ZIP; phân loại Đủ chứng từ, Thiếu hóa đơn (Nợ chứng từ), Hóa đơn treo dựa trên khoản chi, nhà cung cấp và số tiền.
- FR-03: staging; xuất báo cáo nội bộ; điền file mẫu import MISA/VACOM do người dùng tải lên. Thu tiền gửi và Chi tiền gửi là hai loại file riêng.
- FR-04: tuổi nợ và mẫu nhắc công nợ/nhắc hóa đơn/hoàn ứng.
- FR-05: 6 nhóm cảnh báo sơ bộ gồm âm quỹ, thiếu tài khoản, rà soát tiền mặt, lệch VAT, tài khoản bất thường/dòng trùng, cân đối phát sinh và kết chuyển.
- FR-06: báo cáo dòng tiền; P&L ước tính và Top 5 phải thu/phải trả khi có sổ chi tiết.
- FR-07: legal RAG cục bộ theo văn bản đã nạp, trích đoạn nguồn và phiếu checklist.
- FR-08: Supabase Auth, gói thuê bao, tạo payment order, QR, SePay webhook và kích hoạt subscription.

## Giới hạn / việc cần nghiệm thu với dữ liệu thật
- MISA: mỗi loại chứng từ dùng mẫu Excel riêng; cần tải mẫu chính xác từ phiên bản đang dùng để mapping. MISA SME 2026 nêu các cột bắt buộc cơ bản gồm ngày hạch toán, ngày chứng từ, số chứng từ; dữ liệu còn lại phải map theo mẫu.
- VACOM: chưa có file mẫu thực tế nên chỉ hỗ trợ mapping theo header của file mẫu do người dùng tải lên, chưa xác nhận khả năng import end-to-end.
- Chữ ký số XML chưa được xác thực mật mã; chưa kết nối tra cứu trạng thái hóa đơn trên cổng Thuế.
- Legal corpus chưa được đóng gói sẵn và chưa có người kiểm duyệt pháp lý duyệt toàn bộ metadata. Không bật kết luận nếu không có văn bản phù hợp.
- P&L là ước tính theo tài khoản và cần map theo hệ thống tài khoản / chế độ kế toán doanh nghiệp.
- 20 triệu không được hard-code làm ngưỡng thuế cố định; cần xác định điều kiện theo từng loại thuế, kỳ và văn bản áp dụng.
- SePay/Supabase chưa thể nhận tiền thật cho đến khi chủ tài khoản cung cấp và cấu hình credentials, tài khoản ngân hàng và webhook.
- Session TTL được kiểm tra khi Streamlit rerun; không đảm bảo xóa tức thì nếu browser treo/worker vẫn giữ phiên.
- NFR hiệu năng 5.000 dòng/3 giây cần benchmark trên mẫu thực tế từng ngân hàng, chưa được chứng nhận.

## Deploy
- Streamlit Community Cloud: chọn repository, branch, main file app.py, cấu hình secrets theo .env.example.
- Webhook API: deploy billing_api.py lên Render/Railway/VPS và cấu hình biến môi trường server-only.
- Static front door: Cloudflare Pages, output directory web. Trước khi public, cập nhật appUrl trong web/index.html sang URL Streamlit thực tế.
