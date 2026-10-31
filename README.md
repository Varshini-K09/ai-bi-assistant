# Mini AI Business Intelligence Assistant

Ask questions about sales data in plain English and get an answer **plus the SQL-derived figures that prove it**.

```
"Which product generated the highest revenue?"
→ The product with the highest revenue is Monitor with ₹4,73,000 (32.9% of ₹14,39,200 total).
   + full ranking table, notes on exclusions, and the exact parameterised SQL used.
```

* **Backend:** Python 3.10+, Django + Django REST Framework, SQLite (standard library `sqlite3`)
* **AI:** a *free* LLM used only to understand the question (Ollama local model by default; Gemini free tier optional; works with **no LLM at all** too)
* **UI:** one dependency-free HTML page
* **Tests:** 129 automated tests (pytest)

---

## 1. Which AI/LLM is used, and is it free?

| Option | Cost | Needs account / key? | Where it runs | Use it when |
|---|---|---|---|---|
| **Ollama + `llama3.2:3b`** (default) | Free, open-source | No | Your machine (≈2 GB download, ≈4 GB RAM) | You want a real LLM with no signup and no data leaving your PC |
| **Google Gemini free tier** (optional) | Free tier (rate-limited; Google may use free-tier data to improve its products) | Free API key from aistudio.google.com | Google cloud | Your PC is too weak for Ollama |
| **`none` (rule-based)** | Free | No | Your machine | No LLM available — the app still works for all supported questions |

Switch with one line in `.env` (`LLM_PROVIDER=ollama | gemini | none`). If the LLM is down, slow or returns garbage, the app automatically falls back to the rule-based interpreter and tells you so in the response.

> **Key design decision:** the LLM never writes SQL and never produces a number. It only picks *what is being asked* (an intent + parameters). Fixed, parameterised SQL computes every figure. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for why.

---

## 2. Setup & run

```bash
# 1) create environment and install (Python 3.10+)
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 2) (optional but recommended) install a free local LLM
#    Install Ollama from https://ollama.com/download , then:
ollama pull llama3.2:3b              # one-time ~2 GB download
cp .env.example .env                 # Windows: copy .env.example .env

# 3) run
python manage.py runserver
# open http://127.0.0.1:8000
```

**No LLM?** Set `LLM_PROVIDER=none` in `.env` (or run `LLM_PROVIDER=none python manage.py runserver`). Everything still works.

**Gemini instead?** Put `LLM_PROVIDER=gemini` and `GEMINI_API_KEY=...` in `.env`. Free-tier model names change over time; adjust `GEMINI_MODEL` if needed.

**Confirm your LLM is really being used** (takes ~30 s):

```bash
python scripts/check_llm.py
```

The page footer also shows which interpreter is active, and each answer shows `interpreted by: llm:ollama` or `rules`.

### Run the tests

```bash
pytest -q          # 129 tests, ~3 s, no LLM / network needed
```

---

## 3. What it can answer

| Question type | Example | Intent |
|---|---|---|
| Total revenue (optionally filtered) | "Total revenue for the North region in March" | `total_revenue` |
| Best / worst product, category, region, channel (optionally top-N) | "Which product generated the highest revenue?", "Top 3 products by revenue" | `top_by` |
| Monthly trend | "What is the monthly revenue trend?" | `monthly_trend` |
| Change between two months | "How did revenue change between January and February?" | `month_comparison` |
| Average order value (overall, filtered or per dimension) | "What is the average order value by region?" | `average_order_value` |
| Orders and units sold (overall, filtered or per dimension) | "Which product sold the most units?", "How many orders?" | `sales_volume` |
| Best / worst single month | "Which month had the lowest revenue?" | `month_extreme` |
| Data quality | "Which records were rejected?" | `data_quality` |

All **five** example questions from the brief are supported (the brief required four), plus the extra intents above, which cover the most natural follow-up questions. Anything else is refused safely (see §6).

## 4. API

`POST /api/ask` with `{"question": "..."}`

| HTTP | `status` | Meaning |
|---|---|---|
| 200 | `ok` | Answered. Body has `answer`, `columns`, `rows` (supporting figures), `queries` (SQL + params), `notes`, `intent`, `params`, `interpreted_by` |
| 400 | `error` | Malformed request: empty, >500 chars, not JSON, `question` not a string |
| 422 | `unsupported` | Valid request we can't/won't answer (off-topic, SQL/modify attempts, unknown terms). Includes `suggestions` |
| 422 | `invalid` | Understood but parameters invalid (e.g. month with no data). Message says what data exists |
| 500 | `error` | Unexpected failure; generic message only, details are logged server-side |

Also: `GET /api/health`, `GET /api/examples`, `GET /` (UI).

---

## 5. Data management

* **Storage:** the CSV is loaded into SQLite at startup (`data/sales.db`, rebuilt each run so it always matches the CSV).
* **Two tables:** `sales` (validated rows only — every query reads this) and `rejected_records` (rows that failed validation, with line number, reason and the raw row, so nothing disappears silently).
* **Dates** arrive as `DD-MM-YYYY` and are stored as ISO `YYYY-MM-DD`; month = `substr(order_date,1,7)`.
* **Querying:** fixed SQL templates with `?` placeholders. The API opens the DB **read-only** (`mode=ro`).

### What happened to the dirty rows in the supplied CSV

| Row | Problem | Handling |
|---|---|---|
| TXN0033 | `Status = Returned` | **Loaded** but excluded from revenue; disclosed in every answer's notes (₹19,000) |
| TXN0034 | `Quantity = 0` | **Rejected** (invalid sale) → `rejected_records` |
| TXN0051 | `Unit_price` missing | **Rejected** (revenue cannot be computed; guessing would be wrong) → `rejected_records` |

Result: 51 rows → 49 loaded, 2 rejected, 48 counted as revenue (1 returned).

### Assumptions (also shown in the UI notes)

1. **Revenue = quantity × unit_price − discount_amount**, `Completed` orders only.
2. `Discount_amount` is a **per-transaction total**, not per unit (e.g. TXN0004: ₹500 off a ₹57,000 sale).
3. The CSV has no currency; **₹** is shown (change `CURRENCY_SYMBOL` in `analytics.py`).
4. Month comparison uses the **earlier month as baseline** whatever order you type them.
5. Only dates in `DD-MM-YYYY` are accepted.

---

## 6. How wrong / unsupported answers are prevented

1. **LLM output is untrusted input.** Its JSON must name a whitelisted intent; parameters are matched (case-insensitively) against values that actually exist in the database. Invented regions/products/months are rejected with a message listing valid values.
2. **No LLM-written SQL.** Nothing user- or LLM-supplied is ever concatenated into SQL; column names come from a fixed whitelist, values are bound parameters.
3. **Read-only database connection** — even a bug cannot modify data.
4. **Pre-LLM guards:** SQL/"delete/drop/update…" attempts are refused; unresolvable terms (`Central region`, `Q1`, `2025`, `iPhone`, `last month`) are refused rather than silently ignored (otherwise "revenue for Central" would return the overall total — a confidently wrong answer).
5. **Prompt-injection resistant:** the guards run before the LLM is called, and even a fully compromised LLM can only choose among 8 fixed read-only intents.
6. **Transparency:** every answer shows its supporting rows, SQL, exclusions and which interpreter produced it.
7. **Graceful degradation:** LLM down / timeout / malformed JSON → deterministic rules.

## 7. Tests (129)

| File | Covers |
|---|---|
| `tests/test_database.py` | cleaning rules, quarantine, ISO dates, read-only enforcement, missing file/columns |
| `tests/test_analytics.py` | all five brief questions with independently-verified numbers, filters, **edge cases** (empty scope, ties), invalid params, injection |
| `tests/test_new_intents.py` | top-N, average order value, units/orders, best/worst month — expected values recomputed independently from the raw CSV; routing of new phrasings; guards still hold |
| `tests/test_interpreter.py` | 13 supported phrasings, `May` vs *may*, unknown terms, destructive input, ambiguity |
| `tests/test_llm.py` | LLM failure modes via fakes **and a fake Ollama HTTP server** (request format, HTTP 500, not running, timeout, bad JSON, hallucinated intent, Gemini key never in URL) |
| `tests/test_api.py` | HTTP status codes, response shape, all five brief questions end-to-end, no data change after attacks, no leaked internals |

The expected numbers (total ₹14,39,200; Monitor ₹4,73,000; South ₹4,11,200; Jan ₹2,10,250 → Feb ₹2,34,600, +11.58%) were cross-checked against an independent pandas calculation. I also verified the tests are meaningful by temporarily injecting four bugs (ignore discounts, count returns, accept zero quantity, disable a guard) — each made the suite fail.

## 8. Verification status — what has and hasn't been proven

* ✅ Data loading, analytics, API, UI serving, guards: run and tested.
* ✅ LLM *integration code* (request format, parsing, validation, every failure path): tested with fakes and a local fake Ollama server.
* ⚠️ **A live run against a real Ollama/Gemini model was not possible in the environment this was built in.** Run `python scripts/check_llm.py` once on your machine to confirm; if a small model routes some phrasing wrongly, the validation layer prevents a wrong *answer* (you get a clear refusal or the rule-based fallback), and you can try `qwen2.5:3b` or a larger model via `OLLAMA_MODEL`.

## 9. Known limitations

* Rule-based mode is keyword-driven: unusual phrasings may get "unsupported" (the LLM handles paraphrases better).
* Revenue-based ranking only for top-N (units and average order value rank by their own metric); best/worst month is revenue-only.
* No quarters, years, relative dates ("last month"), multi-value filters ("North and South"), profit, forecasts or follow-up questions with memory.
* Single-turn: no conversation context.
* One revenue definition; no per-unit discount interpretation.
* ~50-row dataset: SQLite and an in-request catalog load are fine here, not designed for millions of rows.
* No authentication or rate limiting (not production-ready by design).
* UI is minimal (table with inline bars, no charts).

## 10. Project layout

```
manage.py
bi_project/        Django project (settings.py, urls.py, wsgi.py, asgi.py)
assistant/         Django app
  config.py        env-driven settings
  database.py      CSV → SQLite, validation, quarantine, read-only connection
  analytics.py     whitelisted intents → parameterised SQL (all numbers come from here)
  interpreter.py   LLM prompt + rule-based interpreter + safety guards
  llm.py           Ollama / Gemini clients
  service.py       one question end-to-end
  runtime.py       one-time startup state (CSV→SQLite build, LLM provider)
  serializers.py   DRF request validation
  views.py         DRF views: /api/ask, /api/health, /api/examples, UI page
  urls.py          routes
  static/assistant/index.html  UI
data/sales.csv     supplied data
tests/             129 tests
scripts/check_llm.py   live-LLM smoke test
docs/              ARCHITECTURE.md, AI_USAGE.md, INTERVIEW_PREP.md
```
