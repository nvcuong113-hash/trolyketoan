from io import BytesIO
from zipfile import ZipFile
from openpyxl import load_workbook

from sample_data import sample_legal_questions, sample_workbook, sample_xml_zip


def test_each_operational_module_has_a_valid_downloadable_workbook():
    for module in ("fr01","fr02","fr03","fr04","fr05","fr06","fr08"):
        sheets=sample_workbook(module)
        assert sheets
        for name,frame in sheets.items():
            assert not frame.empty, (module,name)
        buffer=BytesIO()
        with __import__("pandas").ExcelWriter(buffer,engine="openpyxl") as writer:
            for name,frame in sheets.items():
                frame.to_excel(writer,index=False,sheet_name=name[:31])
        workbook=load_workbook(BytesIO(buffer.getvalue()),read_only=True)
        assert set(workbook.sheetnames)==set(sheets)


def test_invoice_sample_zip_contains_well_formed_xml_and_is_explicitly_demo():
    payload=sample_xml_zip()
    with ZipFile(BytesIO(payload)) as archive:
        names=archive.namelist()
        assert len(names)==1 and names[0].endswith(".xml")
        xml=archive.read(names[0]).decode("utf-8")
        assert "<DSign>DEMO_SIGNATURE_PLACEHOLDER_NOT_A_REAL_SIGNATURE</DSign>" in xml
        assert "<MSTNMua>0109999999</MSTNMua>" in xml


def test_legal_sample_is_questions_not_fabricated_law():
    sample=sample_legal_questions().decode("utf-8")
    assert "KHÔNG PHẢI VĂN BẢN PHÁP LUẬT" in sample
    assert "văn bản chính thức còn hiệu lực" in sample
