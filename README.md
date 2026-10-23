# CT-200 PDF Document QA Pipeline

A CT-200-specific FastAPI backend that extracts a hierarchy from the supplied PDF manuals, preserves immutable document versions, supports browsing/search and version-pinned selections, generates schema-validated QA cases, and reports staleness when source content changes.

## Requirements
- Python 3.11+
- Dependencies in `requirements.txt`

## Setup
```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

## Run
```bash
uvicorn app.main:app --reload
```
Open `http://127.0.0.1:8000/docs` for interactive API docs.

## Ingest v1 and v2
In a second terminal, run:
```bash
curl -X POST http://127.0.0.1:8000/ingest -F "file=@data/ct200_manual.pdf"
curl -X POST http://127.0.0.1:8000/ingest -F "file=@data/ct200_manual_v2.pdf"
curl http://127.0.0.1:8000/versions
curl http://127.0.0.1:8000/diff/1/2
```
The same exact PDF cannot be ingested twice; the API returns HTTP 409 for a duplicate source hash.

## Main endpoints
- `POST /ingest` — ingest a PDF as the next immutable version.
- `GET /versions` — list versions.
- `GET /sections?version=1` — top-level and second-level headings.
- `GET /nodes/{node_id}?version=1` — node plus direct children.
- `GET /search?q=battery&version=2` — search headings and body text.
- `GET /diff/1/2` — changed, added, removed and unchanged nodes.
- `POST /selections` — create a version-pinned selection.
- `GET /selections/{selection_id}` — reconstruct selected source text.
- `POST /generate` — generate 3–5 validated QA cases for a selection.
- `GET /generations/{generation_id}` — retrieve generated cases and compute current staleness.
- `GET /generations?selection_id=...` — retrieve stored generations.

## Create a selection and generate cases
1. Call `GET /sections?version=1` and choose node IDs (or use `/search`).
2. Submit `POST /selections` with JSON:
```json
{"name":"Safety review","version":1,"node_ids":["n_REPLACE_WITH_REAL_NODE_ID"]}
```
3. Submit `POST /generate`:
```json
{"selection_id":"sel_REPLACE_WITH_REAL_ID","count":4,"prompt":"Generate concrete safety QA cases."}
```
4. Use the returned `generation_id` with `GET /generations/{generation_id}`. Re-ingest v2 first to test staleness.

## Storage choices
SQLite/SQLAlchemy stores immutable document versions, nodes, and selections. Generated outputs use an atomic JSON file store (`data/generations.json`) because the assignment explicitly permits a well-justified JSON store. Set `GENERATION_STORE` to override the location. Set `DATABASE_URL` to override SQLite.

## Parsing and matching decisions
- PyMuPDF extracts positioned text blocks and font sizes from the supplied PDFs.
- Numbered headings are recognized with a CT-200-focused pattern; parent-child links follow heading levels and observed reading order.
- Paragraphs, likely lists, and table-like blocks are retained as distinct node types where detectable.
- Each node has a SHA-256 content hash, page number, logical key and parent ID.
- Version comparison matches normalized logical paths/ordinals, then compares content hashes. If text is reflowed, a section is moved, or a block is split/merged, the match may show a removal plus an addition. This is surfaced rather than silently claiming semantic equivalence.
- PDF image blocks are not OCR'd in this initial implementation. Figures that exist only as raster images are a known limitation; the parser does not fabricate their contents.
- The local generation provider is a deterministic fallback for reproducible tests. To enable a real OpenAI-compatible LLM provider, set `LLM_API_KEY`, optionally `LLM_BASE_URL` (defaults to Groq-compatible URL) and `LLM_MODEL`. The implementation requests JSON, validates it with Pydantic, retries once after invalid output, and records an explicit failed status/error instead of silently pretending success. Do not send patient data to an external model.

## Tests
```bash
pytest -q
```
The tests cover parser extraction, duplicate-key preservation, cross-version content changes, API health, rejection of non-PDF uploads, and version listing. More edge-case tests should be added after visually comparing the full extraction with both PDF renderings.

## Important scope note
This is a focused parser for the supplied CT-200 manuals, not a generic PDF parser. PDF text extraction preserves much of the hierarchy, but complex table cell geometry and image-only content may need additional OCR/layout analysis. Confirm outputs against the rendered PDF before relying on them.
