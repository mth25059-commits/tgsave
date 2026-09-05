# tgsave — configuration
#
# Values come from config.env sitting next to this file. Nothing here is
# secret by itself; config.env is gitignored and holds the real values.

import os
from dotenv import load_dotenv

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(HERE, "config.env")

# A hand-edited config.env is not always clean UTF-8 — Notepad and anything that
# went through a Windows shell can leave cp1252 bytes in a comment. dotenv raises
# UnicodeDecodeError on those, which would take the whole bot down over a dash.
try:
    load_dotenv(ENV_FILE, override=True, encoding="utf-8")
except UnicodeDecodeError:
    load_dotenv(ENV_FILE, override=True, encoding="latin-1")


def _int(name: str, default: int = 0) -> int:
    try:
        return int((os.getenv(name) or "").strip())
    except ValueError:
        return default


def _str(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


# ---- credentials -----------------------------------------------------------
API_ID = _int("API_ID")
API_HASH = _str("API_HASH")
BOT_TOKEN = _str("BOT_TOKEN")
ADMIN_ID = _int("ADMIN_ID")

# ---- paths -----------------------------------------------------------------
DATA_DIR = os.path.join(HERE, _str("DATA_DIR", "data"))
DL_DIR = os.path.join(DATA_DIR, "downloads")
SESSION_NAME = "owner"          # data/owner.session holds the logged-in account

# ---- behaviour -------------------------------------------------------------
MAX_BATCH = _int("MAX_BATCH", 200) or 200
BATCH_GAP = 1.5                 # seconds between posts in a batch
PROGRESS_EVERY = 5.0            # seconds between progress-bar edits
CAPTION_LIMIT = 1024


def problems() -> list:
    """Return a list of human-readable config problems. Empty list = good to go."""
    out = []
    if not API_ID:
        out.append("API_ID is missing — get it from https://my.telegram.org")
    if not API_HASH:
        out.append("API_HASH is missing — get it from https://my.telegram.org")
    if not BOT_TOKEN:
        out.append("BOT_TOKEN is missing — get it from @BotFather")
    if not ADMIN_ID:
        out.append("ADMIN_ID is missing — your numeric id, ask @userinfobot")
    return out
