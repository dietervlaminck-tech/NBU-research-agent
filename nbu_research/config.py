import os

# Models — exact IDs from the Anthropic Models API. Do not append date suffixes.
MODEL_OPUS = "claude-opus-4-8"
MODEL_SONNET = "claude-sonnet-4-6"
MODEL_HAIKU = "claude-haiku-4-5"

# Live respondent-facing chat favors latency/cost; pipelines favor capability.
DEFAULT_INTERVIEW_MODEL = MODEL_SONNET
DEFAULT_PIPELINE_MODEL = MODEL_OPUS

AVAILABLE_MODELS = [
    (MODEL_OPUS, "Claude Opus 4.8 — most capable"),
    (MODEL_SONNET, "Claude Sonnet 4.6 — fast & smart"),
    (MODEL_HAIKU, "Claude Haiku 4.5 — fastest"),
]

DATA_DIR = os.environ.get(
    "NBU_DATA_DIR", os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
)
DB_PATH = os.path.join(DATA_DIR, "research.db")

SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key-change-me")


def anthropic_api_key():
    return os.environ.get("ANTHROPIC_API_KEY")


# --- LSEG / Refinitiv connector (optional) -----------------------------------
# Two session modes (see docs/REFINITIV_DESIGN.md):
#   desktop  — the lseg-data library talks to a Refinitiv Workspace app running
#              on the SAME machine (named-user seat, e.g. eikon2@nyenrode.nl).
#              Needs only LSEG_APP_KEY (a desktop app key) + Workspace running.
#              Cannot run on a headless server.
#   platform — server-capable: LSEG_APP_KEY + a machine account
#              (LSEG_CLIENT_ID / LSEG_CLIENT_SECRET). For Azure hosting.
LSEG_SESSION = os.environ.get("LSEG_SESSION", "desktop")  # desktop | platform
LSEG_APP_KEY = os.environ.get("LSEG_APP_KEY", "")
LSEG_CLIENT_ID = os.environ.get("LSEG_CLIENT_ID", "")
LSEG_CLIENT_SECRET = os.environ.get("LSEG_CLIENT_SECRET", "")


def lseg_configured():
    """True when the Refinitiv connector has enough config to attempt a session.
    Desktop needs only the app key; platform also needs the machine account."""
    if not LSEG_APP_KEY:
        return False
    if LSEG_SESSION == "platform":
        return bool(LSEG_CLIENT_ID and LSEG_CLIENT_SECRET)
    return True
