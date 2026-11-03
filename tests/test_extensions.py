from datetime import date
import io
import pandas as pd
from openpyxl import Workbook
from accounting_extensions import (
    advanced_pre_audit, build_executive_pnl, build_sepay_qr_url,
    map_staging_to_template, reconcile_payables,
)
from billing_api import parse_sepay_event, verify_hmac
import hashlib, hmac


def test_reconcile_payables_classifies_invoice_and_missing_payment():
    invoices=pd.DataFrame([
        {"Tên người bán":"NCC A","MST người bán":"0100000000","Số hóa đơn":"A01","Tổng thanh toán":1000},
        {"Tên người bán":"NCC B","MST người bán":"0200000000","Số hóa đơn":"B01","Tổng thanh toán":500},
    ])
    payments=pd.DataFrame([
        {"Ngày giao dịch":"01/11/2026","Số tiền ghi Nợ":1000,"Nội dung":"Thanh toan NCC A"},
        {"Ngày giao dịch":"02/11/2026","Số tiền ghi Nợ":700,"Nội dung":"Thanh toan NCC C"},
    ])
    inv,missing=reconcile_payables(invoices,payments,date(2026,11,3),epsilon=0)
    assert inv["Trạng thái chứng từ"].tolist()==["Đủ chứng từ","Hóa đơn treo"]
    assert len(missing)==1 and missing.iloc[0]["Trạng thái chứng từ"]=="Thiếu hóa đơn (Nợ chứng từ)"
    assert missing.iloc[0]["Số ngày từ ngày chi"]==1


def test_executive_pnl():
    journal=pd.DataFrame([
        {"Tài khoản Nợ":"1121","Tài khoản Có":"5111","Số tiền":1000},
        {"Tài khoản Nợ":"632","Tài khoản Có":"156","Số tiền":300},
        {"Tài khoản Nợ":"6421","Tài khoản Có":"334","Số tiền":100},
        {"Tài khoản Nợ":"6428","Tài khoản Có":"1121","Số tiền":50},
    ])
    result=build_executive_pnl(journal)
    assert result["Doanh thu thuần"]==1000
    assert result["Giá vốn"]==300
    assert result["Lợi nhuận ước tính"]==550


def test_sepay_webhook_event():
    event=parse_sepay_event({"id":123,"transferAmount":69000,"transferType":"in","content":"TKPROABC12345678"})
    assert event["transaction_id"]==123 and event["amount"]==69000
    try:
        parse_sepay_event({"id":123,"transferAmount":69000,"transferType":"out","content":"TKPROABC12345678"})
        assert False
    except ValueError:
        pass


def test_qr_vietinbank_sevqr_prefix():
    url=build_sepay_qr_url("VietinBank","0123456789",69000,"TKPROABC12345678",require_sevqr=True)
    assert "SEVQR" in url and "TKPRO" in url and "amount=69000" in url


def test_template_mapping_receipt():
    wb=Workbook()
    ws=wb.active
    ws.title="ThuTien"
    ws.append(["Ngày hạch toán","Ngày chứng từ","Số chứng từ","Diễn giải","Tài khoản Nợ","Tài khoản Có","Số tiền"])
    b=io.BytesIO(); wb.save(b)
    staging=pd.DataFrame([{"Ngày":pd.Timestamp("2026-11-03"),"Diễn giải chuẩn":"Thu khách hàng","Tài khoản Nợ":"1121","Tài khoản Có":"131","Tiền thu":1000,"Tiền chi":0,"Chưa đủ chứng từ":False}])
    data,missing=map_staging_to_template(staging,b.getvalue(),"thu_tien_gui")
    from openpyxl import load_workbook
    out=load_workbook(io.BytesIO(data),data_only=True)
    row=[out["ThuTien"].cell(2,i).value for i in range(1,8)]
    assert row[0] is not None and row[2]=="PTG000001" and row[-1]==1000


def test_advanced_pre_audit_trial_balance_mismatch():
    journal=pd.DataFrame([{"Ngày hạch toán":"31/12/2026","Tài khoản Nợ":"642","Tài khoản Có":"1121","Số tiền":100,"Diễn giải":"Chi"}])
    tb=pd.DataFrame([{"Tài khoản":"111","Tổng phát sinh Nợ":100,"Tổng phát sinh Có":50}])
    findings=advanced_pre_audit(journal,tb,period_end=date(2026,12,31))
    assert (findings["Nhóm kiểm tra"]=="Bảng cân đối phát sinh không cân").any()


def test_sepay_hmac_verification_and_replay_window():
    raw=b'{"id":1,"transferAmount":1000}'
    secret="test-secret"
    timestamp="1793700000"
    digest=hmac.new(secret.encode(),timestamp.encode()+b"."+raw,hashlib.sha256).hexdigest()
    signature="sha256="+digest
    assert verify_hmac(raw,signature,timestamp,secret,now=1793700000)
    assert not verify_hmac(raw,signature,timestamp,secret,now=1793701000)


def test_reconcile_payables_accumulates_partial_payments():
    invoices=pd.DataFrame([{"Tên người bán":"NCC A","Số hóa đơn":"A01","Tổng thanh toán":1000}])
    payments=pd.DataFrame([
        {"Ngày giao dịch":"01/11/2026","Số tiền ghi Nợ":400,"Nội dung":"Thanh toan NCC A"},
        {"Ngày giao dịch":"02/11/2026","Số tiền ghi Nợ":600,"Nội dung":"Thanh toan NCC A"},
    ])
    result,missing=reconcile_payables(invoices,payments,date(2026,11,3),epsilon=0)
    assert result.iloc[0]["Trạng thái chứng từ"]=="Đủ chứng từ"
    assert result.iloc[0]["Số đã thanh toán"]==1000
    assert result.iloc[0]["Còn phải trả"]==0
    assert missing.empty


def test_reconcile_payables_keeps_partial_balance_visible():
    invoices=pd.DataFrame([{"Tên người bán":"NCC A","Số hóa đơn":"A01","Tổng thanh toán":1000}])
    payments=pd.DataFrame([{"Ngày giao dịch":"01/11/2026","Số tiền ghi Nợ":400,"Nội dung":"Thanh toan NCC A"}])
    result,missing=reconcile_payables(invoices,payments,date(2026,11,3),epsilon=0)
    assert result.iloc[0]["Trạng thái chứng từ"]=="Thanh toán một phần"
    assert result.iloc[0]["Còn phải trả"]==600
    assert missing.empty


def test_pnl_respects_selected_accounting_period():
    journal=pd.DataFrame([
        {"Ngày hạch toán":"05/01/2026","Tài khoản Nợ":"1121","Tài khoản Có":"5111","Số tiền":100},
        {"Ngày hạch toán":"05/02/2026","Tài khoản Nợ":"1121","Tài khoản Có":"5111","Số tiền":200},
    ])
    result=build_executive_pnl(journal,start_date=date(2026,1,1),end_date=date(2026,1,31))
    assert result["Doanh thu"]==100


def test_pnl_rejects_reversed_date_range():
    journal=pd.DataFrame([{"Ngày hạch toán":"05/01/2026","Tài khoản Nợ":"1121","Tài khoản Có":"5111","Số tiền":100}])
    try:
        build_executive_pnl(journal,start_date=date(2026,2,1),end_date=date(2026,1,1))
        assert False
    except ValueError as exc:
        assert "Ngày bắt đầu" in str(exc)


def test_pre_audit_cash_threshold_is_configurable():
    from core import pre_audit
    journal=pd.DataFrame([{"Ngày hạch toán":"01/11/2026","Tài khoản Nợ":"642","Tài khoản Có":"1111","Số tiền":6000000,"Diễn giải":"Mua hàng hóa"}])
    no_threshold=pre_audit(journal,cash_payment_threshold=None)
    threshold=pre_audit(journal,cash_payment_threshold=5000000)
    assert not (no_threshold["Nhóm kiểm tra"]=="Rà soát thanh toán không dùng tiền mặt").any()
    assert (threshold["Nhóm kiểm tra"]=="Rà soát thanh toán không dùng tiền mặt").any()


def test_template_mapping_internal_transfer_accounts_and_amount():
    wb=Workbook()
    ws=wb.active
    ws.title="ChuyenTien"
    ws.append(["Ngày hạch toán","Ngày chứng từ","Số chứng từ","Tài khoản đi","Tài khoản đến","Số tiền"])
    b=io.BytesIO(); wb.save(b)
    staging=pd.DataFrame([{
        "Ngày":pd.Timestamp("2026-11-03"),"Loại chứng từ":"Chuyển tiền nội bộ",
        "Số tiền giao dịch":2500000,"Tiền thu":0,"Tiền chi":2500000,
        "Tài khoản đi":"1121","Tài khoản đến":"1122","Chưa đủ chứng từ":False,
    }])
    data,missing=map_staging_to_template(staging,b.getvalue(),"chuyen_tien_noi_bo","CTNB",5)
    from openpyxl import load_workbook
    out=load_workbook(io.BytesIO(data),data_only=True)
    row=[out["ChuyenTien"].cell(2,i).value for i in range(1,7)]
    assert row[2]=="CTNB000005"
    assert row[3]=="1121" and row[4]=="1122" and row[5]==2500000
    assert "Số tiền" not in missing


def test_sepay_uses_dedicated_code_field_when_content_omits_it():
    event=parse_sepay_event({
        "id":124,"transferAmount":69000,"transferType":"in",
        "code":"TKPROABC12345678","content":"chuyen tien"
    })
    assert "TKPROABC12345678" in event["content"]
