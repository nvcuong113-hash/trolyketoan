import time
import pandas as pd
from core import reconcile_bank


def test_reconcile_five_thousand_rows_performance_target():
    count=5000
    invoices=pd.DataFrame({
        "Tên khách hàng":[f"Cong ty ABC{i:05d}" for i in range(count)],
        "Mã khách hàng":[f"ABC{i:05d}" for i in range(count)],
        "Số hóa đơn":[f"HD{i:05d}" for i in range(count)],
        "Số tiền phải thu":[100000+i for i in range(count)],
    })
    bank=pd.DataFrame({
        "Ngày giao dịch":["03/11/2026"]*count,
        "Số tiền ghi Có":[100000+i for i in range(count)],
        "Nội dung giao dịch":[f"ABC{i:05d} thanh toan HD{i:05d}" for i in range(count)],
    })
    started=time.perf_counter()
    matched,unmatched=reconcile_bank(bank,invoices,epsilon=0)
    elapsed=time.perf_counter()-started
    assert len(matched)==count
    assert unmatched.empty
    assert elapsed < 3.0, f"5,000-line reconciliation took {elapsed:.2f}s"
