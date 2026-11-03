from __future__ import annotations

import io
from typing import Any

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def management_report_pdf(cashflow: dict[str, Any], pnl: dict[str, Any] | None = None,
                         brand: str = "Trợ lý Kế toán SME") -> bytes:
    """Render a one-page Vietnamese management snapshot entirely in memory."""
    required = ("Tồn đầu kỳ", "Tổng thu", "Tổng chi", "Dòng tiền thuần", "Tồn cuối kỳ")
    missing = [key for key in required if key not in cashflow]
    if missing:
        raise ValueError("Thiếu chỉ tiêu dòng tiền: " + ", ".join(missing))
    values = [float(cashflow[key]) for key in required]
    if any(not __import__("math").isfinite(value) for value in values):
        raise ValueError("Báo cáo có số liệu không hợp lệ.")

    plt.rcParams["font.family"] = "DejaVu Sans"
    fig = plt.figure(figsize=(11.7, 8.3), constrained_layout=False)
    fig.suptitle("BÁO CÁO QUẢN TRỊ DÒNG TIỀN", fontsize=18, fontweight="bold", y=0.96)
    fig.text(0.5, 0.915, brand, ha="center", fontsize=10)

    labels = ["Tổng thu", "Tổng chi", "Dòng tiền thuần"]
    amounts = [float(cashflow["Tổng thu"]), -float(cashflow["Tổng chi"]), float(cashflow["Dòng tiền thuần"])]
    ax = fig.add_axes([0.10, 0.55, 0.80, 0.27])
    ax.barh(labels, amounts)
    ax.axvline(0, linewidth=0.8)
    ax.set_title("Thu – chi trong kỳ (VNĐ)")
    ax.set_xlabel("Giá trị (VNĐ)")
    ax.ticklabel_format(axis="x", style="plain")
    ax.grid(axis="x", alpha=0.2)

    metric_ax = fig.add_axes([0.08, 0.17, 0.40, 0.27])
    metric_ax.axis("off")
    metric_rows = [
        ["Chỉ tiêu", "Giá trị (VNĐ)"],
        ["Tồn đầu kỳ", f"{cashflow['Tồn đầu kỳ']:,.0f}"],
        ["Tổng thu", f"{cashflow['Tổng thu']:,.0f}"],
        ["Tổng chi", f"{cashflow['Tổng chi']:,.0f}"],
        ["Dòng tiền thuần", f"{cashflow['Dòng tiền thuần']:,.0f}"],
        ["Tồn cuối kỳ", f"{cashflow['Tồn cuối kỳ']:,.0f}"],
    ]
    table = metric_ax.table(cellText=metric_rows[1:], colLabels=metric_rows[0],
                            cellLoc="left", colLoc="left", loc="center")
    table.auto_set_font_size(False)
    table.set_fontsize(9)
    table.scale(1.0, 1.55)

    pnl_ax = fig.add_axes([0.54, 0.17, 0.38, 0.27])
    pnl_ax.axis("off")
    if pnl:
        pnl_rows = [
            ["Chỉ tiêu P&L ước tính", "Giá trị (VNĐ)"],
            ["Doanh thu thuần", f"{pnl.get('Doanh thu thuần', pnl.get('Doanh thu', 0)):,.0f}"],
            ["Giá vốn", f"{pnl.get('Giá vốn', 0):,.0f}"],
            ["Chi phí", f"{pnl.get('Tổng chi phí', pnl.get('Chi phí', 0)):,.0f}"],
            ["Lợi nhuận ước tính", f"{pnl.get('Lợi nhuận ước tính', 0):,.0f}"],
        ]
        ptable = pnl_ax.table(cellText=pnl_rows[1:], colLabels=pnl_rows[0],
                              cellLoc="left", colLoc="left", loc="center")
        ptable.auto_set_font_size(False)
        ptable.set_fontsize(9)
        ptable.scale(1.0, 1.75)
    else:
        pnl_ax.text(0.0, 0.75, "Chưa có dữ liệu P&L trong phiên.", fontsize=10)
        pnl_ax.text(0.0, 0.55, "Tải sổ chi tiết để lập báo cáo lãi lỗ ước tính.", fontsize=9)

    fig.text(0.5, 0.07, "Báo cáo được tạo tự động bởi Trợ lý Kế toán SME · Số liệu cần được kế toán đối chiếu chứng từ gốc.",
             ha="center", fontsize=8)
    fig.text(0.5, 0.045, "Báo cáo quản trị tham khảo, không thay thế báo cáo tài chính hoặc tờ khai thuế.",
             ha="center", fontsize=7)

    output = io.BytesIO()
    fig.savefig(output, format="pdf", bbox_inches="tight")
    plt.close(fig)
    return output.getvalue()
