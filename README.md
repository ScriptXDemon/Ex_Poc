# Spec extraction flow (POC)

A local web page that takes one product or news page and extracts its technical specifications with LLM agents, step by step, so you can see what each step did.

```
raw HTML → 1 resolve → 2 verify (+ repair) → 3 labels + example → 4 final prompt → 5 context check → 6 extract → 7 review
```

| Step | What it does |
|---|---|
| 1 Resolve | Turns the raw HTML into text. **Text only** (default) drops code, tags, SVG and styles and keeps every visible line. **Production** also drops menus, footers and the site template (needs reference pages, see below). |
| 2 Verify | An agent lists every spec on the page; code checks each one is in the resolved passage. Specs the resolver dropped are added back from the page, word for word. |
| 3 Labels + example | One agent lists the page's own spec labels; a second writes a short worked example from this page, in the production prompt's format. |
| 4 Final prompt | The production spec prompt (or your own, pasted in the Page section) + the labels + the example + the passage. |
| 5 Context check | Counts the prompt's tokens with the Qwen2.5 tokenizer and refuses a prompt that would not fit the model's context window. |
| 6 Extract | The final agent returns the specs as JSON, held to a strict schema. |
| 7 Review (optional) | A second, larger model (the judge) compares the JSON with the whole page as Markdown and lists only the **ontology** specs (the allowed parameter names) that are missing; a missed page label is fine. Code rejects any value not on the page, adds the ones written in the resolved passage to the output, and reports the ones the resolver dropped. |

Every step's prompt, answer, token count and time is shown on the page.

## Run it

Needs Python 3.10 or later.

```bash
git clone https://github.com/ScriptXDemon/Ex_Poc.git
cd Ex_Poc
pip install -r requirements.txt
python extraction2/spec_flow/server.py --port 8765
```

Open http://127.0.0.1:8765/ and then:

1. **Settings**: enter an OpenAI-compatible API base URL, your API key and the model.
   - OpenRouter: `https://openrouter.ai/api/v1`, model `qwen/qwen-2.5-72b-instruct` (the defaults).
   - Your own server (vLLM, llama.cpp, Ollama, LiteLLM): its `/v1` URL and its model name.
   - The key stays in your browser; the local server never sees it. Calls go from the browser straight to the API.
2. Set the context window and max output tokens if the page cannot read them from the API's model list.
3. **Page**: enter the page URL. Leave the HTML box empty to fetch the live page, or paste or upload the raw HTML.
4. Press **Run all steps**, or run the steps one at a time.

Self-check (no model calls): `python extraction2/spec_flow/server.py --demo` prints `ok`.
Step 7's recovery logic: `node extraction2/spec_flow/recover_check.js` prints `ok`.

The DC corpus: set `DC_DSN=postgresql://user:pass@host:5432/db` before starting the server and `/fetch` reads the page from its `documents` table first (`extraction2/dc.py`).

## Good to know

- **Slow servers.** Answers are streamed, so a proxy that cuts silent requests (Cloudflare cuts after about 100 s) does not kill a long answer. Each running step shows its elapsed time and how many tokens have arrived.
- **Pages that need JavaScript.** If the plain fetch finds no text, the server renders the page in headless Microsoft Edge with Playwright (`pip install playwright`; the Edge path is set in `server.py` as `EDGE`). Otherwise upload the HTML.
- **Production resolver mode** uses each site's template, learned from reference pages in `extraction2/corpus/web/ref/` (`index.jsonl` + `<document_id>.html`). They are not in this repo. Without them that mode still drops `<nav>`/`<footer>` blocks and menus, but has no site template.
- **Results page** (`/results.html`) shows a batch run's scores. Point it at a run folder with `SPEC_FLOW_RUN=<folder>` (with `results/`, `expected/` and `review/` subfolders); the batch runner itself is not in this repo.
- The verify agent has to see the whole page and the resolved passage together. A page bigger than the model's context window stops at the context check without spending anything.

## Files

| File | Role |
|---|---|
| `extraction2/spec_flow/index.html` | The flow page: all agent prompts, schemas and checks |
| `extraction2/spec_flow/server.py` | Local server: resolve, fetch, token count, page → Markdown |
| `extraction2/spec_flow/base_prompt.json` | The production spec prompt and its JSON schema |
| `extraction2/spec_flow/qwen2.5_tokenizer.json` | Qwen2.5-72B-Instruct tokenizer, for exact token counts |
| `extraction2/spec_flow/results.html`, `runview.py` | Batch run results page |
| `extraction2/webbench.py`, `content.py`, `docprep.py`, `dates.py` | The resolver (HTML → text) |
