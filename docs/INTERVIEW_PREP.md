# Interview Prep (not part of the submission — for you)

The brief says you'll be assessed on *"your ability to explain and modify your implementation"* and interviewed on **Agentic AI, Generative AI, RAG, Vectorless RAG, LangChain, Google ADK, Dify**. This sheet maps each to this project.

## Before you submit — checklist

- [ ] Run it once end-to-end: `pytest -q`, start the server, ask all five brief questions in the UI.
- [ ] Run `python scripts/check_llm.py` with Ollama running, so you can truthfully say which interpreter handled what.
- [ ] Read `analytics.py` and `interpreter.py` top to bottom once. You will be asked to modify them.
- [ ] **Make `docs/AI_USAGE.md` true to your own experience.** It describes the build process honestly, but you must be able to speak to every item in your own words. Edit anything that doesn't match what you did or learned.
- [ ] Be upfront that you used AI — the brief explicitly allows and expects it. What's being assessed is whether you understand and can change what it produced.

## The 60-second explanation of this project

> "The user's question goes to a free local LLM (Ollama) whose only job is to turn it into a small JSON — which *intent* and which *parameters*. I validate that JSON against a whitelist and against the values that really exist in the database, then run a fixed, parameterised SQL query. So the LLM understands language but never writes SQL or produces numbers; the database computes everything. If the LLM is down or confused, a rule-based interpreter takes over. Every answer shows its supporting rows and SQL."

## Concept → this project

| Concept | Short definition | In this project / how to extend |
|---|---|---|
| **Generative AI** | Models that generate text/images/code (LLMs are transformer-based, trained to predict the next token) | The LLM is used for *understanding* (classification + extraction), not for generating answers — deliberately, to avoid hallucinated numbers |
| **RAG** | Retrieve relevant external information (typically via embeddings + a vector DB), put it into the prompt, then generate — grounds answers in real data and keeps knowledge fresh | Here the "retrieval" step is a **SQL query** and the grounding data is shown to the user. A natural RAG extension: retrieve metric definitions/glossary entries (e.g. "net revenue", "AOV") into the prompt |
| **Vectorless RAG** | Retrieval *without* embeddings/vector databases: keyword search (BM25), structured/SQL lookups, or an LLM reasoning over a document's hierarchical index/table of contents, knowledge graphs | This app is effectively vectorless: structured data is retrieved with deterministic SQL, which is exact — embeddings would be the wrong tool for numeric aggregation |
| **Agentic AI** | An LLM that pursues a goal by planning, calling tools, observing results and iterating (patterns: reflection, tool use, planning, multi-agent) | Currently a **single-step tool router** (LLM picks one tool = one intent). It becomes agentic if the LLM can chain tools: e.g. `month_comparison` → notice a big drop → call `top_by(product, month)` to explain it → write a summary; plus asking clarifying questions |
| **LangChain** | Python framework for composing LLM apps: prompts, models, tools, output parsers, chains; LangGraph adds stateful agent graphs | Port: each intent becomes a `@tool`; use structured output with a Pydantic schema instead of my hand-rolled JSON parsing; same validation layer stays |
| **Google ADK (Agent Development Kit)** | Google's open-source framework for building and orchestrating agents/multi-agent systems with tools (optimised for Gemini but model-flexible) | Port: an agent with the five analytics functions registered as tools; could split into a "router" agent and an "analyst" agent |
| **Dify** | Open-source low-code platform for LLM apps: visual workflows, knowledge bases (RAG), agent nodes, API publishing | Same flow as a workflow: LLM node (classify) → code node (validate) → SQL/tool node → template node. Good for prototyping; I'd still keep the validation logic in code |

## Questions you should expect (and the honest answers)

**Why not let the LLM write SQL?** Small free models write unreliable SQL, and a plausible-but-wrong query returns a confident wrong number (forgetting `status='Completed'` would silently include returns). Intent routing puts the business definition of revenue in one tested place. Cost: less flexibility.

**How do you stop hallucinated or unsupported answers?** (1) intent whitelist; (2) parameters must exist in the DB; (3) unknown-term guard refuses `Central`, `Q1`, `2025`; (4) the LLM never produces numbers; (5) every answer shows its evidence; (6) read-only DB.

**What if the LLM returns garbage or is offline?** Falls back to rules, and says so (`interpreted_by`, `llm_error`).

**How is prompt injection handled?** Guards run before the LLM; the question is declared data in the prompt; and even a fully hijacked LLM can only choose one of five read-only intents with validated parameters.

**Why did you reject the zero-quantity and missing-price rows but keep the returned one?** A zero-quantity or price-less row cannot be a real sale/revenue (and guessing a price corrupts totals) → quarantine with a reason. A returned order is a real, valid record whose revenue was reversed → keep it, exclude from revenue, disclose it.

**What are the limitations?** README §9. Know them cold.

**How would you evaluate the LLM part?** Labelled set of ~100 questions → intent/param accuracy, refusal precision on out-of-scope questions, latency; run in CI.

## Live-modification cheat sheet (where to edit)

| Task they might give you | Where |
|---|---|
| Revenue should ignore discounts / use gross sales | `analytics.py` → `NET_REVENUE` |
| Include returned orders | `analytics.py` → `_where()` default `status="Completed"` |
| Add intent "average order value" | 1) add to `INTENTS` 2) add handler `_aov()` (SQL: `AVG(quantity*unit_price-discount_amount)`) 3) register in `_HANDLERS` 4) add keywords in `rule_interpret` 5) add a test. The intent list in the LLM prompt updates automatically (it's built from `INTENTS`); if the new intent accepts filters, also add its name to the tuple at the top of `normalise_request` |
| Add a new column filter (e.g. status) | `DIMENSIONS` / `FILTER_COLUMNS` in `analytics.py` + schema in `database.py` |
| Accept a different date format | `database.py` → `strptime` format |
| Treat missing price differently (e.g. impute) | `database.py` → `_validate_row` |
| Switch LLM or model | `.env`: `LLM_PROVIDER`, `OLLAMA_MODEL` |
| Change currency symbol | `analytics.py` → `CURRENCY_SYMBOL` |
| Add a chart | `static/index.html` → `renderTable` (or add a canvas) |
