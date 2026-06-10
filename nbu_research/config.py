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
