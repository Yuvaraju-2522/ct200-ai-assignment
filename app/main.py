from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Query
from sqlalchemy.orm import Session
from sqlalchemy import select
from pathlib import Path
from datetime import datetime, timezone
import uuid, json, re, os, tempfile
import httpx
from .database import Base, engine, get_db
from .models import DocumentVersion, DocumentNode, Selection
from .schemas import SelectionCreate, GenerateRequest, TestCase
from .parser import parse_pdf, sha256_text, normalize
from .store import save_generation, get_generation, all_generations

Base.metadata.create_all(bind=engine)
app = FastAPI(title="CT-200 Document QA API", version="1.0.0", description="CT-200 PDF ingestion, versioning, selections, QA generation and staleness detection")

@app.get("/health")
def health(): return {"status": "ok"}

def node_dict(n):
    return {"id": n.id, "version": n.version_id, "parent_id": n.parent_id, "heading": n.heading,
            "level": n.level, "node_type": n.node_type, "body_text": n.body_text,
            "content_hash": n.content_hash, "logical_key": n.logical_key, "page_number": n.page_number}

def compare_nodes(old_nodes, new_nodes):
    old_by_key = {n.logical_key: n for n in old_nodes}
    new_by_key = {n.logical_key: n for n in new_nodes}
    changed, added, unchanged = [], [], []
    for key, n in new_by_key.items():
        old = old_by_key.get(key)
        if old is None: added.append(n)
        elif old.content_hash != n.content_hash: changed.append((old, n))
        else: unchanged.append(n)
    removed = [n for k, n in old_by_key.items() if k not in new_by_key]
    return changed, added, unchanged, removed

@app.post("/ingest", status_code=201)
def ingest(file: UploadFile = File(...), db: Session = Depends(get_db)):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files are supported")
    content = file.file.read()
    if not content: raise HTTPException(400, "Uploaded PDF is empty")
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(content); tmp_path = tmp.name
    try: parsed, source_hash = parse_pdf(tmp_path)
    except Exception as e: raise HTTPException(422, f"Could not parse PDF: {e}")
    finally:
        try: os.unlink(tmp_path)
        except OSError: pass
    existing = db.scalar(select(DocumentVersion).where(DocumentVersion.source_sha256 == source_hash))
    if existing: raise HTTPException(409, f"This exact PDF is already ingested as version {existing.version}")
    latest = db.scalar(select(DocumentVersion).order_by(DocumentVersion.version.desc()))
    version_num = 1 if latest is None else latest.version + 1
    old_nodes = list(latest.nodes) if latest else []
    # Match using normalized heading path and exact block text. This deliberately exposes uncertain structural matches.
    old_by_key = {n.logical_key: n for n in old_nodes}
    changed = added = unchanged = 0
    version = DocumentVersion(version=version_num, filename=file.filename, source_sha256=source_hash,
                              created_at=datetime.now(timezone.utc).isoformat())
    db.add(version); db.flush()
    # Database IDs are version-pinned; logical_key is the cross-version identity used for comparison.
    id_map = {item["id"]: "n_" + sha256_text(f"v{version_num}:{item['logical_key']}")[:24] for item in parsed}
    for item in parsed:
        old = old_by_key.get(item["logical_key"])
        if old and old.content_hash == item["content_hash"]: unchanged += 1
        elif old: changed += 1
        else: added += 1
        version_item = dict(item)
        version_item["id"] = id_map[item["id"]]
        version_item["parent_id"] = id_map.get(item["parent_id"], None) if item["parent_id"] else None
        db.add(DocumentNode(version_id=version.id, **version_item))
    db.commit()
    return {"version": version_num, "filename": file.filename, "node_count": len(parsed),
            "changed_nodes": changed, "new_nodes": added, "unchanged_nodes": unchanged,
            "removed_nodes": max(0, len(old_nodes) - unchanged - changed)}

@app.get("/versions")
def list_versions(db: Session = Depends(get_db)):
    versions = db.scalars(select(DocumentVersion).order_by(DocumentVersion.version)).all()
    return [{"version": v.version, "filename": v.filename, "source_sha256": v.source_sha256,
             "created_at": v.created_at, "node_count": len(v.nodes)} for v in versions]

def resolve_version(db, version):
    v = db.scalar(select(DocumentVersion).where(DocumentVersion.version == version)) if version is not None else db.scalar(select(DocumentVersion).order_by(DocumentVersion.version.desc()))
    if not v: raise HTTPException(404, "Document version not found; ingest a PDF first")
    return v

@app.get("/sections")
def sections(version: int | None = None, db: Session = Depends(get_db)):
    v = resolve_version(db, version)
    ns = db.scalars(select(DocumentNode).where(DocumentNode.version_id == v.id, DocumentNode.node_type == "heading").order_by(DocumentNode.page_number, DocumentNode.level)).all()
    return {"version": v.version, "sections": [node_dict(n) for n in ns if n.level <= 1]}

@app.get("/nodes/{node_id}")
def get_node(node_id: str, version: int | None = None, db: Session = Depends(get_db)):
    q = select(DocumentNode).where(DocumentNode.id == node_id)
    if version is not None:
        v = resolve_version(db, version); q = q.where(DocumentNode.version_id == v.id)
    n = db.scalar(q)
    if not n: raise HTTPException(404, "Node not found in requested version")
    children = db.scalars(select(DocumentNode).where(DocumentNode.version_id == n.version_id, DocumentNode.parent_id == n.id)).all()
    return {**node_dict(n), "children": [node_dict(c) for c in children]}

@app.get("/search")
def search(q: str = Query(min_length=1), version: int | None = None, db: Session = Depends(get_db)):
    v = resolve_version(db, version); needle = q.lower()
    ns = db.scalars(select(DocumentNode).where(DocumentNode.version_id == v.id)).all()
    matches = [node_dict(n) for n in ns if needle in (n.heading + " " + n.body_text).lower()]
    return {"version": v.version, "query": q, "count": len(matches), "results": matches[:100]}

@app.get("/diff/{from_version}/{to_version}")
def diff(from_version: int, to_version: int, db: Session = Depends(get_db)):
    a, b = resolve_version(db, from_version), resolve_version(db, to_version)
    changed, added, unchanged, removed = compare_nodes(a.nodes, b.nodes)
    return {"from_version": from_version, "to_version": to_version,
            "changed": [{"logical_key": new.logical_key, "old_hash": old.content_hash, "new_hash": new.content_hash,
                         "old_text": old.body_text, "new_text": new.body_text} for old, new in changed],
            "added": [node_dict(n) for n in added], "unchanged_count": len(unchanged),
            "removed": [node_dict(n) for n in removed],
            "matching_note": "Matching uses logical heading path and normalized block text; reflowed/reparented content may appear as removed plus added."}

@app.post("/selections", status_code=201)
def create_selection(payload: SelectionCreate, db: Session = Depends(get_db)):
    v = resolve_version(db, payload.version)
    found = db.scalars(select(DocumentNode).where(DocumentNode.version_id == v.id, DocumentNode.id.in_(payload.node_ids))).all()
    if len({n.id for n in found}) != len(set(payload.node_ids)):
        raise HTTPException(400, "Every node ID must exist in the selected version")
    sid = "sel_" + uuid.uuid4().hex[:16]
    s = Selection(id=sid, name=payload.name, version_id=v.id, node_ids_json=json.dumps(payload.node_ids), created_at=datetime.now(timezone.utc).isoformat())
    db.add(s); db.commit()
    return {"selection_id": sid, "name": s.name, "version": v.version, "node_ids": payload.node_ids}

@app.get("/selections/{selection_id}")
def get_selection(selection_id: str, db: Session = Depends(get_db)):
    s = db.get(Selection, selection_id)
    if not s: raise HTTPException(404, "Selection not found")
    v = db.get(DocumentVersion, s.version_id)
    ids = json.loads(s.node_ids_json)
    ns = db.scalars(select(DocumentNode).where(DocumentNode.version_id == v.id, DocumentNode.id.in_(ids))).all()
    by_id = {n.id: n for n in ns}
    return {"selection_id": s.id, "name": s.name, "version": v.version,
            "nodes": [node_dict(by_id[i]) for i in ids if i in by_id],
            "reconstructed_text": "\n\n".join((by_id[i].heading + "\n" if by_id[i].heading else "") + by_id[i].body_text for i in ids if i in by_id)}

def validate_cases(raw, source_ids, count):
    errors=[]
    if not isinstance(raw, list): return [], ["Output must be a JSON array"]
    if not 3 <= len(raw) <= 5: errors.append("Output must contain 3 to 5 test cases")
    cases=[]
    for i, x in enumerate(raw):
        try:
            case = TestCase.model_validate(x)
            if not case.steps: errors.append(f"Case {i+1}: steps cannot be empty")
            if not case.expected_result.strip(): errors.append(f"Case {i+1}: expected_result cannot be empty")
            if not case.source_node_ids or any(nid not in source_ids for nid in case.source_node_ids): errors.append(f"Case {i+1}: source_node_ids must refer to selected nodes")
            cases.append(case)
        except Exception as e: errors.append(f"Case {i+1}: schema validation failed: {e}")
    return cases, errors

def llm_cases(nodes, count, prompt):
    """Optional OpenAI-compatible provider with bounded retries; returns cases and provider label."""
    api_key = os.getenv("LLM_API_KEY")
    if not api_key:
        return fallback_cases(nodes, count), "deterministic_local_fallback"
    base_url = os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/")
    model = os.getenv("LLM_MODEL", "llama-3.3-70b-versatile")
    source = [{"id": n.id, "heading": n.heading, "text": n.body_text, "hash": n.content_hash} for n in nodes]
    system = ("You are a QA engineer for a medical device manual. Return ONLY a JSON array of 3 to 5 objects. "
              "Each object must have title, preconditions (array of strings), steps (array of strings), "
              "expected_result, and source_node_ids (array containing only supplied node IDs). "
              "Do not invent device behavior; base expected results on the source text. If evidence is insufficient, say so in expected_result.")
    last_error = None
    for attempt in range(2):
        user_prompt = prompt + "\nRequested cases: " + str(count) + "\nSelected source nodes:\n" + json.dumps(source, ensure_ascii=False)
        if last_error:
            user_prompt += "\nPrevious response failed validation: " + last_error + ". Return corrected JSON only."
        try:
            response = httpx.post(base_url + "/chat/completions", headers={"Authorization": f"Bearer {api_key}"},
                json={"model": model, "temperature": 0.1, "messages": [{"role":"system","content":system},{"role":"user","content":user_prompt}], "response_format":{"type":"json_object"}}, timeout=45)
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            decoded = json.loads(content)
            if isinstance(decoded, dict): decoded = decoded.get("test_cases", decoded.get("cases", []))
            cases, errors = validate_cases(decoded, {n.id for n in nodes}, count)
            if not errors and len(cases) >= 3:
                return [c.model_dump() for c in cases], f"openai-compatible:{model}"
            last_error = "; ".join(errors) or "too few cases"
        except Exception as exc:
            last_error = str(exc)[:500]
    # Preserve the failure as an explicit error instead of silently claiming an LLM result succeeded.
    return [], f"llm_failed:{last_error}"

def fallback_cases(nodes, count):
    """Deterministic source-aware fallback for a runnable demo without API credentials."""
    text = " ".join((n.heading + " " + n.body_text) for n in nodes)
    lower = text.lower()
    ids = [n.id for n in nodes]
    candidates = []
    if "overpressure" in lower or "299 mmhg" in lower or "e3" in lower:
        candidates.append(("Overpressure protection", ["A measurement is in progress"], ["Simulate cuff pressure above the documented limit", "Observe the error display and venting behavior"], "The device stops inflation, vents the cuff, and displays E3 within the timing specified by the selected source."))
    if "battery" in lower or "low-battery" in lower or "low battery" in lower:
        candidates.append(("Battery threshold behavior", ["Battery capacity can be controlled in a test setup"], ["Set remaining battery capacity around the threshold documented in the selected source", "Observe the icon or measurement error"], "The low-battery indication and measurement behavior match the threshold and behavior stated in the selected source."))
    if "cuff" in lower or "cuﬀ" in lower or "leak" in lower:
        candidates.append(("Cuff fit or leak behavior", ["A correctly sized cuff and a controllable leak setup are available"], ["Use a cuff condition covered by the selected source", "Start a measurement and observe the device response"], "The device behavior matches the cuff sizing or leak-detection requirement stated in the selected source."))
    if "motion" in lower or "artifact" in lower or "e2" in lower:
        candidates.append(("Motion artifact handling", ["A measurement is in progress"], ["Introduce motion during measurement", "Observe the error display and retry prompt"], "The device response matches the motion-artifact behavior documented in the selected source."))
    if "bluetooth" in lower or "sync" in lower or "e6" in lower:
        candidates.append(("Bluetooth sync behavior", ["The companion app and device are available"], ["Trigger or simulate a Bluetooth sync failure", "Attempt the next sync and inspect the display"], "The sync-failure behavior matches the selected source; measurement behavior is not changed unless the source says otherwise."))
    if "storage" in lower or "readings" in lower or "overwritten" in lower:
        candidates.append(("Reading storage behavior", ["A user profile has stored readings"], ["Fill the selected profile's storage capacity", "Save one additional reading", "Inspect which reading remains stored"], "Storage behavior matches the retention and overwrite policy in the selected source."))
    # Fill to the required minimum with tests tied to the selected source rather than unrelated device features.
    excerpt = re.sub(r"\s+", " ", text).strip()[:350]
    heading = next((n.heading for n in nodes if n.heading), "Selected manual requirement")
    while len(candidates) < max(3, count):
        i = len(candidates) + 1
        candidates.append((f"Requirement verification {i}: {heading}", ["The selected CT-200 requirement is available for verification"],
                          ["Configure a test setup for the selected requirement", "Execute the documented condition", "Record the observed device behavior"],
                          f"Observed behavior is checked against the selected source text: {excerpt}"))
    return [{"title": title, "preconditions": pre, "steps": steps, "expected_result": expected, "source_node_ids": ids}
            for title, pre, steps, expected in candidates[:count]]

@app.post("/generate")
def generate(payload: GenerateRequest, db: Session = Depends(get_db)):
    s = db.get(Selection, payload.selection_id)
    if not s: raise HTTPException(404, "Selection not found")
    v = db.get(DocumentVersion, s.version_id); ids = json.loads(s.node_ids_json)
    ns = db.scalars(select(DocumentNode).where(DocumentNode.version_id == v.id, DocumentNode.id.in_(ids))).all()
    by_id = {n.id:n for n in ns}; nodes = [by_id[i] for i in ids if i in by_id]
    if not nodes: raise HTTPException(400, "Selection contains no retrievable nodes")
    raw, provider = llm_cases(nodes, payload.count, payload.prompt)
    cases, errors = validate_cases(raw, set(ids), payload.count)
    if provider.startswith("llm_failed:"):
        errors.append(provider)
    gid = "gen_" + uuid.uuid4().hex[:16]
    record = {"generation_id": gid, "selection_id": s.id, "version": v.version, "node_ids": ids,
              "source_hashes": {n.id:n.content_hash for n in nodes}, "prompt": payload.prompt,
              "created_at": datetime.now(timezone.utc).isoformat(), "cases": [c.model_dump() for c in cases],
              "status": "valid" if not errors else "invalid", "validation_errors": errors,
              "provider": provider, "staleness": {"is_stale": False, "changed_nodes": [], "message": "Checked against source version at generation time."}}
    save_generation(record)
    return record

@app.get("/generations/{generation_id}")
def retrieve_generation(generation_id: str, db: Session = Depends(get_db)):
    rec = get_generation(generation_id)
    if not rec: raise HTTPException(404, "Generation not found")
    latest = db.scalar(select(DocumentVersion).order_by(DocumentVersion.version.desc()))
    stale=[]
    if latest and latest.version > rec["version"]:
        current = {n.id:n for n in latest.nodes}
        for nid, old_hash in rec["source_hashes"].items():
            old_node = next((n for n in db.scalars(select(DocumentNode).where(DocumentNode.version_id == rec["version"])).all() if n.id == nid), None)
            # Node IDs are derived from logical keys; use the logical key to find reflow-safe current match.
            if old_node:
                match = next((n for n in latest.nodes if n.logical_key == old_node.logical_key), None)
                if match is None or match.content_hash != old_hash:
                    stale.append({"node_id": nid, "logical_key": old_node.logical_key, "reason": "removed" if match is None else "content_changed", "current_node_id": match.id if match else None})
    rec["staleness"] = {"is_stale": bool(stale), "changed_nodes": stale,
                         "message": "Source content changed or disappeared; review these cases." if stale else "No source changes detected for the selected nodes."}
    return rec

@app.get("/generations")
def list_generations(selection_id: str | None = None):
    records = list(all_generations().values())
    if selection_id: records = [r for r in records if r.get("selection_id") == selection_id]
    return {"count": len(records), "generations": records}
