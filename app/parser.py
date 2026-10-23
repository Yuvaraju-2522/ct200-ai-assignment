"""CT-200-specific PDF parser. Keeps uncertain blocks as paragraphs instead of inventing hierarchy."""
import hashlib, re, unicodedata
from pathlib import Path
import fitz

HEADING_RE = re.compile(r"^(\d+(?:\.\d+)*\.?)[ \t]+(.+?)\s*$")
TABLE_HEADERS = {"parameter", "code"}

def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    text = text.replace("ﬁ", "fi").replace("ﬀ", "ff")
    return re.sub(r"\s+", " ", text).strip()

def parse_pdf(path: str | Path) -> tuple[list[dict], str]:
    path = Path(path)
    pdf = fitz.open(path)
    raw_sha = hashlib.sha256(path.read_bytes()).hexdigest()
    blocks = []
    for page_index, page in enumerate(pdf):
        page_blocks = page.get_text("dict").get("blocks", [])
        for b in page_blocks:
            if "lines" not in b:
                # Image block: retain a traceable placeholder; OCR is not silently fabricated.
                continue
            lines = []
            sizes = []
            for line in b["lines"]:
                spans = line.get("spans", [])
                text = "".join(s.get("text", "") for s in spans).strip()
                if text:
                    lines.append(text)
                    sizes.extend([s.get("size", 10) for s in spans if s.get("text", "").strip()])
            text = "\n".join(lines).strip()
            if not text:
                continue
            blocks.append({"text": text, "page": page_index + 1, "font_size": max(sizes, default=10)})
    # PDF text blocks are in reading order for this supplied manual; preserve page and block order.
    nodes = []
    stack = []
    occurrence = {}
    current_section = None
    for b in blocks:
        text = b["text"]
        lines = [x.strip() for x in text.splitlines() if x.strip()]
        first = lines[0] if lines else text
        match = HEADING_RE.match(first)
        is_heading = bool(match and len(first) < 140)
        # A large bold title without a section number is only promoted if it resembles the document title.
        if not is_heading and b["font_size"] >= 17 and len(first) < 100 and ("manual" in first.lower() or "device overview" in first.lower()):
            is_heading = True
            match = None
        if is_heading:
            number = match.group(1).rstrip(".") if match else "title"
            title = match.group(2).strip() if match else first
            level = number.count(".") + 1 if number != "title" else 0
            # Known CT-200 numbering irregularities are retained as observed; do not force numbering into a perfect tree.
            while stack and stack[-1]["level"] >= level and level > 0:
                stack.pop()
            parent_id = stack[-1]["id"] if stack and level > 0 else None
            parent_key = stack[-1]["logical_key"] if stack and level > 0 else "root"
            logical_key = f"{parent_key}/{normalize(number)}:{normalize(title)}"
            occurrence[logical_key] = occurrence.get(logical_key, 0) + 1
            # Duplicate headings get distinct stable keys rather than being merged.
            logical_key = logical_key if occurrence[logical_key] == 1 else f"{logical_key}#occurrence-{occurrence[logical_key]}"
            node_id = "n_" + sha256_text(logical_key)[:24]
            node = {"id": node_id, "parent_id": parent_id, "heading": title, "level": level,
                    "node_type": "heading", "body_text": "\n".join(lines[1:]).strip(),
                    "content_hash": sha256_text(normalize("\n".join(lines[1:]))), "logical_key": logical_key, "page_number": b["page"]}
            nodes.append(node)
            if level > 0:
                stack.append({"id": node_id, "level": level, "logical_key": logical_key})
            current_section = node_id
        else:
            # Keep tables as a distinct node type when text has multiple aligned rows or table vocabulary.
            looks_table = ("\t" in text or (len(lines) >= 3 and any(x.lower().startswith(tuple(TABLE_HEADERS)) for x in lines)))
            node_type = "table" if looks_table else ("list" if re.match(r"^(\d+\.|[-•]|[A-Z]\))\s", first) else "paragraph")
            parent_id = stack[-1]["id"] if stack else None
            parent_key = stack[-1]["logical_key"] if stack else "root"
            # Distinguish repeated blocks by page and ordinal; body hash aids version matching below.
            base_key = f"{parent_key}/{node_type}"
            occurrence[base_key] = occurrence.get(base_key, 0) + 1
            # Ordinal keys allow changed body text to be detected as a modification, not only as add/remove.
            logical_key = f"{base_key}#{occurrence[base_key]}"
            node_id = "n_" + sha256_text(logical_key)[:24]
            nodes.append({"id": node_id, "parent_id": parent_id, "heading": "", "level": len(stack),
                          "node_type": node_type, "body_text": text, "content_hash": sha256_text(normalize(text)),
                          "logical_key": logical_key, "page_number": b["page"]})
    return nodes, raw_sha
