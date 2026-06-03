import asyncio
import logging
import os
import time
from uuid import uuid4

import requests
import telethon
import telethon.tl.types
from telethon import Button, TelegramClient, events
from telethon.tl.functions.messages import ForwardMessagesRequest
from telethon.types import Message, UpdateNewMessage

from cansend import CanSend
from config import (
    ADMINS,
    API_HASH,
    API_ID,
    BOT_CHANNEL,
    BOT_GROUP,
    BOT_NAME,
    BOT_OWNER,
    BOT_TOKEN,
    DB_NAME,
    FREE_COOLDOWN,
    GIFT_PREFIX,
    GITHUB_URL,
    MAX_FILE_SIZE,
    MAX_USAGE_PER_WINDOW,
    MONGO_URI,
    PREMIUM_COOLDOWN,
    PRIVATE_CHAT_ID,
    REQUIRED_CHANNELS,
    TERABOX_COOKIE,
)
from database import Database
from terabox import _BASE, _h, _cookie_field, extract_surl, get_data
from tools import (
    convert_seconds,
    download_file,
    download_image_to_bytesio,
    extract_code_from_url,
    get_formatted_size,
    get_urls_from_string,
    is_user_on_chat,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(__name__)

bot = TelegramClient("tele", API_ID, API_HASH)
db  = Database(MONGO_URI, DB_NAME)

SUPPORTED_EXTENSIONS = {".mp4", ".mkv", ".webm", ".avi", ".mov", ".flv", ".m4v"}


# ── Branding helpers ──────────────────────────────────────────────────────────

def nav_buttons():
    rows = []
    if GITHUB_URL:
        rows.append([Button.url("Source Code", url=GITHUB_URL)])
    mid = []
    if BOT_CHANNEL:
        mid.append(Button.url("Channel", url=f"https://t.me/{BOT_CHANNEL.lstrip('@')}"))
    if BOT_GROUP:
        mid.append(Button.url("Group", url=f"https://t.me/{BOT_GROUP.lstrip('@')}"))
    if mid:
        rows.append(mid)
    if BOT_OWNER:
        rows.append([Button.url("Owner", url=f"https://t.me/{BOT_OWNER.lstrip('@')}")])
    return rows or None


def contact_text():
    if BOT_OWNER:
        return f"Contact {BOT_OWNER} for assistance."
    return "Contact the bot administrator for assistance."


def footer():
    return f"\n{BOT_CHANNEL}" if BOT_CHANNEL else ""


async def check_required_channels(m: UpdateNewMessage) -> bool:
    if not REQUIRED_CHANNELS:
        return True
    for ch in REQUIRED_CHANNELS:
        if not await is_user_on_chat(bot, ch, m.peer_id):
            joined = " and ".join(REQUIRED_CHANNELS)
            await m.reply(
                f"⚠️ Please join {joined} first, then send the link again.",
                link_preview=False,
            )
            return False
    return True


# ── /start ────────────────────────────────────────────────────────────────────

@bot.on(events.NewMessage(pattern="/start", incoming=True, outgoing=False))
async def start(m: UpdateNewMessage):
    user_id  = m.sender_id
    user     = await bot.get_entity(user_id)
    name     = user.first_name
    username = user.username or "-"
    db.save_user(user_id, name, username)

    for admin_id in ADMINS:
        try:
            await bot.send_message(
                admin_id,
                f"👤 /start\nName: {name}\nUsername: @{username}\nID: `{user_id}`",
                parse_mode="markdown",
            )
        except Exception:
            pass

    if db.is_premium(user_id):
        body = (
            f"┏━━━━━━━━━━⍟\n"
            f"┃ {BOT_NAME}\n"
            f"┗━━━━━━━━━━━━━━━━━⍟\n"
            f"╔══════════⍟\n"
            f"┃ 🌟 Welcome back, Premium User!\n"
            f"┃\n"
            f"┃ Send any Terabox link and I'll\n"
            f"┃ deliver the file instantly. 🚀\n"
            f"╚═════════════════⍟\n"
            f"Use /help to see all commands."
        )
    else:
        body = (
            f"┏━━━━━━━━━━⍟\n"
            f"┃ FREE USER\n"
            f"┗━━━━━━━━━━━━━━━━━⍟\n"
            f"╔══════════⍟\n"
            f"┃ You have limited access.\n"
            f"┃\n"
            f"┃ /cmds or /help — available commands\n"
            f"┃ /id or /info   — your account details\n"
            f"┃ /plan          — see premium plans\n"
            f"╚═════════════════⍟\n"
            f"{contact_text()}"
        )
    await m.reply(body, link_preview=False, parse_mode="markdown", buttons=nav_buttons())


# ── /info  /id ────────────────────────────────────────────────────────────────

@bot.on(events.NewMessage(pattern=r"^/(info|id)$", incoming=True, outgoing=False))
async def user_info(m: UpdateNewMessage):
    user_id  = m.sender_id
    user     = await bot.get_entity(user_id)
    name     = user.first_name
    username = user.username or "-"
    db.save_user(user_id, name, username)
    plan = "💎 Premium" if db.is_premium(user_id) else "🆓 Free"
    await m.reply(
        f"**Name:** {name}\n**Username:** @{username}\n**User ID:** `{user_id}`\n**Plan:** {plan}",
        parse_mode="markdown",
    )


# ── /help  /cmds ──────────────────────────────────────────────────────────────

@bot.on(
    events.NewMessage(
        pattern=r"^/(help|cmds)$", incoming=True, outgoing=False,
        func=lambda x: x.is_private,
    )
)
async def command_help(m: UpdateNewMessage):
    await m.reply(
        "┏━━━━━━━━━━⍟\n"
        "┃ Available Commands\n"
        "┗━━━━━━━━━━━━━━━━━⍟\n\n"
        "/start         — Welcome message\n"
        "/info or /id   — Your user details\n"
        "/redeem <code> — Redeem a gift code\n"
        "/plan          — See available plans\n"
        "/ping          — Check bot latency\n"
        "/help          — This message\n\n"
        "📎 Send any Terabox link to download!",
        link_preview=False,
        parse_mode="markdown",
        buttons=nav_buttons(),
    )


# ── /ping ─────────────────────────────────────────────────────────────────────

@bot.on(events.NewMessage(pattern=r"^/ping$", incoming=True, outgoing=False))
async def ping_pong(m: UpdateNewMessage):
    t   = time.time()
    msg = await m.reply("🖥️ Measuring latency…")
    ms  = round((time.time() - t) * 1000, 2)
    await msg.edit(f"🖥️ **Pong!** `{ms} ms`", parse_mode="markdown")


# ── /plan ─────────────────────────────────────────────────────────────────────

@bot.on(events.NewMessage(pattern=r"^/plan$", incoming=True, outgoing=False))
async def display_plan(m: UpdateNewMessage):
    await m.reply(
        f"┏━━━━━━━━━━⍟\n"
        f"┃ {BOT_NAME} — Plans\n"
        f"┗━━━━━━━━━━━━━━━━━⍟\n\n"
        f"💎 Membership Plans:\n"
        f"• Rs. 100 — 10 days\n"
        f"• Rs.  60 — 4 days\n"
        f"• Rs.  30 — 2 days\n"
        f"• Rs.  20 — 1 day\n\n"
        f"💳 Payment: UPI · Esewa · Khalti · PhonePay · PayPal\n"
        f"✅ Nepal & India payments accepted.\n\n"
        f"📩 {contact_text()}",
        parse_mode="markdown",
    )


# ── /redeem ───────────────────────────────────────────────────────────────────

@bot.on(events.NewMessage(pattern=r"^/redeem (.+)$", incoming=True, outgoing=False))
async def redeem_gift_code(m: UpdateNewMessage):
    code = m.pattern_match.group(1).strip()
    if not db.is_valid_code(code):
        return await m.reply("❌ Invalid or expired gift code.")
    user_id  = m.sender_id
    user     = await bot.get_entity(user_id)
    name     = user.first_name
    username = user.username or "-"
    db.consume_code(code)
    db.add_premium(user_id)
    db.save_user(user_id, name, username)
    for admin_id in ADMINS:
        try:
            await bot.send_message(
                admin_id,
                f"🎁 Code redeemed\nName: {name}\nUsername: @{username}\nID: {user_id}",
            )
        except Exception:
            pass
    await m.reply(
        "✅ Gift code redeemed! You are now a **Premium** user. 🎉",
        parse_mode="markdown",
    )


# ── Admin: /gc ────────────────────────────────────────────────────────────────

@bot.on(events.NewMessage(pattern=r"^/gc (\d+)$", incoming=True, outgoing=False, from_users=ADMINS))
async def generate_gift_codes(m: UpdateNewMessage):
    qty   = int(m.pattern_match.group(1))
    codes = [f"{GIFT_PREFIX}-{str(uuid4())[:8].upper()}" for _ in range(qty)]
    db.add_gift_codes(codes)
    lines = "\n".join(f"`{c}`" for c in codes)
    await m.reply(f"✅ Generated **{qty}** codes:\n\n{lines}", parse_mode="markdown")


# ── Admin: /pre ───────────────────────────────────────────────────────────────

@bot.on(events.NewMessage(pattern=r"^/pre (.+)$", incoming=True, outgoing=False, from_users=ADMINS))
async def promote_user(m: UpdateNewMessage):
    try:
        uid = int(m.pattern_match.group(1).strip())
    except ValueError:
        return await m.reply("❌ Usage: /pre <user_id>")
    if db.add_premium(uid):
        await m.reply(f"✅ `{uid}` promoted to premium.", parse_mode="markdown")
    else:
        await m.reply(f"ℹ️ `{uid}` is already premium.", parse_mode="markdown")


# ── Admin: /de ────────────────────────────────────────────────────────────────

@bot.on(events.NewMessage(pattern=r"^/de (.+)$", incoming=True, outgoing=False, from_users=ADMINS))
async def demote_user(m: UpdateNewMessage):
    try:
        uid = int(m.pattern_match.group(1).strip())
    except ValueError:
        return await m.reply("❌ Usage: /de <user_id>")
    if db.remove_premium(uid):
        await m.reply(f"✅ `{uid}` demoted.", parse_mode="markdown")
    else:
        await m.reply(f"ℹ️ `{uid}` is not premium.", parse_mode="markdown")


# ── Admin: /premium_users ─────────────────────────────────────────────────────

@bot.on(events.NewMessage(pattern=r"^/premium_users$", incoming=True, outgoing=False, from_users=ADMINS))
async def list_premium(m: UpdateNewMessage):
    ids = db.get_all_premium_user_ids()
    if not ids:
        return await m.reply("No premium users.")
    lines = []
    for uid in ids:
        try:
            u = await bot.get_entity(uid)
            lines.append(f"• {u.first_name} (@{u.username or '-'}) — `{uid}`")
        except Exception:
            lines.append(f"• Unknown — `{uid}`")
    await m.reply("**💎 Premium Users:**\n" + "\n".join(lines), parse_mode="markdown")


# ── Admin: /remove_premium_user  /demote_all_premium ─────────────────────────

@bot.on(
    events.NewMessage(
        pattern=r"^/(remove_premium_user|demote_all_premium)$",
        incoming=True, outgoing=False, from_users=ADMINS,
    )
)
async def demote_all(m: UpdateNewMessage):
    db.clear_all_premium()
    await m.reply("✅ All premium users demoted.")


# ── Admin: /remove <user_id>  (clear rate limits) ─────────────────────────────

@bot.on(events.NewMessage(pattern=r"^/remove (.+)$", incoming=True, outgoing=False, from_users=ADMINS))
async def remove_rate_limit(m: UpdateNewMessage):
    try:
        uid = int(m.pattern_match.group(1).strip())
    except ValueError:
        return await m.reply("❌ Usage: /remove <user_id>")
    db.clear_limits(uid)
    await m.reply(f"✅ Rate limits cleared for `{uid}`.", parse_mode="markdown")


# ── Admin: /stats ─────────────────────────────────────────────────────────────

@bot.on(events.NewMessage(pattern=r"^/stats$", incoming=True, outgoing=False, from_users=ADMINS))
async def bot_stats(m: UpdateNewMessage):
    total = db.get_user_count()
    prem  = len(db.get_all_premium_user_ids())
    await m.reply(
        f"📊 **Bot Statistics**\n\n"
        f"👥 Total users:   `{total}`\n"
        f"💎 Premium users: `{prem}`",
        parse_mode="markdown",
    )


# ── Admin: /broadcast ─────────────────────────────────────────────────────────

@bot.on(events.NewMessage(pattern=r"^/broadcast", incoming=True, outgoing=False, from_users=ADMINS))
async def broadcast_message(m: UpdateNewMessage):
    text = m.text.split("/broadcast", 1)[1].strip()
    if not text:
        return await m.reply("❌ Usage: /broadcast <message>")
    all_ids = db.get_all_user_ids()
    sent = failed = 0
    status = await m.reply(f"📢 Broadcasting to {len(all_ids)} users…")
    for uid in all_ids:
        try:
            await bot.send_message(uid, text)
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)
    await status.edit(f"✅ Done!\n✅ Sent: {sent}\n❌ Failed: {failed}")


# ── Admin: /check_cookie ──────────────────────────────────────────────────────

@bot.on(events.NewMessage(pattern=r"^/check_cookie$", incoming=True, outgoing=False, from_users=ADMINS))
async def check_cookie(m: UpdateNewMessage):
    """
    Tests whether TERABOX_COOKIE is set and accepted by Terabox.
    Sends results directly in Telegram — no need to check Railway logs.
    """
    msg = await m.reply("🔍 Testing TERABOX_COOKIE…")

    if not TERABOX_COOKIE:
        return await msg.edit(
            "❌ **TERABOX_COOKIE is empty.**\n\n"
            "Set it in Railway → Variables.\n"
            "See env.example for instructions on how to get it.",
            parse_mode="markdown",
        )

    # Check that the cookie string contains the most critical field
    if "ndus=" not in TERABOX_COOKIE:
        await msg.edit(
            "⚠️ Cookie is set but may be incomplete — `ndus` field not found.\n"
            "Make sure you copied the **entire** Cookie header value from DevTools.",
            parse_mode="markdown",
        )
        return

    # Make a lightweight authenticated request to Terabox
    try:
        r = requests.get(
            "https://www.terabox.com/api/quota",
            params={"checkexpire": "1", "checkfree": "1", "app_id": "250528"},
            headers=_h(TERABOX_COOKIE),
            timeout=15,
        )
        data   = r.json()
        errno  = data.get("errno", -1)
        errmsg = data.get("errmsg", "")

        if errno == 0:
            # Parse quota info if available
            total = data.get("total", 0)
            used  = data.get("used",  0)
            free  = total - used
            gb    = lambda b: f"{b / 1024**3:.1f} GB"
            await msg.edit(
                f"✅ **Cookie is valid!**\n\n"
                f"📦 Total: {gb(total)}\n"
                f"📂 Used:  {gb(used)}\n"
                f"🆓 Free:  {gb(free)}",
                parse_mode="markdown",
            )
        elif errno == -6:
            await msg.edit(
                "❌ **Cookie is expired or invalid** (errno -6).\n\n"
                "Go to terabox.com → F12 → Network tab → copy a fresh Cookie header.",
                parse_mode="markdown",
            )
        else:
            await msg.edit(
                f"⚠️ Terabox responded with errno={errno} (`{errmsg}`).\n"
                "Cookie may still work for downloads — try sending a link.",
                parse_mode="markdown",
            )
    except Exception as e:
        await msg.edit(
            f"❌ Request to Terabox failed: `{e}`\n\n"
            "Check that Railway has outbound internet access.",
            parse_mode="markdown",
        )


# ── Admin: /debug_link <url> ──────────────────────────────────────────────────

@bot.on(events.NewMessage(pattern=r"^/debug_link (.+)$", incoming=True, outgoing=False, from_users=ADMINS))
async def debug_link(m: UpdateNewMessage):
    """
    Runs the full Terabox resolver on a URL and reports every step's outcome
    directly in Telegram — useful when a link fails to download.
    """
    url = m.pattern_match.group(1).strip()
    msg = await m.reply(f"🔍 Debugging: `{url}`…", parse_mode="markdown")

    if not TERABOX_COOKIE:
        return await msg.edit("❌ TERABOX_COOKIE is not set.")

    surl = extract_surl(url)
    if not surl:
        return await msg.edit(f"❌ Could not extract surl from:\n`{url}`", parse_mode="markdown")

    h      = _h(TERABOX_COOKIE)
    report = [f"**surl:** `{surl}`"]

    # Step 1 — bdstoken (the errno=4000020 fix)
    bdstoken = _cookie_field(TERABOX_COOKIE, "csrfToken")
    if bdstoken:
        report.append(f"**bdstoken (csrfToken):** ✅ found — `{bdstoken[:16]}…`")
    else:
        report.append("**bdstoken (csrfToken):** ❌ NOT in cookie — this causes errno=4000020")

    # Step 2 — file list WITH bdstoken
    try:
        params = {
            "app_id": "250528", "web": "1", "shorturl": surl,
            "root": "1", "num": "5", "page": "1", "by": "name", "order": "asc",
        }
        if bdstoken:
            params["bdstoken"] = bdstoken
        r     = requests.get("https://www.terabox.com/share/list",
                             params=params, headers=h, timeout=20)
        data  = r.json()
        errno = data.get("errno", -1)
        if errno == 0 and data.get("list"):
            f0 = data["list"][0]
            report.append(
                f"**share/list:** ✅ errno=0\n"
                f"  File: `{f0.get('server_filename')}`\n"
                f"  fs\\_id: `{f0.get('fs_id')}`\n"
                f"  size: `{f0.get('size')}`"
            )
        else:
            hints = {
                -6: " (cookie expired)", -9: " (link dead)",
                2: " (needs password)", 4000020: " (bdstoken missing/wrong)",
            }
            report.append(f"**share/list:** ❌ errno={errno}{hints.get(errno, '')}")
    except Exception as e:
        report.append(f"**share/list:** ❌ exception: {e}")

    # Step 3 — quota / cookie health
    try:
        qr     = requests.get("https://www.terabox.com/api/quota",
                              params={"checkexpire": "1", "app_id": "250528"},
                              headers=h, timeout=10)
        qdata  = qr.json()
        qerrno = qdata.get("errno", -1)
        report.append(
            f"**cookie health:** {'✅ valid' if qerrno == 0 else f'❌ errno={qerrno}'}"
        )
    except Exception as e:
        report.append(f"**cookie health:** ❌ exception: {e}")

    await msg.edit("\n\n".join(report), parse_mode="markdown")


# ── Chat-join tracking ────────────────────────────────────────────────────────

@bot.on(events.ChatAction)
async def user_joined(event):
    if event.user_joined:
        try:
            user = await bot.get_entity(event.user_id)
            db.save_user(event.user_id, user.first_name, user.username)
        except Exception:
            pass


# ── URL message gate ──────────────────────────────────────────────────────────

@bot.on(
    events.NewMessage(
        incoming=True, outgoing=False,
        func=lambda msg: msg.text
            and get_urls_from_string(msg.text)
            and msg.is_private,
    )
)
async def gate_message(m: Message):
    user_id = m.sender_id
    if db.is_premium(user_id) or user_id in ADMINS:
        asyncio.create_task(handle_message(m))
    else:
        await m.reply(
            "⚠️ Downloads are for **Premium** users only.\n\n"
            f"/plan — see pricing\n{contact_text()}",
            parse_mode="markdown",
        )


# ── Core download handler ─────────────────────────────────────────────────────

async def handle_message(m: Message):
    user_id = m.sender_id

    try:
        user = await bot.get_entity(user_id)
        db.save_user(user_id, user.first_name, user.username)
    except Exception:
        pass

    url = get_urls_from_string(m.text)
    if not url:
        return await m.reply("❌ No valid Terabox link found in your message.")

    if not await check_required_channels(m):
        return

    # ── Cooldown ──────────────────────────────────────────────────────────────
    if db.is_on_cooldown(user_id) and user_id not in ADMINS:
        cd = PREMIUM_COOLDOWN if db.is_premium(user_id) else FREE_COOLDOWN
        return await m.reply(f"⏳ Please wait {cd} seconds before your next request.")

    hm = await m.reply("⏳ Fetching your file, please wait…")

    # ── Usage cap ─────────────────────────────────────────────────────────────
    count = db.get_usage_count(user_id)
    if count >= MAX_USAGE_PER_WINDOW and user_id not in ADMINS:
        return await hm.edit(
            "🚫 You've hit your 2-hour usage limit. Please come back later or upgrade."
        )

    # ── Cache lookup ──────────────────────────────────────────────────────────
    shorturl = extract_code_from_url(url)
    if shorturl:
        cached_id = db.get_cached_file(shorturl)
        if cached_id:
            try:
                await hm.delete()
            except Exception:
                pass
            await bot(
                ForwardMessagesRequest(
                    from_peer=PRIVATE_CHAT_ID,
                    id=[int(cached_id)],
                    to_peer=m.chat.id,
                    drop_author=True,
                    background=True,
                    drop_media_captions=False,
                    with_my_score=True,
                )
            )
            cd = PREMIUM_COOLDOWN if db.is_premium(user_id) else FREE_COOLDOWN
            db.set_cooldown(user_id, cd)
            db.increment_usage(user_id)
            return

    # ── Resolve link (now returns tuple with error detail) ────────────────────
    data, resolve_error = get_data(url, cookie=TERABOX_COOKIE)
    if not data:
        return await hm.edit(
            f"❌ Could not resolve this Terabox link.\n\n"
            f"**Reason:** `{resolve_error}`\n\n"
            f"If the reason says _cookie expired_, use /check\\_cookie to diagnose.",
            parse_mode="markdown",
        )

    # ── File-type guard ───────────────────────────────────────────────────────
    ext = os.path.splitext(data["file_name"])[1].lower()
    if ext not in SUPPORTED_EXTENSIONS:
        return await hm.edit(
            f"⚠️ File type `{ext}` is not supported.\n"
            f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}",
            parse_mode="markdown",
        )

    # ── Size guard ────────────────────────────────────────────────────────────
    if int(data["sizebytes"]) > MAX_FILE_SIZE and user_id not in ADMINS:
        return await hm.edit(
            f"⚠️ File too large: **{data['size']}**\n"
            f"Limit: {get_formatted_size(MAX_FILE_SIZE)}",
            parse_mode="markdown",
        )

    # ── Progress bar ──────────────────────────────────────────────────────────
    start_time = time.time()
    cansend    = CanSend()

    async def progress_bar(current, total, state="Uploading"):
        if not cansend.can_send():
            return
        elapsed = time.time() - start_time
        pct     = current / total if total else 0
        bar     = "█" * int(pct * 20) + "░" * (20 - int(pct * 20))
        speed   = current / elapsed if elapsed > 0 else 0
        eta     = (total - current) / speed if speed > 0 else 0
        try:
            await hm.edit(
                f"{state} `{data['file_name']}`\n"
                f"[{bar}] {pct:.1%}\n"
                f"**Speed:** {get_formatted_size(speed)}/s\n"
                f"**ETA:** `{convert_seconds(eta)}`\n"
                f"**Size:** {get_formatted_size(current)} / {get_formatted_size(total)}",
                parse_mode="markdown",
            )
        except Exception:
            pass

    # ── Caption ───────────────────────────────────────────────────────────────
    user_name  = m.sender.first_name if m.sender else "Unknown"
    user_uname = (m.sender.username or "-") if m.sender else "-"
    caption = (
        f"┏━━━━━━━━━━⍟\n"
        f"┃ {BOT_NAME}\n"
        f"┗━━━━━━━━━━━━━━━━━⍟\n"
        f"╟➣ **File:** `{data['file_name']}`\n"
        f"╟➣ **Size:** {data['size']}\n"
        f"╟➣ **Link:** [Direct download]({data['direct_link']})\n"
        f"╟➣ **User:** {user_name} (@{user_uname})\n"
        f"╚═════════════════⍟{footer()}"
    )

    thumbnail = (
        download_image_to_bytesio(data["thumb"], "thumb.png")
        if data.get("thumb") else None
    )

    # ── Upload ────────────────────────────────────────────────────────────────
    file           = None
    local_download = None

    try:
        file = await bot.send_file(
            PRIVATE_CHAT_ID,
            file=data["direct_link"],
            thumb=thumbnail,
            progress_callback=progress_bar,
            caption=caption,
            supports_streaming=True,
            spoiler=True,
        )
    except telethon.errors.rpcerrorlist.WebpageCurlFailedError:
        await hm.edit("⬇️ Direct link failed — downloading locally first…")
        local_download = await download_file(
            data["direct_link"], data["file_name"], progress_bar
        )
        if not local_download:
            return await hm.edit(
                f"❌ Download failed. Try manually: [here]({data['direct_link']})",
                parse_mode="markdown",
            )
        file = await bot.send_file(
            PRIVATE_CHAT_ID,
            local_download,
            thumb=thumbnail,
            caption=caption,
            progress_callback=progress_bar,
            supports_streaming=True,
            spoiler=True,
        )
    except Exception as e:
        log.error("Upload error: %s", e)
        return await hm.edit(
            f"❌ Upload failed. Try manually: [here]({data['direct_link']})",
            parse_mode="markdown",
        )
    finally:
        if local_download and os.path.exists(local_download):
            try:
                os.unlink(local_download)
            except Exception:
                pass

    try:
        await hm.delete()
    except Exception:
        pass

    if not file:
        return

    if shorturl:
        db.cache_file(shorturl, file.id)

    await bot(
        ForwardMessagesRequest(
            from_peer=PRIVATE_CHAT_ID,
            id=[file.id],
            to_peer=m.chat.id,
            drop_author=True,
            background=True,
            drop_media_captions=False,
            with_my_score=True,
        )
    )
    cd = PREMIUM_COOLDOWN if db.is_premium(user_id) else FREE_COOLDOWN
    db.set_cooldown(user_id, cd)
    db.increment_usage(user_id)


# ── Entry point ───────────────────────────────────────────────────────────────

bot.start(bot_token=BOT_TOKEN)
log.info("🤖 %s is running!", BOT_NAME)
bot.run_until_disconnected()
