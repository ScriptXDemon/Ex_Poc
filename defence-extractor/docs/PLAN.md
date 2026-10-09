# Defence Product Intelligence Extraction Engine — Implementation Plan v1.1

Date: 2026-10-08 · Status: implemented (see "Implementation status") · Builds on *Technical PRD v1* and *Functional Guide v1*; where they differ, this plan wins.

## Implementation status (2026-10-09)

All phases are implemented and run on the Algorithmtest server. Local folder = source of truth (`scripts/sync.sh`).

| Phase | State | Notes |
|---|---|---|
| 0 Foundation | done | uv venv (Python 3.12), torch-cpu, Docling, FlagEmbedding, Temporal CLI 1.9.1 — all user space |
| 1 Deterministic ingestion | done | HTML adapter (scopes, tables, k/v, counters, JSON-LD, title), Docling PDF adapter + pdfium fallback, measurement detector, marker legends, inventory |
| 2 Gold labels | done | 17 benchmark pages, written before any pipeline output was seen (`eval/gold/`) |
| 3 Page understanding | done | A2, A3 (chunked + merged), A4 (roles, classes, relations, merges), A7 (multi-product pages only) |
| 4 Discovery / attribution | done | A5B per chunk, A6 targeted (ambiguous facts only), A5A expected-spec hunt (BM25), designation rule |
| 5 Mapping / structuring / audit | done | A8 alias → label-checked hint → LLM shortlist; A9 value parser, list / "/"-alternative / L×W×H splitting; A10 audit + ≤2 recovery rounds |
| 6 Gates / assembly | done | A11 rules, A12 code gates + gpt-oss verifier, A13 JSON + ledger closure (0 open by construction) |
| 7 Orchestration / report | done | Temporal workflows (document + batch) and a local async runner sharing the same stage functions; `dx eval`, `dx report` |
| 8 A14 ontology learning | done (offline, simple) | dynamic-property clustering → `ontology_proposals.json` (needs approval) |

Corpus run (2026-10-09, `runs/corpus1`, report `docs/results_report.html`): 459/792 documents before the budget stop,
corpus gold 91.1% spec recall, benchmark 97.0%. Fixes made on real inputs during the run: UTF-8-first HTML decoding
(9% of pages were mojibake), WordPress JSON pages, byte-swapped PDF ligatures, comment threads excluded, dense-chunk
cap + overflow bisection + A4 batching, per-document spend cap, A8 generic-label rule, units taken from table headers.

Deviations from the plan, forced by the server (no Docker, no sudo, no GPU):
- **Storage:** filesystem artifacts (`runs/<run>/docs/<doc>/state.json` + `result.json`) instead of Postgres/MinIO; the contracts are unchanged, so a Postgres sink can be added in the datacenter.
- **Temporal:** the CLI dev server (SQLite), not a cluster.
- **Sync:** tar over SSH (no rsync on the server).
- **OCR:** RapidOCR is only used for PDFs without a text layer (none in the current corpus).
- **Embeddings:** BGE-M3 is installed, but A5A uses BM25 (the embedder is wired but off by default, to keep CPU time for parsing).

## 1. Fixed decisions

| Topic | Decision |
|---|---|
| Inputs | Raw HTML, rendered HTML, PDF (including scanned). One document = one independent job; no cross-document merging. |
| Languages | English first. The design stays multilingual-ready: the source is never translated and values are kept verbatim. |
| Flow | Discovery and attribution run at page level (§3), restructured from PRD v1. |
| Orchestration | Temporal. Agents are typed Python functions with no agent framework. |
| Models (OpenRouter, iterations 1–2) | Main model: `qwen/qwen3.8-27b`, pinned to DeepInfra bf16. A10 and A12: `openai/gpt-oss-120b`. |
| Serving (datacenter, later) | SGLang. |
| Embeddings | BGE-M3, installed on the Algorithmtest server. |
| Output | JSON designed by us (§4). Spec vocabulary seeded from the v0 prompt's parameter list (§5). |
| Tech innovation | A technology is its own sub-entity with its own specs, and is also listed as a feature of the product. |
| Code | This local folder is the source of truth. It syncs to a separate project on Algorithmtest, where runs execute. |
| Iteration 1 data | The 17 benchmark pages that fetch cleanly (203 old answer-key specs). The 14 bot-blocked pages are skipped. |
| Ground truth | Gold labels written by Claude *before* seeing any pipeline output, cross-checked against the old answer key. |
| Budget | OpenRouter key limit $40 ($25.61 left on 2026-10-08). Hard budget guard, and spend reported for every run. |

## 2. Iterations

- **Iteration 1 (now):** the full pipeline end to end on 17 HTML pages, plus the evaluation harness and a cost/latency profile.
  - Baseline to beat: on these pages the old v3 flow caught **183/203 (90.1%)** by value alone, and **164/203 (80.8%)** once product attribution is required.
- **Iteration 2 (your 300–500 samples):**
  - PDF adapter: layout, tables, OCR and a vision-model fallback.
  - Rendered-HTML edge cases.
  - Prompt tuning driven by error classes.
  - Gold set grown to about 200 stratified documents.
  - FastAPI service and Docker Compose stack.
  - A cost projection approved before the full run.
- **Iteration 3 (datacenter):**
  - SGLang serving.
  - Prefix-cache validation, comparing runs with the cache on and off.
  - Throughput tuning.
  - A14 offline ontology learning.

## 3. Pipeline v1.1

```
1  A0 profile → A1 parse (HTML / rendered HTML / PDF adapter) → SemanticDocument + indexes
2  Deterministic fact inventory → evidence ledger (every item "open")
3  A2 page map → A3 entities + alias merge → A4 roles, relations, product classes, technologies
4  A7 reference resolution (footnote/variant legends, coreference chains)
5  A5B open discovery per section  +  A5A expected-spec hunt per product class
6  A6 attribution across ALL entities, with applicability (variant / conditions)
7  A8 ontology mapping → A9 structuring             (per entity that owns facts)
8  A10 evidence-first audit per section → recovery (≤ 2 rounds, back to 6)
9  A11 anomaly rules → A12 verification            (per entity, batched)
10 Ledger closure (zero "open") → A13 assembly → JSON Schema validation
```

| Agents | Model | Thinking |
|---|---|---|
| A1, fact inventory, A9 normalisation, A11 rules, verification gates | code | – |
| A0, A2, A8 | Qwen3.8-27B | off |
| A3, A4, A7, A6 | Qwen3.8-27B | low |
| A5B, A5A | Qwen3.8-27B | off vs low, decided by measurement |
| A10, A12 | gpt-oss-120b | medium |

Every agent follows the same contract:
- Pydantic input and output models and a versioned prompt template.
- JSON-schema structured output.
- Post-validation: cited block IDs must exist, and quoted values must occur in the cited blocks.
- One repair retry, after which the activity fails visibly. A document never loses data silently.

## 4. Output contract (per document, illustrative)

```json
{
  "document": {"doc_id": "E526", "url": "https://helicopters.leonardo.com/en/products/aw149",
               "format": "html", "language": "en", "content_sha256": "…",
               "page_type": "manufacturer_product_page", "page_scope": "single_product"},
  "entities": [
    {"entity_id": "E1", "name": "Leonardo", "type": "company"},
    {"entity_id": "E2", "name": "AW149", "aliases": [], "type": "product", "role": "focal_product"},
    {"entity_id": "E3", "name": "General Electric CT7-2E1", "type": "engine", "role": "component"}
  ],
  "products": [{
    "entity_id": "E2", "name": "AW149", "role": "focal_product", "manufacturer_entity_id": "E1",
    "classification": [{"domain": "air", "category": "helicopter", "subcategory": "medium_multirole"}],
    "specifications": [{
      "spec_id": "S14",
      "parameter": {"id": "hover_ceiling_oge", "status": "candidate", "source_label": "HOGE"},
      "fact_class": "performance_specification",
      "value_text": "2,893 m (9,490 ft)",
      "value": {"kind": "scalar", "nominal": 2893, "unit": "m", "alternates": [{"nominal": 9490, "unit": "ft"}]},
      "applies_when": {"conditions": [{"dimension": "powerplant", "value_text": "General Electric CT7-2E1"}]},
      "evidence": [{"block_id": "b0388", "quote": "2,893 m (9,490 ft)*", "char_start": 0, "char_end": 19}],
      "confidence": {"evidence": 1.0, "attribution": 0.96, "mapping": 0.90, "value": 1.0, "overall": 0.90},
      "verification": "verified"
    }],
    "capabilities": [], "features": [], "technologies": [],
    "components": [{"entity_id": "E3", "predicate": "powered_by"}],
    "relations": [{"predicate": "powered_by", "object_entity_id": "E3", "evidence": [{"block_id": "b0371"}]}],
    "missions": [], "targets": [], "unresolved": [],
    "coverage": {"inventory_items": 41, "extracted": 37, "rejected": 3, "unresolved": 1}
  }],
  "ledger": [{"item_id": "L022", "block_id": "b0102", "origin": "inventory:measurement",
              "status": "rejected", "reason": "navigation_contamination", "spec_ids": []}],
  "execution": {"pipeline_version": "1.1.0", "ontology_version": "1.0.0",
                "models": {"main": "qwen/qwen3.8-27b@DeepInfra", "verifier": "openai/gpt-oss-120b"},
                "llm_calls": 31, "cost_usd": 0.07, "duration_s": 84}
}
```

Conventions:
- **Values:** `value_text` is always the source text, unchanged. `value` is the structured reading: scalar, range, bound (`>`, `up to`, `30+`), set, composite (L×W×H) or count (`2 x`), with unit and alternate units.
- **Variant scope:** `applies_when` carries the variant or conditions a value is tied to.
- **One `specifications` list,** with `parameter.status` set to `canonical`, `candidate` or `dynamic`. This replaces the PRD's two separate lists. `source_label` keeps the page's own label.
- **Non-spec facts** go in their own collections:
  - capabilities;
  - features, with `technology_ref` linking to the technology;
  - technologies, each an entity with its own specs;
  - components;
  - relations (launched_from, mounted_on, fires, integrated_with, …);
  - missions;
  - targets.
- **Marketing and procurement statements** stay in the ledger as rejected, with a reason, so they are never silently lost.
- **Every record** carries evidence (block ID, quote, character span), a confidence score and a verification status.

## 5. Ontology seed v1.0.0

**Starting point: the v0 prompt's 72 parameters, cleaned up.**
- **Unit-free canonical IDs.** Legacy IDs stay as aliases so downstream consumers don't break: `maximum_range_km` → `max_range`, `calibre_mm` → `calibre`, `vehicle_weight_t` → `combat_weight`.
- **Merge duplicates:**
  - `muzzle_velocity_mps`/`_mps2` → `muzzle_velocity`
  - `power_kw`/`power_output_kw` → `power_output`
  - `effective_range_km`/`_m` → `effective_range`
  - `projectile_weight_kg`/`shell_weight_kg` → `projectile_weight`
  - `max_speed_kmh`/`maximum_speed_kmh`/`max_speed_knots` → `max_road_speed` and `max_speed`, with the unit taken from the value
  - `protection_class`/`protection_level` → `protection_level`, with anti-examples to stop "dumping"
  - `sensor_payload_suite`/`sensor_acoustic_system` → `sensor_suite`
- **Relation-type parameters become typed relations.** They are still projected into the spec view for consumers:
  - `launch_platform` → `launched_from` / `mounted_on` / `transportable_by`
  - `compatible_weapon_system` → `integrated_with` / `compatible_with`
  - `ammunition_type` → `fires` / `uses_ammunition`
  - armaments → `equipped_with`, with a role
- **Every parameter gets:**
  - description;
  - expected dimension and units;
  - value type and `allow_multiple`;
  - applicable product classes;
  - aliases and anti-examples.

**Additions and taxonomy.**
- **Common parameters missing from v0 are added as `candidate`.** They are mapped consistently but need your approval to become canonical. Examples:
  - width, height, ground clearance, wheelbase;
  - fording, gradient, side slope, trench, vertical obstacle, turning radius;
  - fuel capacity, transmission, suspension, drive configuration, torque, power-to-weight;
  - hover ceiling IGE/OGE, rotor diameter, cruise speed.
- **Product-class taxonomy:** domain → category → subcategory, based on PRD §7.3, and multi-label.

## 6. Phases (iteration 1)

Each phase ends with tests passing and, once git is approved, a local checkpoint.

**Phase 0 — Foundation**
- Repo layout (`src/defence_extractor/…`), Python 3.12 via uv, ruff, mypy, pytest. Makefile targets: `sync`, `test`, `run`, `eval`.
- Contracts: all Pydantic models, plus JSON Schema export as the single source of truth.
- LLM gateway: an OpenAI-compatible client pointed at OpenRouter.
  - Provider pinning: `order`, `allow_fallbacks: false`, `require_parameters: true`.
  - Structured outputs, reasoning control and seed.
  - Retries with backoff, and detection of refusals and empty answers.
  - Per-call usage and cost logging.
  - A response cache keyed on input hash, agent, prompt version, ontology version, model and provider.
- Budget guard:
  - checks the live key balance before each run;
  - refuses a run whose projected cost exceeds the balance minus a $2 reserve;
  - reports spend for every run.
- Server bootstrap in a separate directory on Algorithmtest:
  - a uv virtual environment with Python 3.12;
  - Postgres with pgvector, and MinIO — run in their own Docker Compose project bound to localhost on non-default ports, if Docker is available;
  - a Temporal dev server;
  - BGE-M3 installed, with a smoke test.
- Sync: `make sync` rsyncs code from local to the server. Run outputs and reports are pulled back into local `runs/`.
- **Exit:** tests pass locally and on the server, and one structured-output smoke call per model succeeds with its cost logged.

**Phase 1 — Deterministic ingestion**
- Snapshot store: raw HTML plus URL, fetch time and sha256 for each of the 17 pages.
- HTML adapter → SemanticDocument:
  - blocks in visible order, with heading paths, DOM paths, stable block IDs and sections;
  - tables with multi-row headers, merged cells, units in headers, footnotes and cell-level provenance;
  - key-value patterns;
  - lists, cards, tabs and accordions, with hidden content kept as `visible=false`;
  - detection of navigation, boilerplate and related content, using repetition, link density and position rather than CSS class names alone;
  - metadata evidence blocks: title, H1, breadcrumb, meta/OG tags, JSON-LD, `data-*` counter targets and image alt text.
- Measurement detector:
  - numbers, ranges, bounds, ± and approximate values;
  - dual units and composites;
  - calibres (155 mm, 7.62×51, L52, .50 cal);
  - rates, speeds, angles, temperatures and frequencies;
  - percentages, tagged by context;
  - locale-specific number formats.
- Marker parser: `*`, `**`, `†` and superscripts, linked to their legend text.
- Fact inventory → ledger items, each with block, character span, detector and type.
- Indexes: BM25, unit/value, heading, table schema and DOM neighbourhood; BGE-M3 dense and sparse embeddings on the server.
- **Exit:**
  - SemanticDocument and inventory produced for all 17 pages;
  - unit tests pass;
  - deterministic coverage measured: the share of the old answer key's numeric values that the inventory captures.

**Phase 2 — Gold labels** (in parallel; written before any pipeline output is seen)
- Each of the 17 pages gets:
  - entities: name, aliases, type and role;
  - every fact: entity, fact class, parameter (or acceptable set), verbatim value, conditions and evidence quote;
  - relations;
  - must-not-extract negatives: navigation, related products, accessories, procurement and placeholders.
- Cross-check against the old answer key, with disagreements documented. You spot-check a sample.
- **Exit:** `eval/gold/*.yaml` plus summary statistics.

**Phase 3 — Page understanding**
- Prompt framework:
  - a cache-friendly layout: shared rules → document header → evidence → agent instruction;
  - templates and prompt versioning.
- A0 and A2.
- A3, including alias merging across name, SKU and trademark variants.
- A4: roles, relation graph, product classes and technology entities.
- A7: deterministic legend binding plus LLM coreference chains.
- **Exit:** product detection precision/recall and role accuracy measured against gold, and cost per document recorded.

**Phase 4 — Discovery and attribution**
- Evidence packet builder, using the expansion rules in PRD §10.2. Token budgets apply, and packets are never truncated silently.
- A5B per section.
- A5A per product class: ontology-driven queries → hybrid retrieval → evidence packets.
- A6 attribution at page level, including applicability.
- Inventory items are linked to discovered facts by span overlap, and the ledger is updated accordingly.
- **Exit:** candidate recall and attribution accuracy against gold, plus how much of the inventory the LLM finds on its own.

**Phase 5 — Mapping, structuring, audit**
- A8 mapping: exact alias → embedding shortlist → LLM choice. Below the confidence threshold a parameter stays dynamic. A routing table sends each fact class to its collection.
- A9 structuring:
  - a deterministic value parser, using pint plus defence-specific units;
  - splitting of multi-value statements;
  - decomposition of composite values;
  - the LLM only for values the parser can't read.
- A10 audit (gpt-oss): works evidence-first per section and compares against the ledger.
- Targeted recovery: at most 2 rounds, deduplicated by evidence span and a normalised fact hash.
- **Exit:** first end-to-end metrics on the 17 pages.

**Phase 6 — Quality gates and assembly**
- A11 rules:
  - placeholders and UI defaults;
  - unit/dimension mismatch;
  - label/value shift;
  - conflicting duplicates;
  - bleed from navigation or accessories.

  The LLM is used only for flagged cases.
- A12 verification (gpt-oss), batched per evidence packet:
  - deterministic gates first;
  - then the LLM judges attribution, mapping and support;
  - confidence is a calibrated combination of these.
- A13 assembly:
  - dedupe and assemble the final records;
  - coverage summary;
  - schema validation;
  - execution manifest;
  - ledger closure check (zero items left open).
- **Exit:** schema-valid final JSON for all 17 pages, with zero silent drops.

**Phase 7 — Orchestration, full run, report**
- Temporal workflows on the server:
  - a document workflow, with activities per agent and child workflows per entity and section;
  - retry and timeout settings per activity;
  - a budget overrun is a non-retryable error;
  - runs resume from persisted artifacts.
- A local in-process runner shares the same stage definitions, for debugging.
- Full run on the 17 pages, producing an evaluation report (JSON, plus an HTML diff per document) covering:
  - product precision and recall;
  - fact precision and recall;
  - attribution and mapping accuracy (canonical vs dynamic);
  - split and merge errors;
  - preserved qualifiers and applicability;
  - hallucinations and silent drops;
  - the v3 comparison on the old answer key, both value-only and product-aware;
  - cost, latency, tokens and cache hits per document.
- **Exit:** report delivered, and the pipeline is ready for your iteration-2 data.

## 7. Cost control

- **Estimate:**
  - about $0.06–0.10 per page;
  - about $1–2 for a full 17-page run;
  - about $5–10 for prompt development;
  - iteration 1 in total: about **$8–15** of the $25.61 available.
- **Prices (live on 2026-10-08):**
  - Qwen3.8-27B on DeepInfra: $0.15 per million input tokens, $1.875 per million output tokens, cached input $0.037 per million;
  - gpt-oss-120b: about $0.04 per million input tokens and $0.17–0.19 per million output tokens.
- **Guard:**
  - checks the live balance before every run, because the key's balance also moves from use elsewhere;
  - per-run cap and a $2 reserve;
  - stops and reports instead of overspending.

## 8. Prompt caching (OpenRouter phase)

- **Fixed prompt order:** shared rules → document header (page map and entity registry) → section or entity evidence → agent instruction and schema.
- **Byte-stable serialisation,** so every call on a document shares the same prefix.
- **Cached tokens are measured on every call.** DeepInfra bills cached input at about a quarter of the normal input price.
- **The same layout carries over to SGLang** in the datacenter, where Qwen3.8's hybrid-architecture cache needs the on/off canary before it is trusted.

## 9. Risks

| Risk | Mitigation |
|---|---|
| The pinned OpenRouter provider is unavailable or changes behaviour | Fail fast and retry later. Never switch quantisation silently. Log the provider on every call. |
| Qwen3.8's default thinking setting (`xhigh`) inflates token counts | Explicit reasoning control on every call, per-call token caps, and verification in the Phase 0 smoke test. |
| 17 short, single-product English pages don't exercise long, multi-product or PDF documents | The architecture is still built for those cases; iteration 2 data is essential. |
| Gold labels are written by an LLM | You spot-check a sample, and disagreements are logged. |
| The key is shared with other usage | The guard works from the live balance. A dedicated key for this project is recommended. |

## 10. Pending on you

1. **SSH access.** Install the existing key once by running `ssh-copy-id -i ~/.ssh/algotest_systemadm.pub algotest` (it asks for the server password).
2. **OpenRouter key.** Is something else using it? Its spend rose with no calls from this project. A dedicated key would make budget tracking exact.
3. **Git.** Is it OK to `git init` this folder and commit a local checkpoint per phase, with no remote?
