"""
All configuration is loaded from environment variables.
For local dev, copy .env.example → .env and fill in the values.
For Railway: set these in the Railway dashboard → Variables tab.
"""

import os

from dotenv import load_dotenv

load_dotenv()  # No-op on Railway; reads .env locally

# ── Telegram ──────────────────────────────────────────────────────────────────
# Get API_ID and API_HASH from https://my.telegram.org/apps
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")

# Bot token from @BotFather
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

# ── MongoDB ───────────────────────────────────────────────────────────────────
# Use MongoDB Atlas free tier: https://www.mongodb.com/cloud/atlas
# Format: mongodb+srv://user:password@cluster.mongodb.net/
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "terabox_bot")

# ── Bot Settings ──────────────────────────────────────────────────────────────
# Private Telegram channel/group where uploaded files are stored
# The bot MUST be an admin there with "Post messages" permission
PRIVATE_CHAT_ID = int(os.environ.get("PRIVATE_CHAT_ID", 0))

# Comma-separated list of admin Telegram user IDs e.g. "123456789,987654321"
ADMINS = [
    int(x) for x in os.environ.get("ADMINS", "").split(",") if x.strip().isdigit()
]
ADMIN_ID = ADMINS[0] if ADMINS else 0

# Optional: channels users must join before they can use the bot
# Comma-separated: "@mychannel,@mygroup"  — leave empty to disable
REQUIRED_CHANNELS = [
    ch.strip()
    for ch in os.environ.get("REQUIRED_CHANNELS", "").split(",")
    if ch.strip()
]

# ── Anti-Spam ─────────────────────────────────────────────────────────────────
FREE_COOLDOWN = int(os.environ.get("FREE_COOLDOWN", 60))        # seconds between requests
PREMIUM_COOLDOWN = int(os.environ.get("PREMIUM_COOLDOWN", 30))  # seconds between requests
MAX_USAGE_PER_WINDOW = int(os.environ.get("MAX_USAGE_PER_WINDOW", 5))  # per 2-hour window

# Max file size in bytes (default 500 MB)
MAX_FILE_SIZE = int(os.environ.get("MAX_FILE_SIZE", 524288000))

# ── Terabox API ───────────────────────────────────────────────────────────────
# The bot relies on a third-party API to resolve Terabox download links.
# "NTMPASS" is the original author's token — replace with your own if you
# self-host or switch to a different provider.
TERABOX_API_BASE = os.environ.get("TERABOX_API_BASE", "https://api.ntm.com/api/terabox")
TERABOX_API_TOKEN = os.environ.get("TERABOX_API_TOKEN", "NTMPASS")

# Do NOT change {{url}} — it's a format placeholder used at call time
TERABOX_API_TEMPLATE = f"{TERABOX_API_BASE}?key={TERABOX_API_TOKEN}&url={{url}}"
