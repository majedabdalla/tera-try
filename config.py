"""
All configuration loaded from environment variables.
Local dev: copy env.example → .env and fill in values.
Railway:   set these in the Variables tab — no .env file needed.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── Telegram ──────────────────────────────────────────────────────────────────
API_ID   = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

# ── MongoDB ───────────────────────────────────────────────────────────────────
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
DB_NAME   = os.environ.get("DB_NAME", "terabox_bot")

# ── Bot Settings ──────────────────────────────────────────────────────────────
PRIVATE_CHAT_ID = int(os.environ.get("PRIVATE_CHAT_ID", 0))

ADMINS = [
    int(x) for x in os.environ.get("ADMINS", "").split(",") if x.strip().isdigit()
]
ADMIN_ID = ADMINS[0] if ADMINS else 0

# Channels users must join before they can download (comma-separated @handles).
# Leave empty to disable the check entirely.
REQUIRED_CHANNELS = [
    ch.strip()
    for ch in os.environ.get("REQUIRED_CHANNELS", "").split(",")
    if ch.strip()
]

# ── Anti-Spam ─────────────────────────────────────────────────────────────────
FREE_COOLDOWN        = int(os.environ.get("FREE_COOLDOWN", 60))
PREMIUM_COOLDOWN     = int(os.environ.get("PREMIUM_COOLDOWN", 30))
MAX_USAGE_PER_WINDOW = int(os.environ.get("MAX_USAGE_PER_WINDOW", 5))
MAX_FILE_SIZE        = int(os.environ.get("MAX_FILE_SIZE", 524288000))  # 500 MB

# ── Terabox ───────────────────────────────────────────────────────────────────
# Your own Terabox session cookie (free account works for public shared links).
# How to get it:
#   1. Log in at https://www.terabox.com in Chrome
#   2. F12 → Network tab → click any request → copy the "Cookie:" header value
#   3. Paste the entire string here (it's long — that's normal)
TERABOX_COOKIE = os.environ.get("TERABOX_COOKIE", "")

# ── Bot Branding (YOUR info — replaces all original-author references) ─────────
BOT_NAME    = os.environ.get("BOT_NAME", "Terabox Downloader Bot")
# Your public Telegram channel handle, e.g. "@mychannel"  (leave blank to hide)
BOT_CHANNEL = os.environ.get("BOT_CHANNEL", "")
# Your public Telegram group handle, e.g. "@mygroup"  (leave blank to hide)
BOT_GROUP   = os.environ.get("BOT_GROUP", "")
# Your Telegram @username  (leave blank to hide the Owner button)
BOT_OWNER   = os.environ.get("BOT_OWNER", "")
# GitHub / source-code URL  (leave blank to hide the button)
GITHUB_URL  = os.environ.get("GITHUB_URL", "")
# Prefix used when generating gift codes, e.g. "VIP" → VIP-A1B2C3D4
GIFT_PREFIX = os.environ.get("GIFT_PREFIX", "GIFT")
