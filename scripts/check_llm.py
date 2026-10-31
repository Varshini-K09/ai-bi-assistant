"""Smoke-test the REAL configured LLM (Ollama / Gemini) in about 30 seconds.

    python scripts/check_llm.py

Sends paraphrased and adversarial questions through the interpreter and prints
how each was routed. 'rules (LLM unavailable)' in the Source column means the
LLM could not be reached or returned unusable output - the app still works, but
you are not exercising the model. Exit code 1 if the LLM is never used.
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config  # noqa: E402
from app.analytics import load_catalog  # noqa: E402
from app.database import build_database, get_readonly_connection  # noqa: E402
from app.interpreter import interpret  # noqa: E402
from app.llm import LLMError, get_provider  # noqa: E402

QUESTIONS = [  # (question, expected intent)
    ("What is the total revenue?", "total_revenue"),
    ("how much money did we make overall", "total_revenue"),
    ("Which item brought in the most money?", "top_by"),
    ("Where do we sell the least?", "top_by"),
    ("Show me revenue month by month", "monthly_trend"),
    ("Did sales go up or down from Feb to Mar?", "month_comparison"),
    ("How much did monitors earn in the North in May?", "total_revenue"),
    ("Which rows had bad data?", "data_quality"),
    ("What is our profit margin?", None),
    ("Write me a poem about sales", None),
]


def main() -> int:
    try:
        provider = get_provider()
    except LLMError as exc:
        print(f"Provider error: {exc}")
        return 1
    if provider is None:
        print("LLM_PROVIDER=none -> nothing to check (rule-based mode).")
        return 0
    print(f"Provider: {provider.name}  model: {getattr(provider, 'model', '?')}\n")

    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "check.db"
        build_database(config.CSV_PATH, db)
        conn = get_readonly_connection(db)
        catalog = load_catalog(conn)
        conn.close()

    llm_used = correct = 0
    for question, expected in QUESTIONS:
        r = interpret(question, catalog, provider)
        used = r.source.startswith("llm")
        llm_used += used
        ok = r.intent == expected
        correct += ok
        print(f"{'OK ' if ok else 'XX '} {question!r:55} -> {str(r.intent):17} [{r.source}]"
              + (f"  ({r.llm_error})" if r.llm_error else ""))
    print(f"\nLLM handled {llm_used}/{len(QUESTIONS)}; routed correctly {correct}/{len(QUESTIONS)}.")
    if llm_used == 0:
        print("The LLM was never used. Is Ollama running and the model pulled? (ollama pull llama3.2:3b)")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
