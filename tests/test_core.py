import io
import zipfile
from datetime import date
import pandas as pd
from core import aging_report, build_staging, cashflow_summary, classify_invoice_payments, mask_sensitive, parse_invoice_upload, parse_money, pre_audit, reconcile_bank, subset_sum_indices

def test_parse_money_vietnamese_formats():
    assert parse_money("1.234.567") == 1234567
    assert parse_money("1,234,567") == 1234567
    assert parse_money("1.234.567,50") == 1234567.5
    assert parse_money("1,234.50") == 1234.5

def test_subset_sum():
    chosen = subset_sum_indices([100,200,300,400],500,epsilon=0)
    assert chosen is not None and len(chosen) == 2
    assert sum([100,200,300,400][i] for i in chosen) == 500
    assert subset_sum_indices([100,200],900,epsilon=0) is None

def test_mask_sensitive_labeled_tax_and_phone():
    masked=mask_sensitive("SDT 0912345678, MST 0123456789, STK 123456789012")
    assert "[SDT]" in masked and "[MST]" in masked and "[STK_BANK]" in masked

def test_reconcile_exact_and_same_customer_subset_only():
    bank=pd.DataFrame([
        {"Ngày giao dịch":"01/11/2026","Số tiền ghi Có":300,"Nội dung giao dịch":"CTY ABC thanh toan HD001"},
        {"Ngày giao dịch":"02/11/2026","Số tiền ghi Có":300,"Nội dung giao dịch":"CTY ABC thanh toan cong no"}])
    invoices=pd.DataFrame([
        {"Tên khách hàng":"CTY ABC","Mã khách hàng":"ABC","Số hóa đơn":"HD001","Số tiền phải thu":300},
        {"Tên khách hàng":"CTY ABC","Mã khách hàng":"ABC","Số hóa đơn":"HD002","Số tiền phải thu":100},
        {"Tên khách hàng":"CTY ABC","Mã khách hàng":"ABC","Số hóa đơn":"HD003","Số tiền phải thu":200},
        {"Tên khách hàng":"CTY XYZ","Mã khách hàng":"XYZ","Số hóa đơn":"HD004","Số tiền phải thu":100}])
    result,unmatched=reconcile_bank(bank,invoices,epsilon=0)
    assert result.iloc[0]["Kết quả"]=="Khớp 1-1"
    assert result.iloc[1]["Kết quả"]=="Khớp trả gộp (1-N)"
    assert {"HD002","HD003"} <= set(result.iloc[1]["Hóa đơn"].split(", "))
    assert "HD004" in unmatched["Số hóa đơn"].tolist()

def test_aging_priority_boundaries():
    source=pd.DataFrame([
        {"Tên khách hàng":"A","Số hóa đơn":"1","Số tiền phải thu":10,"Ngày đến hạn":"03/11/2026"},
        {"Tên khách hàng":"B","Số hóa đơn":"2","Số tiền phải thu":20,"Ngày đến hạn":"02/11/2026"},
        {"Tên khách hàng":"C","Số hóa đơn":"3","Số tiền phải thu":30,"Ngày đến hạn":"18/10/2026"},
        {"Tên khách hàng":"D","Số hóa đơn":"4","Số tiền phải thu":40,"Ngày đến hạn":"01/10/2026"}])
    result=aging_report(source,date(2026,11,3))
    assert result["Mức độ"].tolist()==["Sắp đến hạn","Quá hạn nhẹ","Quá hạn nghiêm trọng","Quá hạn nghiêm trọng"]

def test_pre_audit_detects_negative_cash():
    journal=pd.DataFrame([
        {"Ngày hạch toán":"01/11/2026","Tài khoản Nợ":"642","Tài khoản Có":"1111","Số tiền":1000,"Diễn giải":"Chi"},
        {"Ngày hạch toán":"02/11/2026","Tài khoản Nợ":"1111","Tài khoản Có":"131","Số tiền":200,"Diễn giải":"Thu"}])
    assert (pre_audit(journal)["Nhóm kiểm tra"]=="Âm quỹ tiền mặt").any()

def test_cashflow_summary():
    result=cashflow_summary(pd.DataFrame([{"Số tiền ghi Có":"1.000","Số tiền ghi Nợ":"200"}]),500)
    assert result["Tồn cuối kỳ"]==1300

def test_xml_zip():
    xml=b'<Invoice><Seller><TenNBan>Cong ty ban</TenNBan><MSTNBan>123</MSTNBan></Seller><Buyer><TenNMua>Cong ty mua</TenNMua><MSTNMua>456</MSTNMua></Buyer><SHDon>001</SHDon><NLap>2026-11-01</NLap><TgTCThue>100</TgTCThue><TgTThue>10</TgTThue><TgTTTBSo>110</TgTTTBSo><DSign>signed</DSign></Invoice>'
    z=io.BytesIO()
    with zipfile.ZipFile(z,"w") as archive: archive.writestr("invoice.xml",xml)
    result=parse_invoice_upload("invoices.zip",z.getvalue(),"456","Cong ty mua")
    assert result.iloc[0]["Số hóa đơn"]=="001"
    assert result.iloc[0]["Trạng thái kiểm tra"]=="Không phát hiện lỗi cơ bản"

def test_staging_and_payment_matching():
    staging=build_staging(pd.DataFrame([{"Ngày":"01/11/2026","Tiền vào":1000,"Tiền ra":0,"Diễn giải":"Thu tiền khách"}]))
    assert staging.iloc[0]["Tài khoản Nợ"]=="1121"
    inv=pd.DataFrame([{"Tổng thanh toán":1000,"Tên người bán":"NCC A"}])
    pay=pd.DataFrame([{"Số tiền ghi Nợ":1000,"Nội dung":"Thanh toan NCC A"}])
    assert classify_invoice_payments(inv,pay).iloc[0]["Trạng thái thanh toán"]=="Đã tìm thấy khoản chi tương ứng"


def test_reconcile_does_not_auto_match_ambiguous_same_partner_same_amount():
    bank=pd.DataFrame([{"Ngày giao dịch":"03/11/2026","Số tiền ghi Có":1000,"Nội dung giao dịch":"CTY ABC thanh toan cong no"}])
    invoices=pd.DataFrame([
        {"Tên khách hàng":"CTY ABC","Mã khách hàng":"ABC","Số hóa đơn":"HD01","Số tiền phải thu":1000},
        {"Tên khách hàng":"CTY ABC","Mã khách hàng":"ABC","Số hóa đơn":"HD02","Số tiền phải thu":1000},
    ])
    result,unmatched=reconcile_bank(bank,invoices,epsilon=0)
    assert result.iloc[0]["Kết quả"]=="Chưa xác định"
    assert len(unmatched)==2
