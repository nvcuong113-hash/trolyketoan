from __future__ import annotations

import io
import zipfile
from datetime import date, timedelta

import pandas as pd


def sample_workbook(module: str) -> dict[str, pd.DataFrame]:
    today = date.today()
    if module == "fr01":
        return {
            "Sao_ke": pd.DataFrame([
                {"Ngày giao dịch":today.strftime("%d/%m/%Y"),"Số tiền ghi Có":1500000,"Số tiền ghi Nợ":0,"Nội dung giao dịch":"CTY ABC thanh toan HD001"},
                {"Ngày giao dịch":today.strftime("%d/%m/%Y"),"Số tiền ghi Có":3000000,"Số tiền ghi Nợ":0,"Nội dung giao dịch":"CTY XYZ thanh toan cong no"},
                {"Ngày giao dịch":today.strftime("%d/%m/%Y"),"Số tiền ghi Có":0,"Số tiền ghi Nợ":850000,"Nội dung giao dịch":"Thanh toan dien nuoc van phong"},
            ]),
            "Cong_no": pd.DataFrame([
                {"Mã khách hàng":"ABC","Tên khách hàng":"CTY ABC","Số hóa đơn":"HD001","Ngày hóa đơn":today.strftime("%d/%m/%Y"),"Số tiền phải thu":1500000,"Ngày đến hạn":(today-timedelta(days=5)).strftime("%d/%m/%Y")},
                {"Mã khách hàng":"XYZ","Tên khách hàng":"CTY XYZ","Số hóa đơn":"HD002","Ngày hóa đơn":today.strftime("%d/%m/%Y"),"Số tiền phải thu":1000000,"Ngày đến hạn":(today-timedelta(days=10)).strftime("%d/%m/%Y")},
                {"Mã khách hàng":"XYZ","Tên khách hàng":"CTY XYZ","Số hóa đơn":"HD003","Ngày hóa đơn":today.strftime("%d/%m/%Y"),"Số tiền phải thu":2000000,"Ngày đến hạn":(today-timedelta(days=10)).strftime("%d/%m/%Y")},
            ])
        }
    if module == "fr02":
        return {
            "Payment_Log": pd.DataFrame([
                {"Ngày giao dịch":today.strftime("%d/%m/%Y"),"Số tiền ghi Nợ":1500000,"Nội dung":"Thanh toan NCC A MST 0101234567"},
                {"Ngày giao dịch":(today-timedelta(days=2)).strftime("%d/%m/%Y"),"Số tiền ghi Nợ":2500000,"Nội dung":"Thanh toan NCC B"},
            ])
        }
    if module == "fr03":
        return {
            "Du_lieu_giao_dich": pd.DataFrame([
                {"Ngày":today,"Đối tác":"CTY ABC","Diễn giải gốc":"CTY ABC thanh toan HD001","Diễn giải chuẩn":"Thu tiền khách hàng","Tiền thu":1500000,"Tiền chi":0,"Tài khoản Nợ":"1121","Tài khoản Có":"131","Mã hóa đơn":"HD001","Chưa đủ chứng từ":False,"Loại chứng từ":"Thu tiền gửi","Tài khoản đi":"","Tài khoản đến":"","Số tiền giao dịch":1500000},
                {"Ngày":today,"Đối tác":"NCC A","Diễn giải gốc":"Thanh toan NCC A","Diễn giải chuẩn":"Thanh toán nhà cung cấp","Tiền thu":0,"Tiền chi":850000,"Tài khoản Nợ":"331","Tài khoản Có":"1121","Mã hóa đơn":"","Chưa đủ chứng từ":True,"Loại chứng từ":"Chi tiền gửi","Tài khoản đi":"","Tài khoản đến":"","Số tiền giao dịch":850000},
            ])
        }
    if module == "fr04":
        return {
            "Cong_no": pd.DataFrame([
                {"Tên khách hàng":"CTY ABC","Số hóa đơn":"HD001","Số tiền phải thu":1500000,"Ngày đến hạn":(today-timedelta(days=2)).strftime("%d/%m/%Y")},
                {"Tên khách hàng":"CTY XYZ","Số hóa đơn":"HD002","Số tiền phải thu":3000000,"Ngày đến hạn":(today-timedelta(days=10)).strftime("%d/%m/%Y")},
                {"Tên khách hàng":"CTY DEF","Số hóa đơn":"HD003","Số tiền phải thu":5000000,"Ngày đến hạn":(today-timedelta(days=20)).strftime("%d/%m/%Y")},
                {"Tên khách hàng":"CTY GHI","Số hóa đơn":"HD004","Số tiền phải thu":750000,"Ngày đến hạn":(today+timedelta(days=5)).strftime("%d/%m/%Y")},
            ])
        }
    if module == "fr05":
        return {
            "Nhat_ky_chung": pd.DataFrame([
                {"Ngày hạch toán":today.strftime("%d/%m/%Y"),"Ngày chứng từ":today.strftime("%d/%m/%Y"),"Số chứng từ":"PC001","Tài khoản Nợ":"642","Tài khoản Có":"1111","Số tiền":25000000,"Diễn giải":"Mua hàng hóa - giao dịch test"},
                {"Ngày hạch toán":today.strftime("%d/%m/%Y"),"Ngày chứng từ":today.strftime("%d/%m/%Y"),"Số chứng từ":"PT001","Tài khoản Nợ":"1111","Tài khoản Có":"131","Số tiền":5000000,"Diễn giải":"Thu tiền khách hàng"},
            ]),
            "Trial_Balance": pd.DataFrame([
                {"Tài khoản":"1111","Tổng phát sinh Nợ":5000000,"Tổng phát sinh Có":25000000,"Số dư cuối kỳ Nợ":0,"Số dư cuối kỳ Có":20000000},
                {"Tài khoản":"642","Tổng phát sinh Nợ":25000000,"Tổng phát sinh Có":0,"Số dư cuối kỳ Nợ":25000000,"Số dư cuối kỳ Có":0},
            ]),
            "Bang_ke_VAT": pd.DataFrame([
                {"Số hóa đơn":"VAT001","Mã số thuế":"0101234567","Tiền thuế":2500000},
                {"Số hóa đơn":"VAT002","Mã số thuế":"0107654321","Tiền thuế":300000},
            ])
        }
    if module == "fr06":
        return {
            "Sao_ke": pd.DataFrame([
                {"Ngày giao dịch":today.strftime("%d/%m/%Y"),"Số tiền ghi Có":100000000,"Số tiền ghi Nợ":65000000,"Nội dung giao dịch":"Thu chi trong ngày"}
            ]),
            "So_chi_tiet": pd.DataFrame([
                {"Ngày hạch toán":today.strftime("%d/%m/%Y"),"Tài khoản Nợ":"1121","Tài khoản Có":"5111","Số tiền":100000000,"Diễn giải":"Doanh thu bán hàng"},
                {"Ngày hạch toán":today.strftime("%d/%m/%Y"),"Tài khoản Nợ":"632","Tài khoản Có":"156","Số tiền":40000000,"Diễn giải":"Giá vốn"},
                {"Ngày hạch toán":today.strftime("%d/%m/%Y"),"Tài khoản Nợ":"6421","Tài khoản Có":"334","Số tiền":15000000,"Diễn giải":"Chi phí lương"},
                {"Ngày hạch toán":today.strftime("%d/%m/%Y"),"Tài khoản Nợ":"6428","Tài khoản Có":"1121","Số tiền":10000000,"Diễn giải":"Chi phí vận hành"},
            ]),
            "Phai_thu": pd.DataFrame([{"Tên khách hàng":"CTY ABC","Còn phải thu":30000000},{"Tên khách hàng":"CTY XYZ","Còn phải thu":12000000}]),
            "Phai_tra": pd.DataFrame([{"Tên nhà cung cấp":"NCC A","Còn phải trả":18000000},{"Tên nhà cung cấp":"NCC B","Còn phải trả":7000000}])
        }
    if module == "fr08":
        return {
            "Goi_dich_vu": pd.DataFrame([
                {"Gói":"Khách vãng lai","Giá VNĐ/tháng":0,"Giới hạn dòng tải":50,"Giới hạn MST":"Không giới hạn khi dùng thử"},
                {"Gói":"Kế toán Nội bộ","Giá VNĐ/tháng":69000,"Giới hạn dòng tải":"Không giới hạn","Giới hạn MST":1},
                {"Gói":"Kế toán Dịch vụ","Giá VNĐ/tháng":249000,"Giới hạn dòng tải":"Không giới hạn","Giới hạn MST":"Nhiều MST"},
            ]),
            "MST_mau": pd.DataFrame([{"Mã số thuế":"0101234567","Tên doanh nghiệp":"Công ty mẫu A"},{"Mã số thuế":"0107654321","Tên doanh nghiệp":"Công ty mẫu B"}])
        }
    raise ValueError("Không có bộ dữ liệu mẫu cho phân hệ này.")


def sample_xml_zip() -> bytes:
    today = date.today().isoformat()
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Invoice>
  <Seller><TenNBan>Cong ty nha cung cap A</TenNBan><MSTNBan>0101234567</MSTNBan></Seller>
  <Buyer><TenNMua>Cong ty khach hang mau</TenNMua><MSTNMua>0109999999</MSTNMua></Buyer>
  <SHDon>000001</SHDon><NLap>{today}</NLap>
  <TgTCThue>1363636</TgTCThue><TgTThue>136364</TgTThue><TgTTTBSo>1500000</TgTTTBSo>
  <DSign>DEMO_SIGNATURE_PLACEHOLDER_NOT_A_REAL_SIGNATURE</DSign>
</Invoice>"""
    output=io.BytesIO()
    with zipfile.ZipFile(output,"w",zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("HoaDon_MAU_000001.xml",xml)
    return output.getvalue()


def sample_legal_questions() -> bytes:
    text = """CÂU HỎI MẪU CHO CỐ VẤN KẾ TOÁN – KHÔNG PHẢI VĂN BẢN PHÁP LUẬT

1. Hồ sơ chứng từ cần lưu khi thanh toán chi phí vận chuyển gồm những gì?
2. Cần kiểm tra điều kiện nào trước khi hạch toán một khoản chi phí được trừ?
3. Hóa đơn đầu vào sai mã số thuế người mua cần xử lý và lưu hồ sơ ra sao?
4. Căn cứ nào xác định chế độ kế toán áp dụng cho doanh nghiệp trong kỳ?

Lưu ý: Đây chỉ là ví dụ câu hỏi. Hệ thống chỉ được kết luận khi kho RAG có văn bản chính thức còn hiệu lực, đúng thời điểm áp dụng và trích dẫn được điều/khoản phù hợp.
"""
    return text.encode("utf-8")
