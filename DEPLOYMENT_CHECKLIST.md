# Checklist triển khai Trợ lý Kế toán SME

## A. Supabase
- [ ] Tạo project Supabase.
- [ ] Chạy toàn bộ supabase/schema.sql trong SQL Editor.
- [ ] Bật Email provider và cấu hình email confirmation theo chính sách đăng ký.
- [ ] Lấy Project URL, Publishable Key và Secret Key.
- [ ] Kiểm tra các bảng profiles, subscriptions, payments, webhook_events, legal_documents và bucket private legal-corpus.
- [ ] Không bật quyền public đọc/ghi cho payments, webhook_events hoặc legal_documents.

## B. Streamlit
- [ ] Deploy branch feature/full-mvp-hardening, main file app.py.
- [ ] Cấu hình SUPABASE_URL và SUPABASE_PUBLISHABLE_KEY.
- [ ] Cấu hình SUPABASE_SECRET_KEY ở server-side secrets để nạp kho pháp lý private.
- [ ] Cấu hình LEGAL_ADMIN_USER_IDS bằng UUID tài khoản Auth được phép xuất bản văn bản.
- [ ] Cấu hình GEMINI_API_KEY và GEMINI_MODEL=gemini-3.5-flash-lite cho FR-09; không ghi khóa vào repo.
- [ ] Dùng cùng GEMINI_API_KEY/GEMINI_MODEL cho OCR và Cố vấn pháp lý RAG; không cần OpenAI API key.
- [ ] Kiểm thử OCR PDF có text, PDF scan, ảnh hóa đơn, sao kê, UNC và ảnh chụp chuyển tiền; kiểm tra cả trang nhiều dòng giao dịch.
- [ ] Xác nhận cảnh báo đồng ý gửi dữ liệu cho Google Gemini API; kiểm tra ứng dụng gửi inline, không tạo Gemini Files API record, không ghi chứng từ xuống ổ đĩa.
- [ ] Trước khi dùng Free Tier với chứng từ thật, pháp chế/đơn vị quản lý dữ liệu phải chấp thuận điều khoản dữ liệu của Google; Free Tier có thể dùng nội dung gửi lên để cải thiện sản phẩm.
- [ ] Kiểm thử mapping 1-1, thanh toán một phần, thanh toán gộp 1-N, MST chi nhánh -002/-003, số hóa đơn có số 0 đầu và nhiều ứng viên trùng tiền.
- [ ] Người dùng rà soát/sửa dữ liệu OCR trước khi mapping và xác nhận thủ công; không tự ghi sổ.
- [ ] Kiểm tra chức năng xóa dữ liệu sau tải xuống và timeout phiên.
- [ ] Kiểm tra guest chỉ tải tối đa 50 dòng mỗi sheet.

## C. SePay / VietQR
- [ ] Deploy webhook API theo render.yaml; xác nhận /health trả {"ok":true}.
- [ ] Cấu hình SUPABASE_URL và SUPABASE_SECRET_KEY trên backend và cron worker.
- [ ] Chọn HMAC-SHA256; cấu hình SEPAY_WEBHOOK_HMAC_SECRET ở cả hai phía. Nếu dùng API Key, cấu hình SEPAY_WEBHOOK_API_KEY.
- [ ] Cấu hình PAYMENT_BANK bằng short_name chính thức, PAYMENT_ACCOUNT và PAYMENT_ACCOUNT_HOLDER ở Streamlit.
- [ ] Bật PAYMENT_REQUIRE_SEVQR=true nếu loại tài khoản VietinBank yêu cầu prefix SEVQR.
- [ ] Cấu hình URL webhook /webhooks/sepay, sự kiện tiền vào và xác thực tương ứng.
- [ ] Kiểm thử webhook bằng công cụ gửi thử của SePay; xác nhận hàng đợi webhook_events và trạng thái đơn.
- [ ] Kiểm tra cron worker chạy định kỳ và xử lý lại event failed/pending.
- [ ] Kiểm tra payment_code, amount, accountNumber và subscription expiry trước khi mở thanh toán thật.

## D. Kho văn bản pháp luật
- [ ] Đăng nhập bằng tài khoản có UUID nằm trong LEGAL_ADMIN_USER_IDS.
- [ ] Nạp PDF có text hoặc DOCX từ cổng chính thức; không dùng bản scan nếu chưa OCR.
- [ ] Điền đúng số hiệu, ngày hiệu lực, ngày hết hiệu lực, điều/khoản và URL nguồn chính thức.
- [ ] Chỉ chọn "Còn hiệu lực" sau khi người có trách nhiệm kiểm tra văn bản sửa đổi/thay thế.
- [ ] Thử câu hỏi có căn cứ và câu hỏi không có căn cứ; trường hợp không có căn cứ phải từ chối kết luận.
- [ ] Không đưa dữ liệu khách hàng, số hợp đồng hoặc thông tin mật vào câu hỏi AI.

## E. MISA / VACOM
- [ ] Tải đúng mẫu import từ phiên bản MISA/VACOM đang dùng.
- [ ] Dùng mẫu riêng cho Thu tiền gửi, Chi tiền gửi và Chuyển tiền nội bộ; không nhập trực tiếp sao kê ngân hàng.
- [ ] Chọn tiền tố và số chứng từ bắt đầu không trùng số đã ghi sổ.
- [ ] Soát lại ngày hạch toán, ngày chứng từ, số chứng từ, đối tượng, tài khoản, số tiền và mã hóa đơn.
- [ ] Thử import trên dữ liệu test/đơn vị test trước khi dùng dữ liệu thật.

## F. Nghiệm thu trước production
- [ ] Chạy GitHub Actions; yêu cầu compile, PostgreSQL schema parser và toàn bộ pytest đều PASS.
- [ ] Tải và chạy bộ file mẫu ở từng phân hệ trước khi dùng dữ liệu thật.
- [ ] Kiểm tra file XML mẫu được đánh dấu placeholder, không dùng để nghiệm thu chữ ký số.
- [ ] Benchmark 5.000 giao dịch với mẫu sao kê thực tế của từng ngân hàng.
- [ ] Kiểm thử đối soát 1-1, trả gộp, trùng hóa đơn, thanh toán một phần và giao dịch chưa xác định.
- [ ] Kiểm thử XML có chữ ký, thiếu chữ ký, sai MST và XML không hợp lệ.
- [ ] Kiểm thử RLS: user A không đọc được payment/subscription của user B.
- [ ] Kiểm thử webhook giả mạo, sai số tiền, sai tài khoản, retry và duplicate transaction ID.
- [ ] Kiểm thử sao lưu/khôi phục legal corpus và quy trình thu hồi văn bản hết hiệu lực.
- [ ] Có kế toán và pháp chế xác nhận các quy tắc trước khi đưa vào vận hành.

## Cấu hình chưa thể điền thay chủ hệ thống
Các giá trị cần cung cấp từ chủ tài khoản/tổ chức: Supabase project credentials, UUID quản trị, tài khoản ngân hàng nhận tiền, SePay webhook secret, URL Streamlit và mẫu import MISA/VACOM thực tế. Không đưa các secret này vào GitHub.
