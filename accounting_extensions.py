from __future__ import annotations

import io
import re
import uuid
from copy import copy
from datetime import date
from urllib.parse import urlencode

import pandas as pd
from openpyxl import load_workbook

from core import find_column, normalize_text, parse_money, similarity, subset_sum_indices


def reconcile_payables(invoices: pd.DataFrame, payments: pd.DataFrame, today: date | None = None,
                       epsilon: int = 1000, threshold: float = 0.80) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Match outgoing payments to supplier invoices, including unambiguous partial payments."""
    today = today or date.today()
    inv = invoices.copy().reset_index(drop=True)
    pay = payments.copy().reset_index(drop=True)
    inv_amount_col = find_column(inv.columns, ["Tổng thanh toán", "Số tiền phải trả", "Số tiền", "Amount"], required=True)
    inv_name_col = find_column(inv.columns, ["Tên người bán", "Nhà cung cấp", "Tên nhà cung cấp", "Đối tác"])
    inv_tax_col = find_column(inv.columns, ["MST người bán", "Mã số thuế người bán", "MST", "Tax code"])
    inv_no_col = find_column(inv.columns, ["Số hóa đơn", "Ký hiệu và số hóa đơn", "Invoice number"])
    inv_date_col = find_column(inv.columns, ["Ngày lập", "Ngày hóa đơn", "Issue date"])
    pay_amount_col = find_column(pay.columns, ["Số tiền ghi Nợ", "Tiền ra", "Số tiền chi", "Số tiền", "Amount"], required=True)
    pay_desc_col = find_column(pay.columns, ["Nội dung giao dịch", "Nội dung", "Diễn giải", "Description"])
    pay_date_col = find_column(pay.columns, ["Ngày giao dịch", "Ngày hạch toán", "Ngày", "Transaction date"])
    pay_tax_col = find_column(pay.columns, ["MST nhà cung cấp", "Mã số thuế", "Tax code"])
    pay_name_col = find_column(pay.columns, ["Tên nhà cung cấp", "Nhà cung cấp", "Đối tác"])
    inv_amounts = inv[inv_amount_col].map(parse_money).tolist()
    inv_names = inv[inv_name_col].fillna("").astype(str).tolist() if inv_name_col else [""]*len(inv)
    inv_taxes = inv[inv_tax_col].fillna("").astype(str).str.replace(r"\D","",regex=True).tolist() if inv_tax_col else [""]*len(inv)
    inv_numbers = inv[inv_no_col].fillna("").astype(str).tolist() if inv_no_col else [""]*len(inv)
    inv_dates = pd.to_datetime(inv[inv_date_col],errors="coerce",dayfirst=True).tolist() if inv_date_col else [pd.NaT]*len(inv)
    pay_amounts = pay[pay_amount_col].map(parse_money).tolist()
    pay_descs = pay[pay_desc_col].fillna("").astype(str).tolist() if pay_desc_col else [""]*len(pay)
    pay_dates = pd.to_datetime(pay[pay_date_col],errors="coerce",dayfirst=True).tolist() if pay_date_col else [pd.NaT]*len(pay)
    pay_taxes = pay[pay_tax_col].fillna("").astype(str).str.replace(r"\D","",regex=True).tolist() if pay_tax_col else [""]*len(pay)
    pay_names = pay[pay_name_col].fillna("").astype(str).tolist() if pay_name_col else [""]*len(pay)
    remaining = [max(0.0,x) for x in inv_amounts]
    paid = [0.0]*len(inv)
    last_payment = [""]*len(inv)
    used_payments = set()
    unallocated = []

    for pidx in range(len(pay)):
        amount = pay_amounts[pidx]
        if amount <= 0:
            continue
        desc_norm = normalize_text(pay_descs[pidx])
        desc_digits = re.sub(r"\D","",pay_descs[pidx])
        def partner_match(i):
            tax_match = bool(pay_taxes[pidx] and inv_taxes[i] and pay_taxes[pidx] == inv_taxes[i])
            partner_text = pay_names[pidx] or pay_descs[pidx]
            norm_partner = normalize_text(partner_text)
            name_match = bool(inv_names[i] and norm_partner and (
                normalize_text(inv_names[i]) in norm_partner or
                similarity(inv_names[i],partner_text) >= threshold
            ))
            return tax_match or name_match
        candidates = [i for i in range(len(inv)) if remaining[i] > epsilon and partner_match(i)]
        explicit = [i for i in candidates if inv_numbers[i] and normalize_text(inv_numbers[i]) in desc_norm]
        allocated = 0.0
        if len(explicit) == 1:
            i = explicit[0]
            allocated = min(amount,remaining[i])
            remaining[i] -= allocated; paid[i] += allocated
            last_payment[i] = str(pay_dates[pidx]) if pd.notna(pay_dates[pidx]) else ""
        elif len(explicit) > 1:
            allocated = 0.0
        else:
            exact = [i for i in candidates if abs(remaining[i]-amount) <= epsilon]
            subset = None
            if not exact and candidates and len(candidates) <= 80:
                subset = subset_sum_indices([remaining[i] for i in candidates],amount,epsilon)
            if len(exact) == 1:
                chosen = exact
            elif len(exact) > 1:
                chosen = []
            elif subset:
                chosen = [candidates[j] for j in subset]
            elif len(candidates) == 1 and amount < remaining[candidates[0]]-epsilon:
                chosen = candidates
            else:
                chosen = []
            for i in chosen:
                allocation = min(amount-allocated,remaining[i])
                if allocation <= 0:
                    continue
                remaining[i] -= allocation; paid[i] += allocation; allocated += allocation
                last_payment[i] = str(pay_dates[pidx]) if pd.notna(pay_dates[pidx]) else ""
        if allocated <= 0:
            unallocated.append((pidx,amount,"Không tìm thấy hóa đơn hoặc đối tác đủ căn cứ"))
        elif amount-allocated > epsilon:
            unallocated.append((pidx,amount-allocated,"Khoản chi còn dư chưa phân bổ vào hóa đơn"))
        else:
            used_payments.add(pidx)

    inv["Số đã thanh toán"] = [round(x,2) for x in paid]
    inv["Còn phải trả"] = [round(x,2) for x in remaining]
    inv["Ngày thanh toán gần nhất"] = last_payment
    inv["Trạng thái chứng từ"] = [
        "Đủ chứng từ" if remaining[i] <= epsilon else
        "Thanh toán một phần" if paid[i] > epsilon else
        "Hóa đơn treo"
        for i in range(len(inv))
    ]
    inv["Cảnh báo"] = [
        "Đã ghép đủ khoản chi" if remaining[i] <= epsilon else
        "Đã ghép một phần khoản chi; còn số dư phải trả" if paid[i] > epsilon else
        "Có hóa đơn nhưng chưa ghép được khoản chi"
        for i in range(len(inv))
    ]
    if unallocated:
        missing = pay.iloc[[x[0] for x in unallocated]].copy()
        missing["Số tiền chưa phân bổ"] = [round(x[1],2) for x in unallocated]
        missing["Số ngày từ ngày chi"] = [
            (pd.Timestamp(today)-pd.Timestamp(pay_dates[x[0]])).days if pd.notna(pay_dates[x[0]]) else None
            for x in unallocated
        ]
        missing["Trạng thái chứng từ"] = [
            "Thiếu hóa đơn (Nợ chứng từ)" if x[1] == pay_amounts[x[0]] else "Khoản chi chưa phân bổ hết"
            for x in unallocated
        ]
        missing["Cảnh báo"] = [x[2] for x in unallocated]
    else:
        missing = pd.DataFrame(columns=list(pay.columns)+["Số tiền chưa phân bổ","Số ngày từ ngày chi","Trạng thái chứng từ","Cảnh báo"])
    return inv, missing

def build_executive_pnl(journal: pd.DataFrame, receivables: pd.DataFrame | None = None,
                        payables: pd.DataFrame | None = None,
                        start_date: date | None = None, end_date: date | None = None) -> dict:
    debit_col = find_column(journal.columns, ["Tài khoản Nợ", "TK Nợ", "Debit account"], required=True)
    credit_col = find_column(journal.columns, ["Tài khoản Có", "TK Có", "Credit account"], required=True)
    amount_col = find_column(journal.columns, ["Số tiền", "Phát sinh", "Số phát sinh", "Amount"], required=True)
    work = journal.copy()
    if start_date and end_date and start_date > end_date:
        raise ValueError("Ngày bắt đầu kỳ P&L phải trước hoặc bằng ngày kết thúc.")
    if start_date or end_date:
        date_col = find_column(work.columns, ["Ngày hạch toán", "Ngày chứng từ", "Ngày", "Date"], required=True)
        work["_pnl_date"] = pd.to_datetime(work[date_col], errors="coerce", dayfirst=True)
        if start_date:
            work = work[work["_pnl_date"].dt.date >= start_date]
        if end_date:
            work = work[work["_pnl_date"].dt.date <= end_date]
    work["_debit"] = work[debit_col].fillna("").astype(str).str.strip()
    work["_credit"] = work[credit_col].fillna("").astype(str).str.strip()
    work["_amount"] = work[amount_col].map(parse_money)
    credit_sum = lambda prefix: work.loc[work["_credit"].str.startswith(prefix), "_amount"].sum()
    debit_sum = lambda prefix: work.loc[work["_debit"].str.startswith(prefix), "_amount"].sum()
    gross_revenue = credit_sum("511")
    deductions = debit_sum("521")
    net_revenue = gross_revenue - deductions
    cogs = debit_sum("632")
    payroll_detail = sum(debit_sum(p) for p in ("622", "627", "6411", "6421"))
    operating = debit_sum("641") + debit_sum("642")
    financial_net = debit_sum("635") - credit_sum("515")
    other_net = debit_sum("811") - credit_sum("711") + debit_sum("821")
    estimated_profit = net_revenue - cogs - operating - financial_net - other_net
    def top_debt(df, amount_aliases, name_aliases, label):
        if df is None or df.empty:
            return []
        amount_col = find_column(df.columns, amount_aliases)
        name_col = find_column(df.columns, name_aliases)
        if not amount_col:
            return []
        tmp = pd.DataFrame({
            "Đối tác": df[name_col].fillna("").astype(str) if name_col else "",
            "Số tiền": df[amount_col].map(parse_money)
        })
        return tmp.groupby("Đối tác", as_index=False)["Số tiền"].sum().sort_values("Số tiền", ascending=False).head(5).to_dict("records")
    return {
        "Doanh thu": float(gross_revenue), "Giảm trừ doanh thu": float(deductions),
        "Doanh thu thuần": float(net_revenue), "Giá vốn": float(cogs),
        "Chi phí nhân công chi tiết (tham khảo, đã nằm trong nhóm chi phí)": float(payroll_detail),
        "Chi phí bán hàng và quản lý": float(operating), "Chi phí tài chính thuần": float(financial_net),
        "Chi phí khác và thuế TNDN": float(other_net), "Lợi nhuận ước tính": float(estimated_profit),
        "Top 5 phải thu": top_debt(receivables, ["Còn phải thu", "Số tiền phải thu", "Số dư nợ", "Amount"], ["Tên khách hàng", "Khách hàng", "Đối tác"], "AR"),
        "Top 5 phải trả": top_debt(payables, ["Còn phải trả", "Số tiền phải trả", "Số dư có", "Amount"], ["Tên nhà cung cấp", "Nhà cung cấp", "Đối tác"], "AP"),
        "Ghi chú": "P&L ước tính theo phát sinh tài khoản; cần rà soát bút toán kết chuyển, phân loại chi phí và kỳ báo cáo."
    }


def advanced_pre_audit(journal: pd.DataFrame, trial_balance: pd.DataFrame | None = None,
                       invoice_register: pd.DataFrame | None = None, opening_cash: float = 0.0,
                       period_end: date | None = None, cash_payment_threshold: int | None = None) -> pd.DataFrame:
    from core import pre_audit
    base = pre_audit(journal, invoice_register, opening_cash, cash_payment_threshold)
    findings = base.to_dict("records")
    date_col = find_column(journal.columns, ["Ngày hạch toán", "Ngày chứng từ", "Ngày", "Date"])
    debit_col = find_column(journal.columns, ["Tài khoản Nợ", "TK Nợ", "Debit account"], required=True)
    credit_col = find_column(journal.columns, ["Tài khoản Có", "TK Có", "Credit account"], required=True)
    amount_col = find_column(journal.columns, ["Số tiền", "Phát sinh Nợ", "Số phát sinh", "Amount"], required=True)
    voucher_col = find_column(journal.columns, ["Số chứng từ", "Mã chứng từ", "Voucher number"])
    work = journal.copy()
    work["_debit"] = work[debit_col].fillna("").astype(str).str.strip()
    work["_credit"] = work[credit_col].fillna("").astype(str).str.strip()
    work["_amount"] = work[amount_col].map(parse_money)
    # Check 4: malformed/self-offsetting account pairs. This avoids assuming
    # that every credit to an asset or debit to a liability is erroneous.
    for idx, row in work.iterrows():
        d, cr = row["_debit"], row["_credit"]
        if d and cr and d == cr:
            findings.append({"Nhóm kiểm tra": "Định khoản cùng tài khoản", "Ngày": "",
                "Dòng dữ liệu": int(idx)+2, "Giá trị": row["_amount"],
                "Cảnh báo": f"TK Nợ và TK Có cùng là {d}; cần kiểm tra khả năng nhập nhầm"})
        for side, account in (("Nợ", d), ("Có", cr)):
            if account and not re.fullmatch(r"\d{3,8}", account):
                findings.append({"Nhóm kiểm tra": "Mã tài khoản bất thường", "Ngày": "",
                    "Dòng dữ liệu": int(idx)+2, "Giá trị": row["_amount"],
                    "Cảnh báo": f"Mã tài khoản {side} '{account}' không theo cấu trúc số phổ biến; cần đối chiếu hệ thống tài khoản doanh nghiệp"})
    # Check 5: repeated identical journal lines, not merely repeated voucher IDs
    # (a voucher may legitimately contain many accounting lines).
    desc_col = find_column(work.columns, ["Diễn giải", "Nội dung", "Description"])
    duplicate_keys = ["_debit", "_credit", "_amount"]
    if date_col:
        work["_audit_date"] = pd.to_datetime(work[date_col], errors="coerce", dayfirst=True).dt.strftime("%Y-%m-%d")
        duplicate_keys.append("_audit_date")
    if voucher_col:
        work["_audit_voucher"] = work[voucher_col].fillna("").astype(str).str.strip()
        duplicate_keys.append("_audit_voucher")
    if desc_col:
        work["_audit_desc"] = work[desc_col].fillna("").astype(str).str.strip()
        duplicate_keys.append("_audit_desc")
    duplicate = work[work.duplicated(duplicate_keys, keep=False)]
    for idx, row in duplicate.iterrows():
        findings.append({"Nhóm kiểm tra": "Dòng hạch toán trùng lặp", "Ngày": row.get("_audit_date", ""),
            "Dòng dữ liệu": int(idx)+2, "Giá trị": row["_amount"],
            "Cảnh báo": "Có dòng hạch toán giống nhau về ngày, tài khoản, số tiền và thông tin chứng từ; cần kiểm tra trùng nhập"})
    # Check 6: trial-balance debit/credit totals and potential unclosed P&L accounts.
    if trial_balance is not None and not trial_balance.empty:
        debit_total_col = find_column(trial_balance.columns, ["Tổng phát sinh Nợ", "Phát sinh Nợ", "Số dư Nợ", "Cộng Nợ"])
        credit_total_col = find_column(trial_balance.columns, ["Tổng phát sinh Có", "Phát sinh Có", "Số dư Có", "Cộng Có"])
        account_col = find_column(trial_balance.columns, ["Số hiệu tài khoản", "Tài khoản", "Mã tài khoản", "Account"], required=True)
        if debit_total_col and credit_total_col:
            debit_total = trial_balance[debit_total_col].map(parse_money).sum()
            credit_total = trial_balance[credit_total_col].map(parse_money).sum()
            if abs(debit_total-credit_total) > 1:
                findings.append({"Nhóm kiểm tra": "Bảng cân đối phát sinh không cân", "Ngày": "",
                    "Dòng dữ liệu": "", "Giá trị": debit_total-credit_total,
                    "Cảnh báo": f"Tổng Nợ {debit_total:,.0f} khác tổng Có {credit_total:,.0f}"})
        if period_end:
            accounts = trial_balance[account_col].fillna("").astype(str).str.strip()
            ending_debit_col = find_column(trial_balance.columns, ["Số dư cuối kỳ Nợ", "Dư Nợ cuối kỳ"])
            ending_credit_col = find_column(trial_balance.columns, ["Số dư cuối kỳ Có", "Dư Có cuối kỳ"])
            if ending_debit_col or ending_credit_col:
                debit_end = trial_balance[ending_debit_col].map(parse_money) if ending_debit_col else pd.Series(0, index=trial_balance.index)
                credit_end = trial_balance[ending_credit_col].map(parse_money) if ending_credit_col else pd.Series(0, index=trial_balance.index)
                pnl_open = trial_balance.loc[accounts.str.startswith(("511","521","632","635","641","642","811","711")) & ((debit_end+credit_end)>1)]
                close_dates = pd.to_datetime(work[date_col], errors="coerce", dayfirst=True) if date_col else pd.Series([], dtype="datetime64[ns]")
                close_mask = close_dates.dt.date.eq(period_end)
                has_911 = work.loc[close_mask, "_debit"].str.startswith("911").any() or work.loc[close_mask, "_credit"].str.startswith("911").any()
                if not pnl_open.empty and not has_911:
                    findings.append({"Nhóm kiểm tra": "Rà soát kết chuyển cuối kỳ", "Ngày": period_end,
                        "Dòng dữ liệu": "", "Giá trị": len(pnl_open),
                        "Cảnh báo": "Tài khoản doanh thu/chi phí còn số dư cuối kỳ nhưng chưa thấy bút toán liên quan TK 911 vào ngày kết thúc kỳ; cần xác nhận chế độ và quy trình kết chuyển"})
    return pd.DataFrame(findings, columns=["Nhóm kiểm tra", "Ngày", "Dòng dữ liệu", "Giá trị", "Cảnh báo"])


def map_staging_to_template(staging: pd.DataFrame, template_bytes: bytes,
                            transaction_type: str = "thu_tien_gui",
                            voucher_prefix: str | None = None,
                            voucher_start: int = 1) -> tuple[bytes, list[str]]:
    """Fill an uploaded MISA/VACOM template while retaining its worksheets and formatting."""
    if transaction_type not in {"thu_tien_gui", "chi_tien_gui", "chuyen_tien_noi_bo"}:
        raise ValueError("Loại chứng từ không hợp lệ. Chọn Thu tiền gửi, Chi tiền gửi hoặc Chuyển tiền nội bộ.")
    if voucher_start < 1:
        raise ValueError("Số chứng từ bắt đầu phải lớn hơn 0.")
    default_prefix = {"thu_tien_gui":"PTG","chi_tien_gui":"PCG","chuyen_tien_noi_bo":"CTNB"}[transaction_type]
    voucher_prefix = (voucher_prefix or default_prefix).strip()
    if not voucher_prefix or len(voucher_prefix) > 12:
        raise ValueError("Tiền tố số chứng từ không hợp lệ.")
    wb = load_workbook(io.BytesIO(template_bytes))
    aliases = {
        "ngay hach toan": ("Ngày hạch toán", "Ngày HT", "Ngày", "Ngày giao dịch", "Accounting date"),
        "ngay chung tu": ("Ngày chứng từ", "Ngày CT", "Ngày", "Ngày giao dịch", "Voucher date"),
        "so chung tu": ("Số chứng từ", "Mã chứng từ", "Số phiếu", "Voucher number"),
        "ma doi tuong": ("Mã đối tượng", "Mã khách hàng", "Mã nhà cung cấp", "Mã đối tượng THCP"),
        "ten doi tuong": ("Tên đối tượng", "Đối tác", "Tên khách hàng", "Nhà cung cấp"),
        "dien giai": ("Diễn giải", "Diễn giải chuẩn", "Diễn giải gốc", "Nội dung", "Description"),
        "tai khoan no": ("Tài khoản Nợ", "TK Nợ", "Tài khoản hạch toán Nợ"),
        "tai khoan co": ("Tài khoản Có", "TK Có", "Tài khoản hạch toán Có"),
        "so tien": ("Số tiền", "Số tiền thu", "Số tiền chi", "Tiền thu", "Tiền chi", "Số tiền giao dịch", "Số tiền quy đổi"),
        "tai khoan di": ("Tài khoản đi", "Tài khoản chuyển đi", "Tài khoản nguồn"),
        "tai khoan den": ("Tài khoản đến", "Tài khoản nhận", "Tài khoản chuyển đến"),
        "ma hoa don": ("Số hóa đơn", "Mã hóa đơn", "Invoice number"),
        "ma so thue": ("Mã số thuế", "MST", "Tax code"),
    }
    source_aliases = {
        "ngay hach toan": ("Ngày",), "ngay chung tu": ("Ngày",),
        "so chung tu": (), "ma doi tuong": ("Mã đối tượng",),
        "ten doi tuong": ("Đối tác",), "dien giai": ("Diễn giải chuẩn", "Diễn giải gốc"),
        "tai khoan no": ("Tài khoản Nợ",), "tai khoan co": ("Tài khoản Có",),
        "so tien": ("Tiền thu",) if transaction_type == "thu_tien_gui" else ("Tiền chi",) if transaction_type == "chi_tien_gui" else (),
        "tai khoan di": ("Tài khoản đi",), "tai khoan den": ("Tài khoản đến",),
        "ma hoa don": ("Mã hóa đơn",), "ma so thue": ("Mã số thuế",),
    }
    # MISA SME 2026 documents only these three mandatory mappings for bank receipts/payments.
    # Other mapped fields are retained when present in the user's actual template.
    required = {"ngay hach toan", "ngay chung tu", "so chung tu"}
    if transaction_type == "chuyen_tien_noi_bo":
        required.update({"tai khoan di","tai khoan den"})
    chosen_sheet, header_row, mapping, best_score = None, None, None, -1
    for ws in wb.worksheets:
        for row_idx in range(1, min(ws.max_row, 40)+1):
            raw_headers = {normalize_text(cell.value): cell.column for cell in ws[row_idx] if cell.value is not None}
            canonical = {}
            for key, names in aliases.items():
                for name in names:
                    norm = normalize_text(name)
                    if norm in raw_headers:
                        canonical[key] = raw_headers[norm]
                        break
            score = len(canonical)
            if score > best_score:
                chosen_sheet, header_row, mapping, best_score = ws, row_idx, canonical, score
    if chosen_sheet is None or not mapping or sum(1 for k in required if k in mapping) < len(required):
        raise ValueError("Không nhận diện được mẫu Excel hoặc thiếu các cột bắt buộc. Hãy tải đúng mẫu nhập khẩu cho loại chứng từ đã chọn từ phần mềm kế toán.")
    missing_headers = [k for k in required if k not in mapping]
    if missing_headers:
        raise ValueError("Mẫu thiếu cột bắt buộc: " + ", ".join(missing_headers))
    source_cols = {}
    for key, source_names in source_aliases.items():
        if source_names:
            col = find_column(staging.columns, source_names)
            if col:
                source_cols[key] = col
    amount_source = "Tiền thu" if transaction_type == "thu_tien_gui" else "Tiền chi"
    rows = staging.copy()
    type_col = find_column(rows.columns, ["Loại chứng từ"])
    expected_type = {"thu_tien_gui":"Thu tiền gửi","chi_tien_gui":"Chi tiền gửi","chuyen_tien_noi_bo":"Chuyển tiền nội bộ"}[transaction_type]
    if type_col:
        rows = rows[rows[type_col].fillna("").astype(str).str.strip().str.casefold() == expected_type.casefold()]
    if transaction_type == "chuyen_tien_noi_bo":
        amount_col = find_column(rows.columns, ["Số tiền giao dịch","Tiền thu","Tiền chi"])
        if amount_col:
            rows = rows[rows[amount_col].map(parse_money) > 0]
    elif amount_source in rows.columns:
        rows = rows[rows[amount_source].map(parse_money) > 0]
    rows = rows[~rows.get("Chưa đủ chứng từ", pd.Series(False, index=rows.index)).fillna(False).astype(bool)]
    # MISA SME imports bank receipts and payments as separate voucher types/files.
    style_source = chosen_sheet[header_row+1] if chosen_sheet.max_row >= header_row+1 else None
    for out_idx, (_, source) in enumerate(rows.iterrows(), start=header_row+1):
        for key, col_idx in mapping.items():
            if key not in aliases:
                continue
            cell = chosen_sheet.cell(out_idx, col_idx)
            if style_source:
                source_cell = style_source[col_idx-1] if col_idx-1 < len(style_source) else None
                if source_cell and source_cell.has_style:
                    cell._style = copy(source_cell._style)
            if key == "so chung tu":
                cell.value = f"{voucher_prefix}{voucher_start + out_idx-header_row-1:06d}"
            elif key in source_cols:
                cell.value = source[source_cols[key]]
            elif key in {"ngay hach toan", "ngay chung tu"}:
                cell.value = source.get("Ngày")
            elif key == "so tien":
                if transaction_type == "chuyen_tien_noi_bo":
                    cell.value = parse_money(source.get("Số tiền giao dịch",max(parse_money(source.get("Tiền thu",0)),parse_money(source.get("Tiền chi",0)))))
                else:
                    cell.value = parse_money(source.get(amount_source, 0))
    out = io.BytesIO()
    wb.save(out)
    special_mapped = {"so chung tu","ngay hach toan","ngay chung tu"}
    if transaction_type == "chuyen_tien_noi_bo" and find_column(staging.columns,["Số tiền giao dịch","Tiền thu","Tiền chi"]):
        special_mapped.add("so tien")
    return out.getvalue(), [aliases[k][0] for k in mapping if k in aliases and k not in source_cols and k not in special_mapped]


def build_sepay_qr_url(bank: str, account: str, amount: int, user_id: str,
                       account_holder: str = "", require_sevqr: bool = False) -> str:
    if not bank or not account or not user_id or amount <= 0:
        raise ValueError("Thiếu cấu hình ngân hàng hoặc thông tin thanh toán.")
    code = re.sub(r"[^A-Za-z0-9]", "", user_id).upper()
    memo = code if code.startswith("TKPRO") else f"TKPRO{code}"
    if require_sevqr:
        memo = f"SEVQR {memo}"
    params = {"acc": account, "bank": bank, "amount": str(int(amount)), "des": memo, "template": "compact"}
    if account_holder:
        params["holder"] = re.sub(r"[^A-Za-z0-9 ]", "", account_holder.upper())[:80]
        params["showinfo"] = "true"
    return "https://vietqr.app/img?" + urlencode(params)
