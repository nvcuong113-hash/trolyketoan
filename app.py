from __future__ import annotations
import gc, time
from datetime import datetime
import pandas as pd
import streamlit as st
from core import aging_report, build_staging, cashflow_summary, classify_invoice_payments, excel_bytes, mask_sensitive, parse_invoice_upload, pre_audit, read_tabular_bytes, reconcile_bank

st.set_page_config(page_title="Trợ lý Kế toán SME", page_icon="🧾", layout="wide")
TTL=600

def clear_data():
    for k in list(st.session_state):
        if k.startswith(("data_","result_")):
            del st.session_state[k]
    gc.collect()

now=time.time()
if now-st.session_state.get("_last_activity",now)>TTL:
    clear_data()
    st.toast("Phiên quá 10 phút không hoạt động; dữ liệu tạm đã được xóa.",icon="🧹")
st.session_state["_last_activity"]=now

def read_upload(f, aliases=()):
    return read_tabular_bytes(f.name,f.getvalue(),aliases) if f else None

def export(label,sheets,name,key):
    st.download_button(label,excel_bytes(sheets),file_name=name,mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",key=key,use_container_width=True)

st.title("🧾 Trợ lý Kế toán SME")
st.caption("Xử lý file kế toán trong phiên · Không lưu trữ sổ sách khách hàng")
with st.sidebar:
    st.info("Dữ liệu nghiệp vụ xử lý trong bộ nhớ phiên. Tự kiểm tra thời gian không hoạt động: 10 phút.")
    if st.button("🧹 Xóa dữ liệu và làm mới",use_container_width=True):
        clear_data()
        st.rerun()

tabs=st.tabs(["Tổng quan","01 · Đối soát","02 · Hóa đơn","03 · Xuất dữ liệu","04 · Tuổi nợ","05 · Pre-audit","06 · Báo cáo","07 · Cố vấn thuế","08 · Gói dịch vụ"])
with tabs[0]:
    st.subheader("Trung tâm nghiệp vụ kế toán")
    a,b,c,d=st.columns(4)
    a.metric("Phân hệ","08"); b.metric("Định dạng","XLSX · CSV · XML · ZIP"); c.metric("Lưu sổ sách","Không"); d.metric("Tự kiểm tra phiên","10 phút")
    st.markdown("**Quy trình:** Đối soát sao kê → kiểm soát hóa đơn → chuẩn hóa và xuất dữ liệu → theo dõi tuổi nợ → pre-audit → báo cáo dòng tiền.")
    st.warning("Kết quả tự động là gợi ý. Kế toán phải kiểm tra chứng từ gốc trước khi ghi sổ hoặc kê khai.")
    sample={"Sao_ke":pd.DataFrame([{"Ngày giao dịch":"03/11/2026","Số tiền ghi Có":1500000,"Số tiền ghi Nợ":0,"Nội dung giao dịch":"CTY ABC thanh toan HD001"}]),"Cong_no":pd.DataFrame([{"Mã khách hàng":"ABC","Tên khách hàng":"CTY ABC","Số hóa đơn":"HD001","Ngày hóa đơn":"01/10/2026","Số tiền phải thu":1500000,"Ngày đến hạn":"15/10/2026"}])}
    export("⬇ Tải bộ file mẫu",sample,"Bo_file_mau_TroLyKeToan.xlsx","demo")

with tabs[1]:
    st.subheader("Đối soát sao kê ngân hàng & ghép hóa đơn trả gộp")
    l,r=st.columns(2)
    bank=l.file_uploader("Sao kê ngân hàng",["xlsx","xls","csv"],key="bank")
    ar=r.file_uploader("Danh sách công nợ phải thu",["xlsx","xls","csv"],key="ar")
    threshold=st.slider("Ngưỡng nhận diện đối tác",0.6,1.0,0.8,0.01)
    epsilon=st.number_input("Sai số (VNĐ)",0,100000,1000,100)
    if st.button("🔎 Thực hiện đối soát",type="primary",disabled=not(bank and ar)):
        try:
            result,unmatched=reconcile_bank(read_upload(bank,["Ngày giao dịch","Số tiền ghi Có","Nội dung giao dịch"]),read_upload(ar,["Tên khách hàng","Số hóa đơn","Số tiền phải thu"]),threshold,int(epsilon))
            st.session_state["data_reconcile"]=result; st.session_state["data_unmatched"]=unmatched
        except Exception as e: st.error(str(e))
    result=st.session_state.get("data_reconcile")
    if result is not None:
        a,b,c=st.columns(3)
        a.metric("Khớp 1-1",int((result["Kết quả"]=="Khớp 1-1").sum()))
        b.metric("Khớp trả gộp",int((result["Kết quả"]=="Khớp trả gộp (1-N)").sum()))
        c.metric("Chưa xác định",int((result["Kết quả"]=="Chưa xác định").sum()))
        for label in ["Khớp 1-1","Khớp trả gộp (1-N)","Chưa xác định"]:
            with st.expander(label,expanded=label=="Chưa xác định"):
                st.dataframe(result[result["Kết quả"]==label],use_container_width=True,hide_index=True)
        st.markdown("**Hóa đơn chưa ghép**")
        st.dataframe(st.session_state["data_unmatched"],use_container_width=True,hide_index=True)
        export("⬇ Tải kết quả đối soát",{"Ket_qua":result,"Hoa_don_chua_ghep":st.session_state["data_unmatched"]},"Ket_qua_doi_soat.xlsx","reconcile_export")

with tabs[2]:
    st.subheader("Kiểm soát hóa đơn XML & nợ chứng từ")
    tax=st.text_input("Mã số thuế doanh nghiệp",key="co_tax")
    company=st.text_input("Tên doanh nghiệp theo đăng ký",key="co_name")
    xml=st.file_uploader("Tải XML hoặc ZIP hóa đơn",["xml","zip"],key="xml")
    pay=st.file_uploader("Nhật ký thanh toán (không bắt buộc)",["xlsx","xls","csv"],key="payments")
    if st.button("🧾 Kiểm tra hóa đơn",type="primary",disabled=not xml):
        try:
            inv=parse_invoice_upload(xml.name,xml.getvalue(),tax,company)
            if pay: inv=classify_invoice_payments(inv,read_upload(pay,["Số tiền ghi Nợ","Nội dung"]))
            st.session_state["data_invoices"]=inv
        except Exception as e: st.error(str(e))
    inv=st.session_state.get("data_invoices")
    if inv is not None:
        st.dataframe(inv,use_container_width=True,hide_index=True)
        export("⬇ Tải danh sách hóa đơn",{"Hoa_don":inv},"Kiem_tra_hoa_don.xlsx","invoice_export")
    st.warning("Chỉ kiểm tra cấu trúc/trường nhận diện được; chưa xác thực mật mã chữ ký số hoặc tra cứu trạng thái hóa đơn tại cơ quan Thuế.")

with tabs[3]:
    st.subheader("Nhập một lần – xuất nhiều định dạng")
    source=st.file_uploader("Tải nhật ký thu-chi",["xlsx","xls","csv"],key="staging_file")
    standard=st.selectbox("Chế độ tham chiếu",["133","200"],format_func=lambda x:"Thông tư "+x)
    if st.button("Chuẩn hóa dữ liệu",disabled=not source):
        try:
            st.session_state["data_staging"]=build_staging(read_upload(source,["Ngày giao dịch","Tiền vào","Tiền ra","Nội dung"]),standard)
            st.session_state.pop("data_exports",None)
        except Exception as e: st.error(str(e))
    staging=st.session_state.get("data_staging")
    if staging is not None:
        edited=st.data_editor(staging,use_container_width=True,num_rows="dynamic",key="staging_editor")
        st.session_state["data_staging"]=edited
        if st.button("📦 Tạo hai file Excel",type="primary"):
            internal=edited.copy()
            imported=edited[~edited["Chưa đủ chứng từ"].fillna(False).astype(bool)].copy()
            st.session_state["data_exports"]={"internal":internal,"import":imported}
        exports=st.session_state.get("data_exports")
        if exports:
            a,b=st.columns(2)
            with a:
                a.metric("Dòng báo cáo nội bộ",len(exports["internal"]))
                export("⬇ Báo cáo nội bộ",{"Bao_cao_thu_chi":exports["internal"]},"Bao_Cao_Thu_Chi_Noi_Bo.xlsx","internal_export")
            with b:
                b.metric("Dòng file import",len(exports["import"]))
                export("⬇ File import kế toán",{"Import_ke_toan":exports["import"]},"Import_MISA_VACOM.xlsx","import_export")
    st.info("File import là staging tổng quát; cần mẫu import chính thức theo phiên bản MISA/VACOM để ánh xạ chính xác.")

with tabs[4]:
    st.subheader("Quản trị tuổi nợ & tin nhắn nhắc việc")
    f=st.file_uploader("Danh sách công nợ có ngày đến hạn",["xlsx","xls","csv"],key="aging_file")
    if st.button("Tính tuổi nợ",disabled=not f):
        try: st.session_state["data_aging"]=aging_report(read_upload(f,["Ngày đến hạn","Tên khách hàng","Số tiền"]))
        except Exception as e: st.error(str(e))
    aging=st.session_state.get("data_aging")
    if aging is not None:
        counts=aging["Mức độ"].value_counts()
        cols=st.columns(4)
        for col,label in zip(cols,["Sắp đến hạn","Quá hạn nhẹ","Quá hạn nghiêm trọng","Chưa đến hạn"]): col.metric(label,int(counts.get(label,0)))
        st.dataframe(aging[["Đối tác","Số hóa đơn","Số tiền","Ngày đến hạn chuẩn","Số ngày quá hạn","Mức độ"]],use_container_width=True,hide_index=True)
        for i,row in aging.iterrows():
            with st.expander(f"{row['Đối tác']} · {row['Mức độ']} · {row['Số tiền']:,.0f} VNĐ"):
                st.text_area("Nhắc khách trả công nợ",row["Tin nhắn nhắc công nợ"],key=f"ar_{i}")
                st.text_area("Yêu cầu NCC gửi hóa đơn",row["Tin nhắn yêu cầu hóa đơn"],key=f"ap_{i}")
                st.text_area("Nhắc nhân viên hoàn ứng",row["Tin nhắn nhắc hoàn ứng"],key=f"staff_{i}")
        export("⬇ Tải báo cáo tuổi nợ",{"Tuoi_no":aging},"Bao_cao_tuoi_no.xlsx","aging_export")

with tabs[5]:
    st.subheader("Kiểm toán nhanh trước kỳ thuế")
    journal=st.file_uploader("Sổ Nhật ký chung",["xlsx","xls","csv"],key="journal")
    register=st.file_uploader("Bảng kê hóa đơn mua vào (không bắt buộc)",["xlsx","xls","csv"],key="register")
    opening=st.number_input("Số dư đầu kỳ TK 1111",min_value=0.0,step=100000.0)
    if st.button("🩺 Chạy kiểm tra",type="primary",disabled=not journal):
        try:
            j=read_upload(journal,["Ngày hạch toán","Tài khoản Nợ","Tài khoản Có","Số tiền"])
            reg=read_upload(register,["Tiền thuế","Thuế GTGT"]) if register else None
            st.session_state["data_audit"]=pre_audit(j,reg,opening)
        except Exception as e: st.error(str(e))
    audit=st.session_state.get("data_audit")
    if audit is not None:
        if audit.empty: st.success("Không phát hiện cảnh báo theo quy tắc hiện có.")
        else: st.warning(f"{len(audit)} điểm cần rà soát."); st.dataframe(audit,use_container_width=True,hide_index=True)
        export("⬇ Tải báo cáo Pre-audit",{"Canh_bao":audit},"Bao_cao_Pre_Audit.xlsx","audit_export")
    st.caption("Kiểm tra sơ bộ, không thay thế kiểm toán hoặc kết luận nghĩa vụ thuế.")

with tabs[6]:
    st.subheader("Báo cáo quản trị nhanh cho Giám đốc")
    f=st.file_uploader("Dữ liệu thu-chi ngày/tháng",["xlsx","xls","csv"],key="report_file")
    opening=st.number_input("Số dư đầu kỳ (VNĐ)",min_value=0.0,step=100000.0,key="report_opening")
    if st.button("Tổng hợp báo cáo",disabled=not f):
        try: st.session_state["data_cashflow"]=cashflow_summary(read_upload(f,["Số tiền ghi Có","Số tiền ghi Nợ","Tiền vào","Tiền ra"]),opening)
        except Exception as e: st.error(str(e))
    summary=st.session_state.get("data_cashflow")
    if summary:
        a,b,c,d=st.columns(4)
        a.metric("Tổng thu",f"{summary['Tổng thu']:,.0f} đ"); b.metric("Tổng chi",f"{summary['Tổng chi']:,.0f} đ")
        c.metric("Dòng tiền thuần",f"{summary['Dòng tiền thuần']:,.0f} đ"); d.metric("Tồn cuối kỳ",f"{summary['Tồn cuối kỳ']:,.0f} đ")
        message=(f"📊 BÁO CÁO DÒNG TIỀN\n💰 Tồn đầu kỳ: {summary['Tồn đầu kỳ']:,.0f} VNĐ\n📥 Tổng thu: {summary['Tổng thu']:,.0f} VNĐ\n📤 Tổng chi: {summary['Tổng chi']:,.0f} VNĐ\n📈 Dòng tiền thuần: {summary['Dòng tiền thuần']:,.0f} VNĐ\n🏦 Tồn cuối kỳ: {summary['Tồn cuối kỳ']:,.0f} VNĐ\n\nBáo cáo được tạo tự động bởi Trợ lý Kế toán SME.")
        st.text_area("Nội dung gửi Zalo cho Giám đốc",message,height=180)
        export("⬇ Tải báo cáo dòng tiền",{"Tong_hop":pd.DataFrame([summary])},"Bao_cao_dong_tien.xlsx","cashflow_export")
    st.info("Báo cáo P&L tháng cần sổ chi tiết doanh thu, giá vốn, chi phí và công nợ có cấu trúc; chưa thể suy ra chính xác từ sao kê đơn thuần.")

with tabs[7]:
    st.subheader("Cố vấn nghiệp vụ & pháp lý thuế có kiểm chứng")
    st.warning("Chưa cấu hình kho RAG văn bản pháp luật chính thức; ứng dụng không tạo kết luận thuế giả lập.")
    question=st.text_area("Nhập câu hỏi nghiệp vụ")
    if st.button("Ẩn danh hóa nội dung",disabled=not question.strip()):
        st.session_state["result_masked"]=mask_sensitive(question)
    if st.session_state.get("result_masked"):
        st.text_area("Nội dung sau khi che chuỗi định danh",st.session_state["result_masked"],height=120)
        st.caption("Chưa gửi dữ liệu ra nhà cung cấp AI. Regex không bảo đảm loại bỏ mọi dữ liệu cá nhân/bí mật.")
    st.markdown("Để bật RAG cần nạp văn bản chính thức, lưu số hiệu, điều/khoản, ngày hiệu lực, văn bản sửa đổi/thay thế và trích nguyên văn đoạn nguồn.")
    st.link_button("Tra cứu văn bản pháp luật Chính phủ","https://vanban.chinhphu.vn/")

with tabs[8]:
    st.subheader("Tài khoản, gói cước & thanh toán")
    st.dataframe(pd.DataFrame([
        {"Gói":"Khách dùng thử","Giá":"0 VNĐ","Giới hạn":"50 dòng tải xuống"},
        {"Gói":"Kế toán Nội bộ","Giá":"69.000 VNĐ/tháng","Giới hạn":"Không giới hạn dòng · 01 MST"},
        {"Gói":"Kế toán Dịch vụ","Giá":"249.000 VNĐ/tháng","Giới hạn":"Không giới hạn dòng · nhiều MST"}]),hide_index=True,use_container_width=True)
    st.warning("Chưa bật đăng nhập, cơ sở dữ liệu thuê bao, QR thanh toán và webhook. Cần thông tin merchant, tài khoản nhận tiền, webhook secret và nhà cung cấp trước khi kết nối thanh toán thật.")
    st.caption("Không nhập mật khẩu ngân hàng, khóa bí mật hoặc thông tin thẻ tại đây.")

st.divider()
st.caption("TroLyKeToan · MVP theo SRS v1.0 · Mọi kết quả cần kế toán kiểm tra trước khi ghi sổ/kê khai.")
