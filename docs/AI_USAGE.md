# AI Usage, Problems & Improvements

## 1. AI tools used

| Tool | Role | Cost |
|---|---|---|
| **Claude (Anthropic), via claude.ai with code execution** | Development assistant: requirements analysis, design, writing code/tests/docs, running the code, debugging | Used as the development assistant |
| **Ollama + Llama 3.2 3B** (runtime) | The LLM *inside* the product; only classifies a question into an intent + parameters | Free, local, open-source |
| **Google Gemini free tier** (optional runtime) | Alternative cloud LLM for machines that can't run Ollama | Free tier |

Researched during development: current Gemini free-tier status. Findings that shaped the design: free-tier models and limits changed several times in 2025–2026 (limits were cut in Dec 2025, some models were deprecated), and free-tier inputs may be used by Google to improve its products. That is why the **default is a local Ollama model** (no account, no quota, data stays local) and Gemini is only optional.

## 2. How AI was used during development

1. **Requirement analysis first.** I had the assistant read the brief and inspect the CSV *before* designing. This surfaced three planted data problems: a `Returned` order (TXN0033), `Quantity = 0` (TXN0034) and a missing `Unit_price` (TXN0051), plus `DD-MM-YYYY` dates.
2. **Design discussion → decision.** The AI proposed two architectures (free-form text-to-SQL vs intent routing). I chose intent routing because a 3B free model writes unreliable SQL and a plausible-but-wrong query gives a confident wrong number. The reasoning is in `ARCHITECTURE.md`.
3. **Generation in layers**, each verified before building on it: data layer → analytics → interpreter → API → UI → tests → docs.
4. **AI as adversary.** After the code worked, I had the assistant attack it: a battery of ~35 tricky questions (SQL injection, unknown regions, "May" the verb, creative requests, ambiguous asks) and then deliberately injected bugs to see if the tests noticed.
5. **Nothing trusted on sight.** Every number was checked against an independent calculation, not against the code that produced it.

## 3. Problems encountered and how they were debugged

| # | Problem | How it was found | Fix |
|---|---|---|---|
| 1 | **Which "free" LLM?** Free cloud tiers change often and may train on inputs | Web research on current Gemini free-tier terms | Default to local Ollama; Gemini optional; plus a no-LLM mode so the app never depends on any provider |
| 2 | **Ambiguous dirty data**: what to do with a returned order, zero quantity, missing price? | Inspecting the CSV | Returned → keep but exclude from revenue and disclose; zero qty and missing price → quarantine in `rejected_records` with reasons (a guessed price would corrupt revenue). Assumptions documented |
| 3 | **Misleading note**: comparing Jan vs Feb said "Excluded 1 returned order worth ₹19,000", but that return was in April | Reading the real output rather than only checking the headline number | Scoped the note to the months being compared; test added |
| 4 | **`DROP TABLE sales; --` returned a monthly trend**: the word "drop" matched my "revenue dropped" keyword | The adversarial question battery | Added a guard that refuses SQL/write attempts first; verified that normal sentences using "dropped"/"update" still work |
| 5 | **Silent wrong answers**: "revenue for Central region" would have returned the *overall* total because the unknown word was ignored | Reasoning about failure modes while reading the interpreter | "Unknown-term guard" refuses any unresolvable filter phrase; applied to the LLM path too, since an LLM can also drop a filter |
| 6 | **My own test was wrong**: I assumed East had no January sales; the code correctly returned TXN0004 | Test failed; I checked the CSV before touching code | Fixed the test data assumption (the code was right). Lesson: when a test fails, find out which side is wrong |
| 7 | **Rule-based fallback weaknesses**: false refusal of "…brought in the most *money*", missed "Where do we sell the least?", and "Write me a poem about *sales*" returned total revenue | `scripts/check_llm.py` run with the LLM unreachable (it exercises the fallback) | Added filler words, `where` → region, `sell`, and an off-topic blocklist; regression tests for each |
| 8 | **A code edit silently didn't apply** (string replace with wrong escaping) | A grep that should have matched printed nothing | Re-applied with a line-targeted edit and added a test for the behaviour |
| 9 | **"May" the month vs "May I know…"** | Anticipated up front | Context-based month detection with tests both ways |
| 10 | **Could not run a live LLM** in the build environment | Sandbox network limits | Tested the full LLM path with fakes and a fake Ollama HTTP server; wrote `scripts/check_llm.py` to verify a real model in 30 seconds |

**Debugging approach used throughout:** reproduce with the smallest input → read the actual output → decide whether the code, the test, or my assumption is wrong → fix → add a regression test → re-run everything.

## 4. How I checked that the AI-generated work is correct

* Independent pandas recomputation of total revenue, product/region ranking and every monthly figure (all matched).
* **Mutation checks:** temporarily broke the code four ways (ignore discounts, count returns, accept zero quantity, disable the unknown-term guard); the test suite failed each time.
* A fake Ollama server to test the real HTTP contract, not just mocks of my own functions.
* Verified the live server over HTTP with `curl` for success, 400, 422 and an injection attempt, then confirmed the row count was unchanged.

## 5. What I would improve with another day

1. **Run and tune against a real LLM**: measure routing accuracy on ~100 labelled paraphrases; try `qwen2.5:3b` vs larger models; add few-shot examples for failures.
2. **Validated text-to-SQL as a second tier** for questions no intent covers: parse with a SQL parser, allow only a single `SELECT` on `sales`, enforce `status='Completed'`, add `LIMIT`, run on the read-only connection, label the answer "ad-hoc query – verify".
3. **Cross-check LLM vs rules**: when both produce an intent and they disagree, ask the user to clarify instead of picking one.
4. **Conversation memory** ("and for the South?") and richer time handling (quarters, "last month" relative to the data's latest date).
5. **Charts** for trends and rankings; CSV export of supporting data.
6. **Evaluation harness** in CI that runs the labelled question set against the LLM and tracks accuracy over time.
7. **Retrieval for metadata (RAG-style):** as the schema/glossary grows ("revenue", "net sales", "AOV"), retrieve the relevant definitions into the prompt instead of hard-coding them.
8. **Production concerns:** auth, rate limiting, structured logging with request IDs, Docker image, caching the value catalog.
