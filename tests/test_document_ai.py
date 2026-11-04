import pandas as pd

from document_ai import (
    documents_to_dataframes,
    map_invoice_payments,
    normalize_invoice_number,
    normalize_tax_code,
    reconcile_invoice_registers,
)


def test_normalization_keeps_mst_branch_but_strips_leading_invoice_zeroes():
    assert normalize_invoice_number("00015388") == normalize_invoice_number("15388")
    assert normalize_invoice_number("HD 001") == "HD001"
    assert normalize_tax_code("0106536425-003") == "0106536425-003"
    assert normalize_tax_code("0106536425-003") != normalize_tax_code("0106536425-002")


def test_register_reconciliation_groups_tax_lines_and_matches_rounded_values():
    bookkeeping = pd.DataFrame([
        {"Số hóa đơn": "00015388", "Ngày hóa đơn": "2026-10-01", "Tên người bán": "Công ty A",
         "Mã số thuế người bán": "0101234567", "Giá trị HHDV mua vào chưa có thuế": 100000,
         "Thuế GTGT": 8000},
        {"Số hóa đơn": "00015388", "Ngày hóa đơn": "2026-10-01", "Tên người bán": "Công ty A",
         "Mã số thuế người bán": "0101234567", "Giá trị HHDV mua vào chưa có thuế": 50000,
         "Thuế GTGT": 5000},
    ])
    tax_portal = pd.DataFrame([{
        "Số hóa đơn": "15388", "Ngày lập": "01/10/2026", "Tên người bán/Tên người xuất hàng": "Công ty A",
        "MST người bán/MST người xuất hàng": "0101234567", "Tổng tiền chưa thuế": 150000,
        "Tổng tiền thuế": 13000, "Tổng tiền thanh toán": 163000.04
    }])
    result, counts = reconcile_invoice_registers(bookkeeping, tax_portal, epsilon=1)
    assert len(result) == 1
    assert result.iloc[0]["Trạng thái"] == "Khớp hoàn toàn"
    assert counts["Khớp hoàn toàn"] == 1
    assert result.iloc[0]["Số dòng thuế bảng kê"] == 2


def test_register_reconciliation_does_not_merge_mst_branches():
    bookkeeping = pd.DataFrame([{
        "Số hóa đơn": "54638", "Ngày hóa đơn": "2026-09-01", "Tên người bán": "Chi nhánh Hải Phòng",
        "Mã số thuế người bán": "0106536425-003", "Giá trị HHDV mua vào chưa có thuế": 1000000,
        "Thuế GTGT": 0
    }])
    tax_portal = pd.DataFrame([{
        "Số hóa đơn": "54638", "Ngày lập": "01/09/2026", "Tên người bán/Tên người xuất hàng": "Chi nhánh Hưng Yên",
        "MST người bán/MST người xuất hàng": "0106536425-002", "Tổng tiền chưa thuế": 1000000,
        "Tổng tiền thuế": 0, "Tổng tiền thanh toán": 1000000
    }])
    result, _ = reconcile_invoice_registers(bookkeeping, tax_portal)
    assert result.iloc[0]["Trạng thái"] == "Nghi vấn sai MST/chi nhánh"


def test_payment_mapping_handles_explicit_reference_and_grouped_invoice_payment():
    invoices = pd.DataFrame([
        {"Số hóa đơn": "HD001", "MST người bán": "0101234567", "Tên người bán": "Công ty A",
         "Ngày hóa đơn": "2026-10-01", "Tổng thanh toán": 100000},
        {"Số hóa đơn": "HD002", "MST người bán": "0101234567", "Tên người bán": "Công ty A",
         "Ngày hóa đơn": "2026-10-02", "Tổng thanh toán": 200000},
    ])
    transactions = pd.DataFrame([{
        "Ngày giao dịch": "2026-10-03", "Chiều giao dịch": "debit", "Số tiền": 300000,
        "Tên đối tác": "Công ty A", "MST đối tác": "0101234567", "Nội dung": "Thanh toan HD001 HD002"
    }])
    invoice_result, mapping, tx_result = map_invoice_payments(invoices, transactions, epsilon=0)
    assert invoice_result["Trạng thái"].tolist() == ["Đủ thanh toán", "Đủ thanh toán"]
    assert len(mapping) == 2
    assert tx_result.iloc[0]["Trạng thái"] == "Đã phân bổ"


def test_documents_to_dataframes_preserves_source_and_confidence():
    inv, tx, summary = documents_to_dataframes([{
        "source_file": "scan.pdf", "document_type": "invoice", "document_confidence": 0.98,
        "document_summary": "Hóa đơn đầu vào", "warnings": [],
        "invoices": [{"seller_name": "Công ty A", "seller_tax_code": "0101234567-001",
                      "invoice_number": "0001", "invoice_date": "2026-10-01",
                      "net_amount": 100, "vat_amount": 10, "total_amount": 110,
                      "confidence": 0.96, "evidence": "Trang 1"}],
        "transactions": []
    }])
    assert inv.iloc[0]["MST người bán"] == "0101234567-001"
    assert inv.iloc[0]["Độ tin cậy"] == 0.96
    assert tx.empty
    assert summary.iloc[0]["Tệp"] == "scan.pdf"
