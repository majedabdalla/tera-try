"""
MongoDB wrapper — replaces Redis.
Collections:
  users          → all users who have interacted with the bot
  premium_users  → users with a paid plan
  gift_codes     → unredeemed codes
  rate_limits    → cooldowns & per-window usage counters (TTL auto-delete)
  cache          → shorturl → Telegram file_id  (TTL auto-delete)
"""

import logging
from datetime import datetime, timedelta

from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError

log = logging.getLogger(__name__)


class Database:
    def __init__(self, uri: str, db_name: str = "terabox_bot"):
        self.client = MongoClient(uri)
        self.db = self.client[db_name]
        self._setup_indexes()
        log.info("✅ MongoDB connected — database: '%s'", db_name)

    # ─────────── Indexes ───────────
    def _setup_indexes(self):
        # TTL indexes: MongoDB auto-deletes docs when expires_at is reached
        self.db.rate_limits.create_index("expires_at", expireAfterSeconds=0)
        self.db.cache.create_index("expires_at", expireAfterSeconds=0)
        # Unique indexes
        self.db.users.create_index("user_id", unique=True)
        self.db.premium_users.create_index("user_id", unique=True)
        self.db.gift_codes.create_index("code", unique=True)

    # ─────────── Users ───────────
    def save_user(self, user_id: int, name: str, username: str = None):
        """Upsert user — updates name/username on every call."""
        self.db.users.update_one(
            {"user_id": user_id},
            {
                "$set": {
                    "name": name,
                    "username": username,
                    "last_seen": datetime.utcnow(),
                },
                "$setOnInsert": {"joined_at": datetime.utcnow()},
            },
            upsert=True,
        )

    def get_all_user_ids(self) -> list:
        return [d["user_id"] for d in self.db.users.find({}, {"user_id": 1})]

    def get_user_count(self) -> int:
        return self.db.users.count_documents({})

    # ─────────── Premium ───────────
    def is_premium(self, user_id: int) -> bool:
        return self.db.premium_users.count_documents({"user_id": user_id}) > 0

    def add_premium(self, user_id: int) -> bool:
        try:
            self.db.premium_users.insert_one(
                {"user_id": user_id, "added_at": datetime.utcnow()}
            )
            return True
        except DuplicateKeyError:
            return False  # Already premium

    def remove_premium(self, user_id: int) -> bool:
        return self.db.premium_users.delete_one({"user_id": user_id}).deleted_count > 0

    def get_all_premium_user_ids(self) -> list:
        return [d["user_id"] for d in self.db.premium_users.find({}, {"user_id": 1})]

    def clear_all_premium(self):
        self.db.premium_users.delete_many({})

    # ─────────── Gift Codes ───────────
    def add_gift_codes(self, codes: list):
        docs = [{"code": c, "created_at": datetime.utcnow()} for c in codes]
        try:
            self.db.gift_codes.insert_many(docs, ordered=False)
        except Exception:
            pass  # Silently skip duplicates

    def is_valid_code(self, code: str) -> bool:
        return self.db.gift_codes.count_documents({"code": code}) > 0

    def consume_code(self, code: str) -> bool:
        return self.db.gift_codes.delete_one({"code": code}).deleted_count > 0

    # ─────────── Rate Limiting ───────────
    def is_on_cooldown(self, user_id: int) -> bool:
        """True if user sent a request too recently."""
        return (
            self.db.rate_limits.count_documents({"user_id": user_id, "type": "cd"}) > 0
        )

    def set_cooldown(self, user_id: int, seconds: int):
        self.db.rate_limits.update_one(
            {"user_id": user_id, "type": "cd"},
            {"$set": {"expires_at": datetime.utcnow() + timedelta(seconds=seconds)}},
            upsert=True,
        )

    def get_usage_count(self, user_id: int) -> int:
        doc = self.db.rate_limits.find_one({"user_id": user_id, "type": "usage"})
        return doc["count"] if doc else 0

    def increment_usage(self, user_id: int, window_hours: int = 2):
        doc = self.db.rate_limits.find_one({"user_id": user_id, "type": "usage"})
        if doc:
            self.db.rate_limits.update_one(
                {"user_id": user_id, "type": "usage"},
                {"$inc": {"count": 1}},
            )
        else:
            self.db.rate_limits.insert_one(
                {
                    "user_id": user_id,
                    "type": "usage",
                    "count": 1,
                    "expires_at": datetime.utcnow() + timedelta(hours=window_hours),
                }
            )

    def clear_limits(self, user_id: int):
        """Admin command: wipe all rate-limit docs for a user."""
        self.db.rate_limits.delete_many({"user_id": user_id})

    # ─────────── File Cache ───────────
    def get_cached_file(self, shorturl: str):
        doc = self.db.cache.find_one({"shorturl": shorturl})
        return doc["file_id"] if doc else None

    def cache_file(self, shorturl: str, file_id: int, ttl_days: int = 7):
        self.db.cache.update_one(
            {"shorturl": shorturl},
            {
                "$set": {
                    "file_id": file_id,
                    "expires_at": datetime.utcnow() + timedelta(days=ttl_days),
                }
            },
            upsert=True,
        )
