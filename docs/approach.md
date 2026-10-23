# Approach Document — CT-200 Document QA Pipeline

## 1. Goal and scope

The system is intentionally specific to the supplied CardioTrack CT-200 manuals. It ingests the original PDF and the modified PDF as separate immutable versions, builds a searchable node hierarchy, pins selections to a specific version, generates structured QA cases, and reports when selected source content has changed. It does not claim to be a general parser for arbitrary PDFs.

## 2. Parsing approach

PyMuPDF is used first because the provided PDFs contain extractable text and PyMuPDF exposes page boundaries, text blocks, spans, and font sizes. The parser reads blocks in page order and recognizes numbered headings with a constrained pattern such as `2.1 General Specifications` and `2.1.1.1 Battery Life Under Typical Use`. Paragraphs and table-like blocks are retained as separate nodes. Every node stores page number, type, heading, level, parent ID, logical key, body text, and SHA-256 content hash.

The supplied manuals have visible tables on PDF pages 2 and 4 of v1 and pages 2 and 4 of v2. Text extraction retains the table contents as blocks, but may not preserve every cell boundary. Image-only content is not OCR'd by the current implementation. Those cases are exposed as known limitations rather than silently fabricated.

## 3. Hierarchy reconstruction

Numbered heading depth is inferred from the numbering pattern. A stack of open headings assigns parent IDs; a heading at the same or shallower depth closes the previous sibling/subtree. Unnumbered content remains under the nearest recognized heading. The source contains a numbering irregularity: `2.1.1.1 Battery Life Under Typical Use` appears under `2.1 General Specifications`, while `2.2 Cuff Specifications` follows it. The parser preserves the observed heading and hierarchy rather than renumbering the source.

Repeated headings receive distinct occurrence suffixes, so duplicate headings are not collapsed into one database node. Paragraph and table blocks use per-parent/type ordinal logical keys; this permits same-position body edits to be recognized as changed content, though block splitting/merging can cause a remove/add result.

## 4. Persistence and API

SQLAlchemy + SQLite stores document versions, nodes, and selections. A version records its original filename, source SHA-256 and ingest timestamp. Nodes are version-scoped; older nodes are never overwritten. Selections store the exact version and node IDs used when created. The generated QA output is stored in an atomic JSON file because the assignment allows a justified JSON store and this avoids requiring a local MongoDB service for a simple prototype.

FastAPI provides ingestion, version listing, section browsing, node retrieval, text search, version diff, version-pinned selection creation/retrieval, generation, and retrieval with a live staleness check.

## 5. Version matching and staleness

Nodes are matched by normalized logical key, then content hashes are compared. Exact hash equality means unchanged content. A shared key with a different hash is changed. A key only in the new version is added; a key only in the old version is removed. This is deterministic and explainable, but not full semantic matching. If a heading is renamed, a paragraph moves to another parent, or layout reflow changes block boundaries, the node can appear as removed plus added. The API documents this failure mode rather than claiming perfect matching.

Generated outputs record the exact source version, source node IDs, and source hashes. On retrieval, the system compares the stored hashes against the latest version's matching logical keys and returns a stale flag and per-node reason. It does not automatically regenerate stale test cases.

## 6. QA generation and validation

The current implementation includes a deterministic local fallback to make the project runnable without API credentials. It produces 3–5 cases and runs them through Pydantic validation, including checks that source IDs belong to the version-pinned selection. In deployment, replace the fallback with an LLM provider call that requests JSON only, validates the response, retries malformed output a bounded number of times, and stores invalid output plus validation errors if retries fail. The API's schema gate must remain authoritative; a model response is never trusted just because it is syntactically JSON.

## 7. Testing and validation

Automated tests verify basic PDF extraction, unique logical keys, detection of content changes between the supplied versions, API health, rejection of non-PDF uploads, and version listing. Manual validation should compare extracted headings and tables with the rendered PDF pages. In particular, compare the General Specifications table and Error Codes table, and inspect the battery-life paragraph, cuff-inflation sequence, E3 response time, and E6 addition across versions.

## 8. Initial implementation failures and improvements

A first draft keyed body blocks by their text. That made edited paragraphs look like unrelated additions/removals rather than changes. The key was changed to parent + block type + ordinal, with the content hash used independently for change detection. This improves the expected case where a paragraph's content changes but its location remains stable. It still has a known weakness when blocks are inserted before a paragraph or PDF layout causes block reordering; those cases can shift ordinals.

A second risk is table extraction: PDF text extraction can flatten cells into one block. The parser retains the text and tags likely table blocks, but full geometric row/cell reconstruction would require coordinate-based line analysis or a table extraction library and manual validation.

## 9. Decision log

1. **Most likely silent failure:** a layout change can shift block ordinals and cause a changed paragraph to look like a new/removed node. Catch it with cross-version diff review, content-hash checks, and fixtures that assert known changed CT-200 values are detected.
2. **Simplicity over correctness:** table cells and raster-only figures are not fully reconstructed in this first pass. This would break first for tables whose columns wrap or image-only diagrams; the current output preserves extracted text and reports this limitation.
3. **Unsupported input:** a scanned PDF whose pages contain no extractable text is not OCR'd. The parser may return very few nodes. In production, detect low text density and run OCR (for example, Tesseract) or return a clear unsupported/scanned-PDF warning rather than pretending extraction succeeded.

## 10. Further work

Add coordinate-aware table reconstruction, OCR for image-only pages, fixture-based assertions for each known v1-to-v2 change, a real configurable LLM provider with bounded retries, and a semantic matching review queue for ambiguous version matches.
