"""Bounded TXT/PDF extraction and local excerpt retrieval, without network access.

原文件只进入账号数据库。PDF 在独立子进程解析，限制 CPU 和墙钟时间，Linux 上额外限制内存；
不执行 PDF 脚本、不下载链接、不做 OCR。页码/行号属于原文定位，不推断说话人身份。
"""

import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys

MAX_BYTES = 2 * 1024 * 1024
MAX_CHARS = 100000
MAX_PAGES = 40


def extract(filename, raw):
    """提取受限文本块；拒绝空白、加密、扫描件及超过解析预算的文件。"""
    name = re.split(r"[/\\]", filename)[-1].strip()
    if not name or len(name) > 160 or any(ord(c) < 32 for c in name):
        raise ValueError("文件名无效 / Invalid filename")
    suffix = Path(name).suffix.lower()
    if suffix not in (".txt", ".pdf") or not 0 < len(raw) <= MAX_BYTES:
        raise ValueError("仅支持 2 MB 内 TXT / PDF / TXT or PDF up to 2 MB")
    if suffix == ".txt":
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeError:
            raise ValueError("请将 TXT 保存为 UTF-8 编码 / Save TXT as UTF-8") from None
        if any(ord(c) < 32 and c not in "\n\r\t" for c in text):
            raise ValueError("TXT 含非文本内容 / Not a plain text file")
        pages = [text]
    else:
        if not raw.startswith(b"%PDF-"):
            raise ValueError("不是有效 PDF / Invalid PDF")
        try:
            result = subprocess.run(
                [sys.executable, str(Path(__file__).resolve()), "--pdf-worker"],
                # 解析器无需模型凭证，不继承服务进程中的 API Key。
                env={"PATH": os.defpath, "PYTHONIOENCODING": "utf-8"},
                input=raw,
                capture_output=True,
                timeout=15,
                check=True,
            )
            data = json.loads(result.stdout)
        except (subprocess.SubprocessError, ValueError):
            raise ValueError(
                "PDF 无法读取或超出解析限额 / PDF parsing failed or exceeded limits"
            ) from None
        if data.get("error"):
            raise ValueError(data["error"])
        pages = data["pages"]
    if sum(map(len, pages)) > MAX_CHARS:
        raise ValueError(
            "文字超过 10 万字符，请拆分文件 / Split files over 100,000 characters"
        )
    if not any(p.strip() for p in pages):
        raise ValueError(
            "没有可读取文字，暂不支持扫描件 / No text; scanned files need OCR"
        )
    blocks = []
    for page, text in enumerate(pages, 1):
        # 保留原始行内文字与时间标签；长行切片不假装是完整发言。
        for line, value in enumerate(text.splitlines(), 1):
            for start in range(0, len(value), 900):
                part = value[start : start + 900]
                if part.strip():
                    blocks.append(
                        {
                            "index": len(blocks) + 1,
                            "page": page if suffix == ".pdf" else None,
                            "line": line,
                            "text": part,
                        }
                    )
    if len(blocks) > 3000:
        raise ValueError(
            "段落过多，请拆分文件 / Split files with over 3,000 text segments"
        )
    return {
        "name": name,
        "blocks": blocks,
        "pages": len(pages),
        "characters": sum(map(len, pages)),
        "blank_pages": [i for i, p in enumerate(pages, 1) if not p.strip()],
    }


def retrieve(rows, query, budget=7000):
    """按词项匹配选取受限片段；保留原顺序，并明确全文/局部覆盖范围。"""
    tokens = set(re.findall(r"[a-z0-9]{2,}|[\u4e00-\u9fff]{2}", query.lower()))
    candidates = []
    for row in rows:
        for b in row["blocks"]:
            item = {
                **b,
                "source_id": f"{row['id']}:{b['index']}",
                "attachment_id": row["id"],
                "name": row["name"],
            }
            candidates.append(item)
    ranked = sorted(
        candidates, key=lambda b: -sum(t in b["text"].lower() for t in tokens)
    )
    chosen, used = [], 0
    for b in ranked:
        cost = len(json.dumps(b, ensure_ascii=False))
        if used + cost <= budget:
            chosen.append(b)
            used += cost
    selected = {b["source_id"] for b in chosen}
    excerpts = [b for b in candidates if b["source_id"] in selected]
    coverage = [
        {
            "attachment_id": r["id"],
            "name": r["name"],
            "total_blocks": len(r["blocks"]),
            "selected_blocks": sum(b["attachment_id"] == r["id"] for b in excerpts),
            "blank_pages": r["blank_pages"],
            "partial": any(
                f"{r['id']}:{b['index']}" not in selected for b in r["blocks"]
            )
            or bool(r["blank_pages"]),
        }
        for r in rows
    ]
    return {"excerpts": excerpts, "coverage": coverage}


def pdf_worker():
    """独立解析进程；资源超限由父进程报告，不把解析异常或文件内容写入日志。"""
    import resource

    resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
    # macOS 不支持此地址空间限制；部署的 Linux 子进程强制 512 MB 上限。
    if sys.platform.startswith("linux"):
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(sys.stdin.buffer.read(MAX_BYTES + 1)))
        if reader.is_encrypted:
            raise ValueError("请上传未加密的 PDF / Encrypted PDF is not supported")
        if len(reader.pages) > MAX_PAGES:
            raise ValueError("PDF 最多 40 页，请拆分 / Split PDFs over 40 pages")
        pages, count = [], 0
        for page in reader.pages:
            text = page.extract_text() or ""
            count += len(text)
            if count > MAX_CHARS:
                raise ValueError(
                    "文字超过 10 万字符，请拆分 / Text exceeds 100,000 characters"
                )
            pages.append(text)
        print(json.dumps({"pages": pages}, ensure_ascii=False))
    except ValueError as e:
        # 仅返回本文件定义的固定消息，避免第三方异常包含隐私原文。
        message = str(e)
        print(
            json.dumps(
                {
                    "error": message
                    if message.startswith(("请上传", "PDF 最多", "文字超过"))
                    else "PDF 无法读取 / Cannot read PDF"
                }
            )
        )
    except Exception:
        print(json.dumps({"error": "PDF 无法读取 / Cannot read PDF"}))


if __name__ == "__main__":
    pdf_worker()
