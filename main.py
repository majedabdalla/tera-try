import asyncio
import logging
import os
import time
from uuid import uuid4

import telethon
import telethon.tl.types
from telethon import Button, TelegramClient, events
from telethon.tl.functions.messages import ForwardMessagesRequest
from telethon.types import Message, UpdateNewMessage

from cansend import CanSend
from config import (
    ADMIN_ID,
    ADMINS,
    API_HASH,
    API_ID,
    BOT_TOKEN,
    DB_NAME,
    FREE_COOLDOWN,
    MAX_FILE_SIZE,
    MAX_USAGE_PER_WINDOW,
    MONGO_URI,
    PREMIUM_COOLDOWN,
    PRIVATE_CHAT_ID,
    REQUIRED_CHANNELS,
)
from database import Database
from terabox import get_data
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
db = Database(MONGO_URI, DB_NAME)

# File extensions the bot will accept
SUPPORTED_EXTENSIONS = {".mp4", ".mkv", ".webm", ".avi", ".mov", ".flv", ".m4v"}


# ──────────────────────────── Helpers ────────────────────────────

async def check_required_channels(m: UpdateNewMessage) -> bool:
    """Returns False and replies if the user hasn't joined all required channels."""
    if not REQUIRED_CHANNELS:
        return True
    for channel in REQUIRED_CHANNELS:
        if not await is_user_on_chat(bot, channel, m.peer_id):
            joined = " and ".join(REQUIRED_CHANNELS)
            await m.reply(
                f"⚠️ Please join {joined} first, then send the link again.",
                link_preview=False,
            )
            return False
    return True


def nav_buttons():
    return [
        [Button.url("Source Code", url="https://github.com/Abdul97233/TeraBox-Downloader-Bot")],
        [
            Button.url("Channel", url="https://t.me/NTMpro"),
            Button.url("Group", url="https://t.me/NTMchat"),
        ],
        [Button.url("Owner", url="https://t.me/abdul97233")],
    ]


# ──────────────────────────── /start ────────────────────────────

@bot.on(events.NewMessage(pattern="/start", incoming=True, outgoing=False))
async def start(m: UpdateNewMessage):
    user_id = m.sender_id
    user = await bot.get_entity(user_id)
    name = user.first_name
    username = user.username or "-"

    db.save_user(user_id, name, username)  # Store in MongoDB

    # Notify admins
    admin_msg = f"👤 /start\nName: {name}\nUsername: @{username}\nID: `{user_id}`"
    for admin_id in ADMINS:
        try:
            await bot.send_message(admin_id, admin_msg, parse_mode="markdown")
        except Exception:
            pass

    is_prem = db.is_premium(user_id)
    if is_prem:
        body = (
            "┏━━━━━━━━━━⍟\n"
            "┃ 𝐍𝐓𝐌 𝐓𝐞𝐫𝐚 𝐁𝐨𝐱 𝐃𝐨𝐰𝐧𝐥𝐨𝐚𝐝𝐞𝐫 𝐁𝐨𝐭\n"
            "┗━━━━━━━━━━━━━━━━━⍟\n"
            "╔══════════⍟\n"
            "┃ 🌟 Welcome back, Premium User!\n"
            "┃\n"
            "┃ Send any Terabox link and I'll\n"
            "┃ deliver the file instantly. 🚀\n"
            "╚═════════════════⍟\n"
            "Use /help to see all commands."
        )
    else:
        body = (
            "┏━━━━━━━━━━⍟\n"
            "┃ 𝐅𝐑𝐄𝐄 𝐔𝐒𝐄𝐑\n"
            "┗━━━━━━━━━━━━━━━━━⍟\n"
            "╔══════════⍟\n"
            "┃ You currently have limited access.\n"
            "┃\n"
            "┃ /cmds or /help — available commands\n"
            "┃ /id or /info   — your account details\n"
            "┃ /plan          — see premium plans\n"
            "╚═════════════════⍟\n"
            "For premium access, contact @Abdul97233."
        )

    await m.reply(body, link_preview=False, parse_mode="markdown", buttons=nav_buttons())


# ──────────────────────────── /info  /id ────────────────────────────

@bot.on(
    events.NewMessage(
        pattern=r"^/(info|id)$",
        incoming=True,
        outgoing=False,
    )
)
async def user_info(m: UpdateNewMessage):
    user_id = m.sender_id
    user = await bot.get_entity(user_id)
    name = user.first_name
    username = user.username or "-"
    db.save_user(user_id, name, username)
    plan = "💎 Premium" if db.is_premium(user_id) else "🆓 Free"
    await m.reply(
        f"**Name:** {name}\n**Username:** @{username}\n**User ID:** `{user_id}`\n**Plan:** {plan}",
        parse_mode="markdown",
    )


# ──────────────────────────── /help  /cmds ────────────────────────────

@bot.on(
    events.NewMessage(
        pattern=r"^/(help|cmds)$",
        incoming=True,
        outgoing=False,
        func=lambda x: x.is_private,
    )
)
async def command_help(m: UpdateNewMessage):
    text = (
        "┏━━━━━━━━━━⍟\n"
        "┃ 𝘼𝙫𝙖𝙞𝙡𝙖𝙗𝙡𝙚 𝘾𝙤𝙢𝙢𝙖𝙣𝙙𝙨\n"
        "┗━━━━━━━━━━━━━━━━━⍟\n\n"
        "/start — Welcome message\n"
        "/info or /id — Your user details\n"
        "/redeem <code> — Redeem a gift code\n"
        "/plan — See available plans\n"
        "/ping — Check bot latency\n"
        "/help or /cmds — This message\n\n"
        "📎 Just send a Terabox link to download!"
    )
    await m.reply(text, link_preview=False, parse_mode="markdown", buttons=nav_buttons())


# ──────────────────────────── /ping ────────────────────────────

@bot.on(events.NewMessage(pattern=r"^/ping$", incoming=True, outgoing=False))
async def ping_pong(m: UpdateNewMessage):
    t = time.time()
    msg = await m.reply("🖥️ Measuring latency...")
    ms = round((time.time() - t) * 1000, 2)
    await msg.edit(f"🖥️ **Pong!** `{ms} ms`", parse_mode="markdown")


# ──────────────────────────── /plan ────────────────────────────

@bot.on(events.NewMessage(pattern=r"^/plan$", incoming=True, outgoing=False))
async def display_plan(m: UpdateNewMessage):
    await m.reply(
        "┏━━━━━━━━━━⍟\n"
        "┃ 𝐓𝐄𝐑𝐀 𝐁𝐎𝐗 𝐏𝐑𝐄𝐌𝐈𝐔𝐌 𝐏𝐋𝐀𝐍\n"
        "┗━━━━━━━━━━━━━━━━━⍟\n\n"
        "💎 Membership Plans:\n"
        "• Rs. 100 — 10 days\n"
        "• Rs. 60  — 4 days\n"
        "• Rs. 30  — 2 days\n"
        "• Rs. 20  — 1 day\n\n"
        "💳 Payment: UPI · Esewa · Khalti · PhonePay · PayPal\n"
        "✅ Nepal & India payments accepted.\n\n"
        "📩 Contact @Abdul97233 to purchase.",
        parse_mode="markdown",
    )


# ──────────────────────────── /redeem ────────────────────────────

@bot.on(events.NewMessage(pattern=r"^/redeem (.+)$", incoming=True, outgoing=False))
async def redeem_gift_code(m: UpdateNewMessage):
    code = m.pattern_match.group(1).strip()
    if not db.is_valid_code(code):
        return await m.reply("❌ Invalid or expired gift code.")

    user_id = m.sender_id
    user = await bot.get_entity(user_id)
    name = user.first_name
    username = user.username or "-"

    db.consume_code(code)
    db.add_premium(user_id)
    db.save_user(user_id, name, username)

    admin_msg = f"🎁 Gift code redeemed!\nName: {name}\nUsername: @{username}\nID: {user_id}"
    for admin_id in ADMINS:
        try:
            await bot.send_message(admin_id, admin_msg)
        except Exception:
            pass

    await m.reply(
        "✅ Gift code redeemed successfully!\nYou are now a **Premium** user. 🎉",
        parse_mode="markdown",
    )


# ──────────────────────────── Admin: /gc ────────────────────────────

@bot.on(
    events.NewMessage(
        pattern=r"^/gc (\d+)$", incoming=True, outgoing=False, from_users=ADMINS
    )
)
async def generate_gift_codes(m: UpdateNewMessage):
    qty = int(m.pattern_match.group(1))
    codes = [f"NTM-{str(uuid4())[:8].upper()}" for _ in range(qty)]
    db.add_gift_codes(codes)
    lines = "\n".join(f"`{c}`" for c in codes)
    await m.reply(
        f"✅ Generated **{qty}** gift codes:\n\n{lines}", parse_mode="markdown"
    )


# ──────────────────────────── Admin: /pre ────────────────────────────

@bot.on(
    events.NewMessage(
        pattern=r"^/pre (.+)$", incoming=True, outgoing=False, from_users=ADMINS
    )
)
async def promote_user(m: UpdateNewMessage):
    try:
        uid = int(m.pattern_match.group(1).strip())
    except ValueError:
        return await m.reply("❌ Usage: /pre <user_id>")
    if db.add_premium(uid):
        await m.reply(f"✅ `{uid}` promoted to premium.", parse_mode="markdown")
    else:
        await m.reply(f"ℹ️ `{uid}` is already premium.", parse_mode="markdown")


# ──────────────────────────── Admin: /de ────────────────────────────

@bot.on(
    events.NewMessage(
        pattern=r"^/de (.+)$", incoming=True, outgoing=False, from_users=ADMINS
    )
)
async def demote_user(m: UpdateNewMessage):
    try:
        uid = int(m.pattern_match.group(1).strip())
    except ValueError:
        return await m.reply("❌ Usage: /de <user_id>")
    if db.remove_premium(uid):
        await m.reply(f"✅ `{uid}` demoted from premium.", parse_mode="markdown")
    else:
        await m.reply(f"ℹ️ `{uid}` is not a premium user.", parse_mode="markdown")


# ──────────────────────────── Admin: /premium_users ────────────────────────────

@bot.on(
    events.NewMessage(
        pattern=r"^/premium_users$", incoming=True, outgoing=False, from_users=ADMINS
    )
)
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


# ──────────────────────────── Admin: /remove_premium_user  /demote_all_premium ──

@bot.on(
    events.NewMessage(
        pattern=r"^/(remove_premium_user|demote_all_premium)$",
        incoming=True,
        outgoing=False,
        from_users=ADMINS,
    )
)
async def demote_all(m: UpdateNewMessage):
    db.clear_all_premium()
    await m.reply("✅ All premium users have been demoted.")


# ──────────────────────────── Admin: /remove (clear rate limits) ──────────────

@bot.on(
    events.NewMessage(
        pattern=r"^/remove (.+)$", incoming=True, outgoing=False, from_users=ADMINS
    )
)
async def remove_rate_limit(m: UpdateNewMessage):
    try:
        uid = int(m.pattern_match.group(1).strip())
    except ValueError:
        return await m.reply("❌ Usage: /remove <user_id>")
    db.clear_limits(uid)
    await m.reply(f"✅ Cleared all rate limits for `{uid}`.", parse_mode="markdown")


# ──────────────────────────── Admin: /stats ────────────────────────────

@bot.on(
    events.NewMessage(
        pattern=r"^/stats$", incoming=True, outgoing=False, from_users=ADMINS
    )
)
async def bot_stats(m: UpdateNewMessage):
    total = db.get_user_count()
    prem = len(db.get_all_premium_user_ids())
    await m.reply(
        f"📊 **Bot Statistics**\n\n"
        f"👥 Total users: `{total}`\n"
        f"💎 Premium users: `{prem}`",
        parse_mode="markdown",
    )


# ──────────────────────────── Admin: /broadcast ───────────────────────────────

@bot.on(
    events.NewMessage(
        pattern=r"^/broadcast", incoming=True, outgoing=False, from_users=ADMINS
    )
)
async def broadcast_message(m: UpdateNewMessage):
    text = m.text.split("/broadcast", 1)[1].strip()
    if not text:
        return await m.reply("❌ Usage: /broadcast <message>")

    # Broadcasts to all users stored in OUR database — not a hardcoded group
    all_ids = db.get_all_user_ids()
    sent = failed = 0
    status = await m.reply(f"📢 Broadcasting to {len(all_ids)} users…")

    for uid in all_ids:
        try:
            await bot.send_message(uid, text)
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)  # Stay under Telegram flood limits

    await status.edit(f"✅ Done!\n✅ Sent: {sent}\n❌ Failed: {failed}")


# ──────────────────────────── Chat join tracking ────────────────────────────

@bot.on(events.ChatAction)
async def user_joined(event):
    if event.user_joined:
        try:
            user = await bot.get_entity(event.user_id)
            db.save_user(event.user_id, user.first_name, user.username)
        except Exception:
            pass


# ──────────────────────────── Download handler ────────────────────────────

@bot.on(
    events.NewMessage(
        incoming=True,
        outgoing=False,
        func=lambda msg: msg.text
        and get_urls_from_string(msg.text)
        and msg.is_private,
    )
)
async def gate_message(m: Message):
    """Only premium users can trigger downloads."""
    user_id = m.sender_id
    if db.is_premium(user_id) or user_id in ADMINS:
        asyncio.create_task(handle_message(m))
    else:
        await m.reply(
            "⚠️ Downloads are for **Premium** users only.\n\n"
            "Use /plan to see pricing, or contact @Abdul97233.",
            parse_mode="markdown",
        )


async def handle_message(m: Message):
    user_id = m.sender_id

    # Save/update user on every interaction so the DB stays fresh
    try:
        user = await bot.get_entity(user_id)
        db.save_user(user_id, user.first_name, user.username)
    except Exception:
        pass

    url = get_urls_from_string(m.text)
    if not url:
        return await m.reply("❌ Couldn't find a valid Terabox link in your message.")

    if not await check_required_channels(m):
        return

    # ── Cooldown check ──────────────────────────────────────────────────────
    if db.is_on_cooldown(user_id) and user_id not in ADMINS:
        cd = PREMIUM_COOLDOWN if db.is_premium(user_id) else FREE_COOLDOWN
        return await m.reply(f"⏳ Please wait {cd} seconds before your next request.")

    hm = await m.reply("⏳ Fetching your file, please wait…")

    # ── Usage cap check ─────────────────────────────────────────────────────
    count = db.get_usage_count(user_id)
    if count >= MAX_USAGE_PER_WINDOW and user_id not in ADMINS:
        return await hm.edit(
            "🚫 You've hit the 2-hour usage limit. Please come back later or upgrade to premium."
        )

    # ── Cache check ─────────────────────────────────────────────────────────
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

    # ── Fetch metadata ──────────────────────────────────────────────────────
    data = get_data(url)
    if not data:
        return await hm.edit(
            "❌ API request failed — the link may be broken or the API is down."
        )

    # ── File-type guard ─────────────────────────────────────────────────────
    file_ext = os.path.splitext(data["file_name"])[1].lower()
    if file_ext not in SUPPORTED_EXTENSIONS:
        return await hm.edit(
            f"⚠️ File type `{file_ext}` is not supported.\n"
            f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}",
            parse_mode="markdown",
        )

    # ── Size guard ──────────────────────────────────────────────────────────
    if int(data["sizebytes"]) > MAX_FILE_SIZE and user_id not in ADMINS:
        return await hm.edit(
            f"⚠️ File too large: **{data['size']}**\n"
            f"Maximum: {get_formatted_size(MAX_FILE_SIZE)}",
            parse_mode="markdown",
        )

    # ── Progress bar ────────────────────────────────────────────────────────
    start_time = time.time()
    cansend = CanSend()

    async def progress_bar(current, total, state="Uploading"):
        if not cansend.can_send():
            return
        elapsed = time.time() - start_time
        pct = current / total if total else 0
        bar = "█" * int(pct * 20) + "░" * (20 - int(pct * 20))
        speed = current / elapsed if elapsed > 0 else 0
        eta = (total - current) / speed if speed > 0 else 0
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

    # ── Build caption ────────────────────────────────────────────────────────
    user_name = m.sender.first_name if m.sender else "Unknown"
    user_uname = (m.sender.username or "-") if m.sender else "-"
    caption = (
        "┏━━━━━━━━━━⍟\n"
        "┃ 𝐍𝐓𝐌 𝐓𝐞𝐫𝐚 𝐁𝐨𝐱 𝐃𝐨𝐰𝐧𝐥𝐨𝐚𝐝𝐞𝐫\n"
        "┗━━━━━━━━━━━━━━━━━⍟\n"
        f"╟➣ **File:** `{data['file_name']}`\n"
        f"╟➣ **Size:** {data['size']}\n"
        f"╟➣ **Link:** [Direct download]({data['direct_link']})\n"
        f"╟➣ **User:** {user_name} (@{user_uname})\n"
        "╚═════════════════⍟\n"
        "@NTMpro"
    )

    thumbnail = (
        download_image_to_bytesio(data["thumb"], "thumb.png")
        if data.get("thumb")
        else None
    )

    # ── Upload ───────────────────────────────────────────────────────────────
    file = None
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

    # ── Cache & forward ──────────────────────────────────────────────────────
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


# ──────────────────────────── Entry point ────────────────────────────

bot.start(bot_token=BOT_TOKEN)
log.info("🤖 Bot is running!")
bot.run_until_disconnected()
