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


def test_payment_mapping_does_not_allocate_unknown_direction():
    invoices = pd.DataFrame([{
        "Số hóa đơn": "HD001", "MST người bán": "0101234567", "Tên người bán": "Công ty A",
        "Tổng thanh toán": 100000
    }])
    transactions = pd.DataFrame([{
        "Chiều giao dịch": "unknown", "Số tiền": 100000,
        "Tên đối tác": "Công ty A", "MST đối tác": "0101234567",
        "Nội dung": "Thanh toan HD001"
    }])
    invoice_result, mapping, tx_result = map_invoice_payments(invoices, transactions, epsilon=0)
    assert invoice_result.iloc[0]["Trạng thái"] == "Chưa tìm thấy thanh toán"
    assert mapping.empty
    assert tx_result.empty


def test_register_reconciliation_does_not_double_repeated_invoice_total_per_tax_line():
    bookkeeping = pd.DataFrame([
        {"Số hóa đơn": "00123", "Ký hiệu hóa đơn": "C26TAA", "Mã số thuế người bán": "0101234567",
         "Giá trị HHDV mua vào chưa có thuế": 100000, "Thuế GTGT": 8000, "Tổng thanh toán": 163000},
        {"Số hóa đơn": "00123", "Ký hiệu hóa đơn": "C26TAA", "Mã số thuế người bán": "0101234567",
         "Giá trị HHDV mua vào chưa có thuế": 50000, "Thuế GTGT": 5000, "Tổng thanh toán": 163000},
    ])
    tax_portal = pd.DataFrame([{
        "Số hóa đơn": "123", "Ký hiệu hóa đơn": "C26TAA", "MST người bán/MST người xuất hàng": "0101234567",
        "Tổng tiền chưa thuế": 150000, "Tổng tiền thuế": 13000, "Tổng tiền thanh toán": 163000
    }])
    result, _ = reconcile_invoice_registers(bookkeeping, tax_portal, epsilon=0)
    assert len(result) == 1
    assert result.iloc[0]["Trạng thái"] == "Khớp hoàn toàn"
    assert result.iloc[0]["Tổng thanh toán bảng kê"] == 163000


def test_register_reconciliation_flags_missing_tax_code_instead_of_dropping_invoice():
    bookkeeping = pd.DataFrame([{
        "Số hóa đơn": "9001", "Mã số thuế người bán": None,
        "Giá trị HHDV mua vào chưa có thuế": 100000, "Thuế GTGT": 8000
    }])
    tax_portal = pd.DataFrame([{
        "Số hóa đơn": "9001", "MST người bán/MST người xuất hàng": "0101234567",
        "Tổng tiền chưa thuế": 100000, "Tổng tiền thuế": 8000, "Tổng tiền thanh toán": 108000
    }])
    result, counts = reconcile_invoice_registers(bookkeeping, tax_portal)
    assert len(result) == 1
    assert result.iloc[0]["Trạng thái"] == "Thiếu MST – cần xác minh"
    assert counts["Thiếu MST – cần xác minh"] == 1


def test_register_reconciliation_uses_invoice_symbol_to_resolve_duplicate_numbers():
    bookkeeping = pd.DataFrame([{
        "Số hóa đơn": "001", "Ký hiệu hóa đơn": "C26AAA", "Mã số thuế người bán": "0101234567",
        "Giá trị HHDV mua vào chưa có thuế": 100000, "Thuế GTGT": 8000
    }])
    tax_portal = pd.DataFrame([
        {"Số hóa đơn": "1", "Ký hiệu hóa đơn": "C26BBB", "MST người bán/MST người xuất hàng": "0101234567",
         "Tổng tiền chưa thuế": 100000, "Tổng tiền thuế": 8000, "Tổng tiền thanh toán": 108000},
        {"Số hóa đơn": "1", "Ký hiệu hóa đơn": "C26AAA", "MST người bán/MST người xuất hàng": "0101234567",
         "Tổng tiền chưa thuế": 100000, "Tổng tiền thuế": 8000, "Tổng tiền thanh toán": 108000},
    ])
    result, _ = reconcile_invoice_registers(bookkeeping, tax_portal, epsilon=0)
    assert result.iloc[0]["Ký hiệu danh sách"] == "C26AAA"
    assert result.iloc[0]["Trạng thái"] == "Khớp hoàn toàn"


def _mock_gemini_response(payload):
    import json

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps({
                "steps": [{
                    "type": "model_output",
                    "content": [{"type": "text", "text": json.dumps(payload)}]
                }]
            }).encode("utf-8")

    return FakeResponse()


def test_extract_document_sends_pdf_inline_to_gemini_without_storage(monkeypatch):
    import base64
    import json
    import urllib.request
    from document_ai import extract_document

    captured = {}
    payload = {
        "document_type": "invoice", "document_confidence": 0.99,
        "document_summary": "Hóa đơn đầu vào", "invoices": [],
        "transactions": [], "warnings": []
    }

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return _mock_gemini_response(payload)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    raw = b"%PDF-1.4 test document"
    result = extract_document("invoice.pdf", raw, "gemini-test-key")

    request_body = json.loads(captured["request"].data.decode("utf-8"))
    part = request_body["input"][0]
    assert result["document_type"] == "invoice"
    assert result["source_file"] == "invoice.pdf"
    assert request_body["model"] == "gemini-3.5-flash-lite"
    assert request_body["store"] is False
    assert part["type"] == "document"
    assert part["mime_type"] == "application/pdf"
    assert part["data"] == base64.b64encode(raw).decode("ascii")
    assert request_body["response_format"]["mime_type"] == "application/json"
    assert captured["request"].get_header("X-goog-api-key") == "gemini-test-key"
    assert captured["timeout"] == 120


def test_extract_document_sends_image_inline_to_gemini(monkeypatch):
    import base64
    import json
    import urllib.request
    from document_ai import extract_document

    payload = {
        "document_type": "payment_proof", "document_confidence": 0.95,
        "document_summary": "Ủy nhiệm chi", "invoices": [],
        "transactions": [], "warnings": []
    }
    captured = {}

    def fake_urlopen(request, timeout):
        captured["request"] = request
        return _mock_gemini_response(payload)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    raw = b"png-bytes"
    result = extract_document("transfer.png", raw, "gemini-test-key", "gemini-3.5-flash-lite")

    request_body = json.loads(captured["request"].data.decode("utf-8"))
    part = request_body["input"][0]
    assert result["document_type"] == "payment_proof"
    assert part["type"] == "image"
    assert part["mime_type"] == "image/png"
    assert part["data"] == base64.b64encode(raw).decode("ascii")
    assert request_body["store"] is False


def test_normalize_bank_statement_supports_common_vietnamese_headers():
    from document_ai import normalize_bank_statement

    source = pd.DataFrame([{
        "Ngày GD": "04/11/2026", "Phát sinh Nợ": "1.250.000",
        "Phát sinh Có": 0, "Diễn giải": "Thanh toan HD001",
        "Mã GD": "TX0001", "Tên đơn vị": "Công ty A"
    }])
    result = normalize_bank_statement(source)
    assert len(result) == 1
    assert result.iloc[0]["Chiều giao dịch"] == "debit"
    assert result.iloc[0]["Số tiền"] == 1250000
    assert result.iloc[0]["Mã tham chiếu"] == "TX0001"
    assert result.iloc[0]["Nội dung"] == "Thanh toan HD001"


def test_normalize_bank_statement_does_not_guess_when_both_debit_and_credit_present():
    from document_ai import normalize_bank_statement

    source = pd.DataFrame([{
        "Ngày giao dịch": "04/11/2026", "Số tiền ghi Nợ": 1000,
        "Số tiền ghi Có": 2000, "Nội dung giao dịch": "Dòng bất thường"
    }])
    result = normalize_bank_statement(source)
    assert result.iloc[0]["Chiều giao dịch"] == "unknown"
