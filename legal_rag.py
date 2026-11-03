from __future__ import annotations

import io
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

try:
    from pypdf import PdfReader
except Exception:
    PdfReader = None
try:
    from docx import Document
except Exception:
    Document = None


def extract_text(filename: str, content: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        if PdfReader is None:
            raise RuntimeError("Thiếu thư viện đọc PDF.")
        reader = PdfReader(io.BytesIO(content))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    if suffix == ".docx":
        if Document is None:
            raise RuntimeError("Thiếu thư viện đọc DOCX.")
        doc = Document(io.BytesIO(content))
        return "\n".join(p.text for p in doc.paragraphs)
    if suffix in {".txt", ".md"}:
        return content.decode("utf-8-sig", errors="replace")
    raise ValueError("Chỉ hỗ trợ PDF có text, DOCX, TXT hoặc MD. PDF scan cần OCR trước.")


def chunk_text(text: str, max_chars: int = 1400, overlap: int = 180) -> list[str]:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        return []
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks, current = [], ""
    for paragraph in paragraphs:
        if len(paragraph) > max_chars:
            if current:
                chunks.append(current); current = ""
            start = 0
            while start < len(paragraph):
                chunks.append(paragraph[start:start+max_chars])
                start += max_chars-overlap
            continue
        if len(current) + len(paragraph) + 2 > max_chars and current:
            chunks.append(current)
            current = current[-overlap:] + "\n" + paragraph
        else:
            current = (current + "\n\n" + paragraph).strip()
    if current:
        chunks.append(current)
    return chunks


def build_legal_index(uploaded: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Only documents explicitly marked current and within their effective dates enter RAG."""
    today = date.today()
    index = []
    for item in uploaded:
        metadata = item["metadata"]
        if metadata.get("status") != "Còn hiệu lực":
            continue
        start = date.fromisoformat(metadata["effective_from"])
        end = date.fromisoformat(metadata["effective_to"]) if metadata.get("effective_to") else None
        if start > today or (end and end < today):
            continue
        text = extract_text(item["filename"], item["content"])
        for n, chunk in enumerate(chunk_text(text)):
            index.append({
                "chunk_id": f"{metadata['number']}#{n+1}",
                "text": chunk,
                "number": metadata["number"],
                "title": metadata["title"],
                "article": metadata.get("article",""),
                "effective_from": metadata["effective_from"],
                "source_url": metadata["source_url"],
                "status": metadata["status"],
            })
    return index


def retrieve_legal_context(index: list[dict[str, Any]], question: str, top_k: int = 5) -> list[dict[str, Any]]:
    if not index or not question.strip():
        return []
    corpus = [x["text"] for x in index]
    vectorizer = TfidfVectorizer(ngram_range=(1,2), max_features=50000, sublinear_tf=True)
    matrix = vectorizer.fit_transform(corpus + [question])
    scores = cosine_similarity(matrix[-1], matrix[:-1]).ravel()
    ranked = scores.argsort()[::-1][:top_k]
    return [{**index[i], "score": float(scores[i])} for i in ranked if scores[i] > 0.015]


def answer_with_openai(question: str, sources: list[dict[str, Any]], api_key: str,
                       model: str = "gpt-4o-mini") -> str:
    if not sources:
        return "Không tìm thấy đoạn văn bản phù hợp trong kho tài liệu đã xác minh. Hệ thống không đưa ra kết luận."
    from openai import OpenAI
    context = "\n\n".join(
        f"[{i+1}] {s['number']} – {s['title']} – {s.get('article','')}\n{s['text']}\nNguồn: {s['source_url']}"
        for i,s in enumerate(sources)
    )
    client = OpenAI(api_key=api_key)
    response = client.responses.create(
        model=model,
        input=[
            {"role":"system","content":"Bạn là trợ lý kế toán Việt Nam. Chỉ được kết luận dựa trên các trích đoạn nguồn được cung cấp. Nếu nguồn thiếu, mâu thuẫn hoặc không đủ căn cứ, nói rõ chưa thể kết luận. Không tự suy diễn hiệu lực văn bản. Trả lời gồm: kết luận có điều kiện; phân tích; trích dẫn nguyên văn ngắn kèm số hiệu/điều/khoản; checklist chứng từ. Gắn [1], [2] ngay sau từng nhận định. Không bịa số điều."},
            {"role":"user","content":f"Câu hỏi: {question}\n\nNguồn được truy xuất:\n{context}"}
        ],
    )
    return response.output_text


def make_checklist_xlsx(question: str, answer: str, sources: list[dict[str, Any]]) -> bytes:
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        pd.DataFrame([{"Câu hỏi":question,"Kết quả tư vấn":answer}]).to_excel(writer,index=False,sheet_name="Tu_van")
        pd.DataFrame([{"Văn bản":s["number"],"Tên văn bản":s["title"],"Đoạn":s.get("article",""),
                       "Mã đoạn":s["chunk_id"],"Ngày hiệu lực":s["effective_from"],"Nguồn":s["source_url"]}
                      for s in sources]).to_excel(writer,index=False,sheet_name="Can_cu")
    return output.getvalue()
