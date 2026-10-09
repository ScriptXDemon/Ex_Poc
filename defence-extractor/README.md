# Defence Product Intelligence Extraction Engine (pipeline v1.1)

Turns defence web pages (raw or rendered HTML) and PDFs into an evidence-backed JSON record of every company, product,
variant and subsystem in the document and every technical fact about each: attached to the right product, copied
verbatim with its block and page, mapped to the parameter catalogue when safe and kept as a **dynamic** specification
otherwise. Every technical-looking statement ends in an evidence ledger as extracted, rejected (with a reason) or
unresolved, so nothing is silently dropped.

Measured results (strict scorer, hand-written answer keys in `eval/`):

| set | result |
|---|---|
| 17 benchmark pages, 267 specs | 97.0% found on the right product, 0 on a wrong product |
| 16 corpus documents, 281 specs | 94.0% found, 2 on a wrong product |
| 13 large documents (PDF catalogues up to 144 pages, HTML up to 310k chars), 403 sampled specs | 75.7% found; start / middle / end of document 82% / 64% / 76% |

Reports: `docs/results_report.html` (459-document run), `docs/large_file_results.html` (large-file test),
`docs/large_file_plan.html` (design of the large-file changes).

## Pipeline

Each document runs as a sequence of checkpointed stages (`runs/<run>/docs/<doc>/state.json`), as a Temporal workflow
or with the local async runner:

```
ingest          HTML adapter, or PDF via Docling in 40-page windows (no page limit, disk cache); page statistics
vision          pages without a text layer or mostly image -> rendered and transcribed by the vision model
page_map  (A2)  section roles and subjects, windowed over the whole document
entities  (A3)  products, variants, systems, companies (chunked over the document)
roles     (A4)  role, parent, product class and relations of every entity
references(A7)  footnote / variant legends, "the system", "its"
product_map     which product each section, page, table row and table column describes (deterministic)
discovery (A5B) open fact discovery per chunk, densest chunks first; each call sees only the 40 relevant entities
attribution(A6) owner of every fact: document structure first, the model only for ambiguous facts
known_hunt(A5A) expected parameters per product class
mapping   (A8)  catalogue mapping (alias -> hint -> model with a shortlist); unknown properties stay dynamic
structuring(A9) value parsing (ranges, units, alternates, conditions)
audit     (A10) coverage audit of unexplained values -> targeted recovery
anomaly   (A11) deterministic rules
verification(A12) evidence and owner check of every fact (code gates + verifier model)
assembly  (A13) one product sheet per product: duplicates merged, conflicts flagged, subsystems rolled up, page provenance
```

## Quick start

Requirements: Linux or macOS, Python 3.12, about 8 GB RAM (Docling), an OpenAI-compatible LLM endpoint.

```bash
git clone <this repo> && cd defence-extractor
bash scripts/server_setup.sh            # uv, .venv (Python 3.12), CPU torch, docling, Temporal CLI; idempotent
source .venv/bin/activate
cp .env.example .env                     # then set LLM_BASE_URL, LLM_API_KEY and the model names
python scripts/llm_probe.py              # checks the endpoint: chat, JSON schema, image input, throughput
python -m pytest -q tests/unit           # 39 unit tests, no LLM calls
```

### Run on your own chunk of documents

Put the files in any folder (sub-folders allowed). HTML (`.html`, `.htm`) and PDF files are picked up; an optional
`index.csv` / `pdf_index.csv` (columns `file, url, level, host, ...`) adds the source URL and metadata.

```bash
# local async runner (simplest)
dx run --root /data/my_chunk --run-id chunk01 --concurrency 4

# durable Temporal run (resumable: the same command continues a stopped run; finished stages are kept)
ROOT=/data/my_chunk bash scripts/run_corpus.sh chunk01 8
python scripts/progress.py chunk01       # done / in flight, spend, stage errors
python3 scripts/stuck.py chunk01         # documents idle in a stage
bash scripts/restart_worker.sh chunk01 8 # load new code without losing in-flight work
```

Useful options: `--ids a,b,c` or `--ids @ids.txt` (only these documents), `--levels 3,4`, `--fmt pdf`, `--limit N`,
`--order budget` (most documents per token first). Per-document caps: `DOC_BUDGET_TOKENS` (self-hosted) or
`DOC_BUDGET_USD` (OpenRouter); once reached, no new discovery chunks or audit rounds start, and mapping and
verification still run.

### Outputs

- `runs/<run>/docs/<doc_id>/result.json`: the final record (`docs/final_result.schema.json`; regenerate with `dx schema`)
- `runs/<run>/docs/<doc_id>/state.json`: every intermediate artifact (used for resume and debugging)
- `runs/<run>/llm_calls.jsonl`: every model call (agent, tokens, latency, cost)
- `python3 scripts/export_outputs.py [out_dir]`: one folder per document with the input file, `output.json` and a readable `summary.md`
- `dx report <run>`: corpus statistics and catalogue proposals (A14) as HTML

### Evaluate

Answer keys are YAML files (`eval/gold`, `eval/gold_corpus`, `eval/gold_large`; format in
`eval/gold_large/*.yaml`, written from the parsed source before looking at any output, with `pos` start/middle/end).

```bash
dx eval <run> --gold eval/gold_large      # recall, wrong-product values, by position, parameter mapping
python3 scripts/large_report.py <run>     # per-document view: product map coverage, vision, budget, dynamic specs
python scripts/dump_pages.py <doc_id> --pages 10-14 --out dump.txt   # parsed text with page numbers, to write keys
python3 scripts/gold_check.py eval/gold_large/<doc>.yaml dump.txt    # every key value must occur in the source
```

## LLM endpoints

The gateway speaks the OpenAI chat-completions API with strict JSON-schema output.

- **Self-hosted** (`LLM_API_FLAVOR=openai`): GPU farm, vLLM, SGLang or llama.cpp behind LiteLLM. Responses are streamed by
  default, so long JSON answers survive a proxy request timeout. Qwen3 thinking is requested off with
  `chat_template_kwargs`; servers that ignore it still work (the model then thinks on every call). Set
  `LLM_CONCURRENCY` to the number of requests the server really runs in parallel: llama.cpp with one slot serves
  requests one at a time, vLLM / SGLang batch dozens.
- **OpenRouter** (`LLM_API_FLAVOR=openrouter`): provider pinning, cost accounting per call and document, budget guard on
  the key balance.

Roles: `MAIN_MODEL` (A2–A9, discovery), `VERIFIER_MODEL` (A10 audit, A12 verification; a different model family gives
a more independent check), `VISION_MODEL` (page transcription; must accept image input).

## Layout

```
src/defence_extractor/
  contracts/      typed Pydantic models (single source of truth; `dx schema` exports the JSON Schema)
  ingest/         HTML adapter, PDF adapter (Docling windows, table continuation), units, measurements, inventory
  ontology/       seed_v1.yaml + extensions/a14_corpus1.yaml (catalogue v1.1, 178 parameters), loader, value parser
  llm/            gateway (streaming, cache, retries, schema repair), budget guard, response cache
  agents/         prompts, A2..A13, vision, segments (product map)
  pipeline/       DocState (checkpointed per stage), rendering, local runner
  orchestrator/   Temporal workflows, activities (heartbeats), worker
  evaluation/     scorer, HTML reports, corpus statistics, A14 catalogue proposals
eval/             answer keys (benchmark, corpus, large files) and the parsed old benchmark
scripts/          setup, runs, progress, probes, dumps, reports, repairs
tests/unit/       unit tests (no LLM calls)
```

## Development notes

- `bash scripts/sync.sh` copies the code to a server (`DX_REMOTE`, `DX_REMOTE_DIR`); the laptop copy is the source of truth.
- The LLM response cache (`var/llm_cache.sqlite`) makes a re-run with unchanged prompts free and deterministic.
- `docs/PLAN.md` holds the design decisions; `docs/ontology_a14_review.md` the catalogue extension review.
