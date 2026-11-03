# Trợ lý Kế toán SME

## Chạy local
\`\`\`bash
python -m venv .venv
pip install -r requirements.txt
streamlit run app.py
\`\`\`

## Kiểm thử
\`\`\`bash
pytest -q
\`\`\`

## Phạm vi
- FR-01: đọc Excel/CSV, dò header, khớp 1-1 và ghép hóa đơn cùng đối tác; epsilon mặc định 1.000 VND, tối đa 12 hóa đơn.
- FR-02: đọc XML/ZIP, kiểm tra thông tin người mua cơ bản, phát hiện dấu hiệu cấu trúc chữ ký, đối chiếu khoản chi theo số tiền/diễn giải.
- FR-03: staging, gợi ý định khoản theo từ khóa, tách dòng thiếu chứng từ khỏi file import.
- FR-04: tuổi nợ và ba mẫu tin nhắn.
- FR-05: số dư tiền mặt theo từng dòng, thiếu tài khoản, thanh toán tiền mặt từ 20 triệu và lệch VAT.
- FR-06: tổng thu/chi, dòng tiền thuần, tồn cuối kỳ.
- FR-07: che một số chuỗi định danh; RAG chưa bật vì chưa có kho văn bản chính thức đã xác minh.
- FR-08: hiển thị gói; đăng nhập, thuê bao và VietQR webhook chưa bật.

## Giới hạn quan trọng
- File nghiệp vụ xử lý trong bộ nhớ và file xuất tạo bằng BytesIO. TTL được kiểm tra khi có tương tác/rerun, không đảm bảo xóa đúng thời điểm khi phiên treo hoặc worker còn giữ RAM.
- XML parser không xác thực chữ ký số về mặt mật mã, không tra cứu trạng thái hóa đơn tại cơ quan Thuế.
- File import là staging tổng quát; không tuyên bố tương thích 100% MISA/VACOM khi chưa có mẫu import đúng phiên bản.
- Chưa có đăng nhập/phân quyền, subscription DB, thanh toán VietQR/webhook, gửi Zalo/Email, AI RAG hay P&L đầy đủ.
- Kết quả tự động phải được kế toán duyệt.

## Deploy Streamlit Community Cloud
Chọn repository/branch, đặt Main file path là app.py và deploy sau khi kiểm thử dependencies.
