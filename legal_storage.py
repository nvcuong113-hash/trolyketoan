from __future__ import annotations

import hashlib
import re
from pathlib import Path
from datetime import date
from typing import Any
from urllib.parse import urlparse

MAX_LEGAL_FILE_BYTES = 20 * 1024 * 1024
ALLOWED_HOSTS = {"vanban.chinhphu.vn","vbpl.moj.gov.vn","mof.gov.vn","portal.mof.gov.vn","nif.mof.gov.vn","gdt.gov.vn"}
MIME_TYPES = {
    ".pdf":"application/pdf",
    ".docx":"application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt":"text/plain",
    ".md":"text/markdown",
}


def trusted_official_url(url: str) -> bool:
    parsed=urlparse(url or "")
    host=(parsed.hostname or "").lower()
    return parsed.scheme=="https" and (host in ALLOWED_HOSTS or any(
        host.endswith("."+domain) for domain in {"mof.gov.vn","gdt.gov.vn","moj.gov.vn","chinhphu.vn"}
    ))


def publish_legal_document(client, filename: str, content: bytes, metadata: dict[str, Any], uploaded_by: str):
    suffix=Path(filename).suffix.lower()
    if suffix not in MIME_TYPES:
        raise ValueError("Chỉ nhận PDF, DOCX, TXT hoặc MD.")
    if not content or len(content)>MAX_LEGAL_FILE_BYTES:
        raise ValueError("Tệp rỗng hoặc vượt quá giới hạn 20 MB.")
    if not trusted_official_url(metadata.get("source_url","")):
        raise ValueError("Nguồn phải là HTTPS thuộc cổng văn bản/cơ quan nhà nước được cho phép.")
    if metadata.get("status") not in {"Còn hiệu lực","Hết hiệu lực","Chưa xác minh"}:
        raise ValueError("Trạng thái văn bản không hợp lệ.")
    digest=hashlib.sha256(content).hexdigest()
    number=re.sub(r"[^A-Za-z0-9.-]+","_",metadata["number"]).strip("_")[:80]
    effective=re.sub(r"[^0-9-]","",metadata["effective_from"])
    path=f"{number}/{effective}/{digest[:16]}_{Path(filename).name}"
    client.storage.from_("legal-corpus").upload(
        path,content, file_options={"content-type":MIME_TYPES[suffix],"upsert":"true"}
    )
    row={
        "number":metadata["number"],"title":metadata["title"],"article":metadata.get("article",""),
        "effective_from":metadata["effective_from"],"effective_to":metadata.get("effective_to"),
        "status":metadata["status"],"source_url":metadata["source_url"],
        "storage_path":path,"filename":Path(filename).name,"content_hash":digest,"uploaded_by":uploaded_by,
    }
    client.table("legal_documents").upsert(row,on_conflict="content_hash").execute()
    return row


def load_published_legal_documents(client) -> list[dict[str, Any]]:
    today=date.today().isoformat()
    rows=client.table("legal_documents").select(
        "number,title,article,effective_from,effective_to,status,source_url,storage_path,filename"
    ).eq("status","Còn hiệu lực").lte("effective_from",today).order("number").execute().data or []
    documents=[]
    for row in rows:
        if row.get("effective_to") and row["effective_to"] < today:
            continue
        try:
            content=client.storage.from_("legal-corpus").download(row["storage_path"])
            documents.append({
                "filename":row["filename"],"content":content,
                "metadata":{
                    "number":row["number"],"title":row["title"],"article":row.get("article",""),
                    "effective_from":row["effective_from"],"effective_to":row.get("effective_to"),
                    "status":row["status"],"source_url":row["source_url"],
                }
            })
        except Exception:
            continue
    return documents
