"""
PredictionLab Site Checker — Configuration
All tuneable settings live here.
Email credentials: edit qa/checker_email.py (env vars override if set).
"""

import os
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────
QA_DIR          = Path(__file__).parent
REPO_ROOT       = QA_DIR.parent
HISTORY_DIR     = QA_DIR / "audit_history"
SCREENSHOTS_DIR = QA_DIR / "screenshots"

# Ensure dirs exist
HISTORY_DIR.mkdir(exist_ok=True)
SCREENSHOTS_DIR.mkdir(exist_ok=True)

# ── Target site ────────────────────────────────────────────────────────────
BASE_URL = os.environ.get("AUDIT_BASE_URL", "http://127.0.0.1:5003")

# ── Email — load from checker_email.py, then override with env vars ────────
def _load_email_config():
    cfg = {"EMAIL_FROM": "", "EMAIL_PASSWORD": "",
           "EMAIL_TO": "nmesghali@gmail.com",
           "SMTP_HOST": "smtp.gmail.com", "SMTP_PORT": 587}
    try:
        import importlib.util, sys
        spec = importlib.util.spec_from_file_location(
            "checker_email", QA_DIR / "checker_email.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        for k in cfg:
            v = getattr(mod, k, None)
            if v:
                cfg[k] = v
    except Exception:
        pass
    # Env vars always win
    cfg["EMAIL_FROM"]     = os.environ.get("AUDIT_EMAIL_FROM",     cfg["EMAIL_FROM"])
    cfg["EMAIL_PASSWORD"] = os.environ.get("AUDIT_EMAIL_PASSWORD", cfg["EMAIL_PASSWORD"])
    cfg["EMAIL_TO"]       = os.environ.get("AUDIT_EMAIL_TO",       cfg["EMAIL_TO"])
    cfg["SMTP_HOST"]      = os.environ.get("AUDIT_SMTP_HOST",      cfg["SMTP_HOST"])
    cfg["SMTP_PORT"]      = int(os.environ.get("AUDIT_SMTP_PORT",  str(cfg["SMTP_PORT"])))
    return cfg

_email_cfg   = _load_email_config()
EMAIL_FROM   = _email_cfg["EMAIL_FROM"]
EMAIL_PASSWORD = _email_cfg["EMAIL_PASSWORD"]
EMAIL_TO     = _email_cfg["EMAIL_TO"]
SMTP_HOST    = _email_cfg["SMTP_HOST"]
SMTP_PORT    = _email_cfg["SMTP_PORT"]

# ── HTTP settings ──────────────────────────────────────────────────────────
REQUEST_TIMEOUT   = int(os.environ.get("AUDIT_TIMEOUT",   "12"))
PREFLIGHT_TIMEOUT = int(os.environ.get("AUDIT_PREFLIGHT_TIMEOUT", "5"))
MAX_REDIRECTS     = 3
USER_AGENT        = "PredictionLab-QA/1.0 (internal audit bot)"

# ── Sport slugs ───────────────────────────────────────────────────────────
SPORT_PICKS_SLUGS = [
    "nba-picks", "nhl-picks", "nfl-picks", "mlb-picks",
    "ncaab-picks", "ncaaf-picks", "ncaaw-picks", "wnba-picks", "soccer-picks",
    "tennis-picks", "ufc-picks", "golf-picks",
]
SPORT_RESULTS_SLUGS = [s.replace("-picks", "-results") for s in SPORT_PICKS_SLUGS]

# Sport display names that MUST appear on the homepage "Today's Picks by Sport"
# grid and the All-Sports-Results dashboard. If a sport is added to the app but
# missing from a dashboard, the checker flags it.
EXPECTED_DASHBOARD_SPORTS = [
    "NHL", "NBA", "MLB", "NFL", "NCAAB", "NCAAW", "NCAAF", "WNBA", "Soccer",
    "Tennis", "UFC", "Golf",
]

# ── Model labels ──────────────────────────────────────────────────────────
MODEL_DISPLAY_NAMES = ["Grinder2", "Takedown", "Edge", "XSharp", "Consensus", "Sharp Consensus"]
MODEL_KEYS          = ["glicko2", "trueskill", "xgb_prob", "xsharp_prob", "consensus_prob"]

# ── Card field CSS classes expected in pick cards ─────────────────────────
CARD_REQUIRED_CLASSES = [
    "league-badge",
    "team-name",
    "win-pct",
    "ai-pick-text",
    "confidence",
]

# ── Stale / forbidden strings ─────────────────────────────────────────────
STALE_STRINGS = [
    "underdogs.bet",
    "underdogs_bet",
    "TODO",
    "FIXME",
    "lorem ipsum",
    "test button",
    "sample data",
    "placeholder text",
    "coming soon",
]

# Content strings that should NOT appear in public pages
FORBIDDEN_CONTENT = [
    "underdogs.bet",
    "underdogs_bet",
]

# ── Screenshot pages ──────────────────────────────────────────────────────
SCREENSHOT_PAGES = [
    {"path": "/",              "name": "homepage"},
    {"path": "/nba-picks",     "name": "nba_picks"},
    {"path": "/all-sports-results", "name": "results"},
    {"path": "/daily-report",  "name": "daily_report"},
    {"path": "/plans",         "name": "plans"},
]

# ── Results math tolerance ────────────────────────────────────────────────
RESULTS_PCT_TOLERANCE = 0.5   # allow ±0.5% discrepancy in displayed vs calculated

# ── Clustering detection thresholds ──────────────────────────────────────
CLUSTER_RANGE_LOW  = 49.0
CLUSTER_RANGE_HIGH = 51.0
CLUSTER_WARN_PCT   = 80        # warn if >80% of probs are in the clustering range

# ── Quick mode — only run these auditors ─────────────────────────────────
QUICK_MODE_AUDITORS = ["routes", "content", "navigation"]
FULL_MODE_AUDITORS  = ["routes", "content", "navigation", "cards", "models",
                       "results", "gamecounts", "csv", "props", "seo", "schema",
                       "consistency"]
