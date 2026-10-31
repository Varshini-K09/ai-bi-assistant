"""Central configuration. Everything is read from environment variables so the
app can be switched between LLM providers without touching code."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

CSV_PATH = Path(os.getenv("SALES_CSV_PATH", BASE_DIR / "data" / "sales.csv"))
DB_PATH = Path(os.getenv("SALES_DB_PATH", BASE_DIR / "data" / "sales.db"))


def _load_dotenv() -> None:
    """Tiny .env loader (avoids an extra dependency). Real env vars win."""
    env_file = BASE_DIR / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama").lower()
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:3b")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
LLM_TIMEOUT_SECONDS = float(os.getenv("LLM_TIMEOUT_SECONDS", "60"))

MAX_QUESTION_LENGTH = 500
