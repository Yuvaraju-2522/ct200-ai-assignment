from pathlib import Path
from app.parser import parse_pdf

ROOT = Path(__file__).resolve().parents[1]

def test_parser_extracts_headings_and_content():
    nodes, digest = parse_pdf(ROOT / "data" / "ct200_manual.pdf")
    assert len(nodes) > 20
    assert len(digest) == 64
    assert any(n["heading"] == "Device Overview" for n in nodes)
    assert any("oscillometric" in n["body_text"].lower() for n in nodes)

def test_duplicate_logical_keys_do_not_collapse():
    nodes, _ = parse_pdf(ROOT / "data" / "ct200_manual.pdf")
    keys = [n["logical_key"] for n in nodes]
    assert len(keys) == len(set(keys))

def test_versions_have_detectable_changes():
    v1, _ = parse_pdf(ROOT / "data" / "ct200_manual.pdf")
    v2, _ = parse_pdf(ROOT / "data" / "ct200_manual_v2.pdf")
    old = {n["logical_key"]: n for n in v1}
    new = {n["logical_key"]: n for n in v2}
    shared = set(old) & set(new)
    assert shared
    assert any(old[k]["content_hash"] != new[k]["content_hash"] for k in shared)

def test_known_v1_v2_changes_are_visible():
    v1, _ = parse_pdf(ROOT / "data" / "ct200_manual.pdf")
    v2, _ = parse_pdf(ROOT / "data" / "ct200_manual_v2.pdf")
    text1 = "\n".join(n["body_text"] for n in v1)
    text2 = "\n".join(n["body_text"] for n in v2)
    assert "300 measurement cycles" in text1
    assert "250 measurement cycles" in text2
    assert "E6" not in "\n".join(n["body_text"] for n in v1)
    assert "E6" in "\n".join(n["body_text"] for n in v2)
