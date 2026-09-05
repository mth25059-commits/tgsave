# tgsave — a private Telegram saver.
#
# Paste a post link, get the file back. One owner, no database, no join-gate.
#
#   /login    put your account online (needed once)
#   /batch    <first link> <last link>
#   /cancel   stop whatever is running
#   /status   what's up

import asyncio
import logging
import os
import shutil
import time

from pyrogram import Client, filters, idle
from pyrogram.errors import (
    ChannelInvalid, ChannelPrivate, FloodWait, PasswordHashInvalid,
    PeerIdInvalid, PhoneCodeExpired, PhoneCodeInvalid, SessionPasswordNeeded,
    UsernameNotOccupied,
)

import config
import grab
import links

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
logging.getLogger("pyrogram").setLevel(logging.WARNING)
log = logging.getLogger("tgsave")

os.makedirs(config.DATA_DIR, exist_ok=True)

bot = Client(
    "tgsave-bot",
    api_id=config.API_ID,
    api_hash=config.API_HASH,
    bot_token=config.BOT_TOKEN,
    workdir=config.DATA_DIR,
    in_memory=True,
)

user = Client(
    config.SESSION_NAME,
    api_id=config.API_ID,
    api_hash=config.API_HASH,
    workdir=config.DATA_DIR,
    no_updates=True,
)

owner = filters.create(
    lambda _, __, m: bool(m.from_user) and m.from_user.id == config.ADMIN_ID
)

# Everything the process needs to remember. No database by design.
state = {
    "online": False,    # is the saved account usable
    "login": None,      # in-progress /login conversation
    "job": None,        # grab.Job currently running
    "task": None,       # asyncio task running it
    "warmed": False,    # dialog cache primed for private-channel ids
    "booted": time.time(),
}

HELP = (
    "**tgsave**\n\n"
    "Send me any post link and I'll bring the file back:\n"
    "`https://t.me/c/1234567890/55`\n\n"
    "**Commands**\n"
    "`/batch <first link> <last link>` — a whole range\n"
    "`/cancel` — stop the current job\n"
    "`/status` — account, disk, uptime\n"
    "`/login` — put your account online\n"
    "`/logout` — take it offline\n\n"
    "Albums come back whole. Files keep their real name and extension."
)


async def bring_online() -> bool:
    """Connect the saved account without ever prompting on stdin."""
    try:
        if await user.connect():
            await user.initialize()
            me = await user.get_me()
            state["online"] = True
            log.info("account online: %s", me.first_name)
            return True
        await user.disconnect()
        log.warning("no saved account — send /login to the bot")
    except ConnectionError:
        state["online"] = True
        return True
    except Exception as exc:
        log.warning("could not bring account online: %s", exc)
    return False


async def warm_peers():
    """Private /c/ ids only resolve once the dialog list has been walked."""
    if state["warmed"]:
        return
    state["warmed"] = True
    try:
        async for _ in user.get_dialogs():
            pass
        log.info("dialog cache primed")
    except Exception as exc:
        log.warning("dialog walk failed: %s", exc)


async def get_post(chat, message_id):
    """Fetch one source post, priming the peer cache if the id is cold."""
    try:
        return await user.get_messages(chat, message_id)
    except (PeerIdInvalid, ChannelInvalid, KeyError):
        if state["warmed"]:
            raise
        await warm_peers()
        return await user.get_messages(chat, message_id)


def free_disk() -> int:
    return shutil.disk_usage(config.DATA_DIR).free


async def blocked(message) -> bool:
    """Refuse to start a second job, or to work without an account."""
    if not state["online"]:
        await message.reply_text("🔌 No account online yet. Send `/login` first.")
        return True
    if state["job"] is not None:
        await message.reply_text("⏳ Already working on something. `/cancel` to stop it.")
        return True
    return False


def claim(job):
    state["job"] = job
    state["task"] = asyncio.current_task()


def release():
    state["job"] = None
    state["task"] = None


UNREACHABLE = (ChannelPrivate, ChannelInvalid, PeerIdInvalid, UsernameNotOccupied)
NO_ACCESS = "🔒 Your account can't see that chat. Join it first, then send the link again."


async def run_single(message, url):
    try:
        chat, post_id = links.parse(url)
    except links.BadLink as bad:
        await message.reply_text(f"❌ {bad}")
        return

    job = grab.Job(links.short(chat))
    claim(job)
    status = await message.reply_text("🔎 Fetching the post…")
    try:
        src = await get_post(chat, post_id)
        if not src or getattr(src, "empty", False):
            await status.edit_text("❌ Nothing there — the post is deleted or out of reach.")
            return

        posts = [src]
        if src.media_group_id:
            try:
                posts = await user.get_media_group(chat, post_id) or [src]
            except Exception:
                posts = [src]

        sent = 0
        for index, post in enumerate(posts, 1):
            if job.cancel:
                break
            note = f"📦 album {index}/{len(posts)}" if len(posts) > 1 else ""
            outcome = await grab.deliver(user, message, post, status, job, note)
            if outcome in ("sent", "text"):
                sent += 1
            elif outcome == "cancelled":
                break

        if job.cancel:
            await status.edit_text("🛑 Stopped.")
        else:
            await status.delete()
            log.info("delivered %s/%s from %s", sent, len(posts), links.short(chat))
    except UNREACHABLE:
        await status.edit_text(NO_ACCESS)
    except FloodWait as wait:
        await status.edit_text(f"🐢 Telegram wants a {grab.clock(wait.value)} pause. Retry after that.")
    except asyncio.CancelledError:
        await status.edit_text("🛑 Stopped.")
    except Exception as exc:
        log.exception("single failed")
        await status.edit_text(f"❌ Failed: `{str(exc)[:200]}`")
    finally:
        release()


async def run_batch(message, first_url, last_url):
    try:
        chat, start = links.parse(first_url)
        other, end = links.parse(last_url)
    except links.BadLink as bad:
        await message.reply_text(f"❌ {bad}")
        return
    if chat != other:
        await message.reply_text("❌ Both links have to point at the same chat.")
        return
    if start > end:
        start, end = end, start
    total = end - start + 1
    if total > config.MAX_BATCH:
        await message.reply_text(
            f"❌ {total} posts at once is too many — keep it to {config.MAX_BATCH}."
        )
        return

    job = grab.Job(links.short(chat))
    claim(job)
    status = await message.reply_text(f"📦 Batch of {total} from {links.short(chat)}…")
    sent = skipped = failed = 0
    try:
        for offset, post_id in enumerate(range(start, end + 1), 1):
            if job.cancel:
                break
            note = f"📦 {offset}/{total}   ✅ {sent}  ⏭ {skipped}  ❌ {failed}"
            try:
                src = await get_post(chat, post_id)
                if not src or getattr(src, "empty", False):
                    skipped += 1
                    continue
                outcome = await grab.deliver(user, message, src, status, job, note)
                if outcome in ("sent", "text"):
                    sent += 1
                elif outcome == "cancelled":
                    break
                else:
                    skipped += 1
            except FloodWait as wait:
                if wait.value > 300:
                    raise
                await asyncio.sleep(wait.value + 1)
                failed += 1
            except Exception:
                failed += 1
            await asyncio.sleep(config.BATCH_GAP)

        headline = "🛑 Stopped" if job.cancel else "✅ Batch finished"
        await status.edit_text(
            f"{headline}\n\n"
            f"✅ {sent} sent   ⏭ {skipped} skipped   ❌ {failed} failed\n"
            f"📊 {total} asked for"
        )
    except UNREACHABLE:
        await status.edit_text(NO_ACCESS)
    except FloodWait as wait:
        await status.edit_text(f"🐢 Telegram wants a {grab.clock(wait.value)} pause. Retry after that.")
    except asyncio.CancelledError:
        await status.edit_text(f"🛑 Stopped after {sent}.")
    except Exception as exc:
        log.exception("batch failed")
        await status.edit_text(f"❌ Failed: `{str(exc)[:200]}`")
    finally:
        release()


# ---------------------------------------------------------------- commands --

@bot.on_message(filters.command(["start", "help"]) & owner, group=0)
async def cmd_start(_, message):
    await message.reply_text(HELP, disable_web_page_preview=True)


@bot.on_message(filters.command("start") & ~owner, group=0)
async def cmd_stranger(_, message):
    await message.reply_text("🔒 Private bot.")


@bot.on_message(filters.command("status") & owner, group=0)
async def cmd_status(_, message):
    who = "—"
    if state["online"]:
        try:
            me = await user.get_me()
            who = f"{me.first_name} (`{me.id}`)"
        except Exception:
            who = "connected, but not answering"
    job = state["job"]
    await message.reply_text(
        f"**Account:** {'✅ ' + who if state['online'] else '❌ not logged in'}\n"
        f"**Working:** {('yes — ' + job.label) if job else 'idle'}\n"
        f"**Free disk:** {grab.human(free_disk())}\n"
        f"**Uptime:** {grab.clock(time.time() - state['booted'])}"
    )


@bot.on_message(filters.command("cancel") & owner, group=0)
async def cmd_cancel(_, message):
    if state["login"]:
        state["login"] = None
        await message.reply_text("🛑 Login cancelled.")
        return

    job, task = state["job"], state["task"]
    if not job:
        await message.reply_text("Nothing is running.")
        return

    job.cancel = True
    await message.reply_text("🛑 Stopping…")
    await asyncio.sleep(2)
    if state["job"] is job and task and not task.done():
        task.cancel()
    job.scrap()


@bot.on_message(filters.command("batch") & owner, group=0)
async def cmd_batch(_, message):
    found = links.find(message.text or "")
    if len(found) < 2:
        await message.reply_text("Usage: `/batch <first link> <last link>`")
        return
    if await blocked(message):
        return
    await run_batch(message, found[0], found[1])


@bot.on_message(filters.command("login") & owner, group=0)
async def cmd_login(_, message):
    if state["online"]:
        await message.reply_text("Already online — `/logout` first to switch accounts.")
        return
    state["login"] = {"step": "phone"}
    await message.reply_text(
        "📱 Send your phone number with the country code, like `+919876543210`.\n"
        "`/cancel` to stop."
    )


@bot.on_message(filters.command("logout") & owner, group=0)
async def cmd_logout(_, message):
    if not state["online"]:
        await message.reply_text("No account is logged in.")
        return
    try:
        await user.log_out()
    except Exception as exc:
        log.warning("logout: %s", exc)
    state["online"] = False
    state["warmed"] = False
    await message.reply_text("👋 Account unlinked. `/login` when you want it back.")


# ------------------------------------------------------------------- login --

async def wrap_up_login(note):
    state["login"] = None
    try:
        await user.initialize()
    except ConnectionError:
        pass
    me = await user.get_me()
    state["online"] = True
    state["warmed"] = False
    await note.edit_text(f"✅ Logged in as **{me.first_name}**. Send me a post link.")


async def login_step(message):
    step = state["login"]["step"]
    text = (message.text or "").strip()

    if step == "phone":
        digits = text[1:].replace(" ", "").replace("-", "")
        if not text.startswith("+") or not digits.isdigit():
            await message.reply_text("❌ Country code needed, like `+919876543210`.")
            return
        note = await message.reply_text("📨 Sending the code…")
        try:
            if not user.is_connected:
                await user.connect()
            sent = await user.send_code("+" + digits)
        except Exception as exc:
            state["login"] = None
            await note.edit_text(f"❌ {str(exc)[:200]}")
            return
        state["login"] = {"step": "code", "phone": "+" + digits, "hash": sent.phone_code_hash}
        await note.edit_text(
            "🔑 Code sent.\n\nType it **with spaces** — `1 2 3 4 5` — otherwise Telegram "
            "spots the code in this chat and kills it."
        )
        return

    note = await message.reply_text("🔐 Checking…")
    try:
        await message.delete()          # don't leave codes or passwords lying around
    except Exception:
        pass

    if step == "code":
        try:
            await user.sign_in(state["login"]["phone"], state["login"]["hash"],
                               text.replace(" ", ""))
        except SessionPasswordNeeded:
            state["login"]["step"] = "password"
            await note.edit_text("🔒 Two-step verification is on. Send your password.")
            return
        except (PhoneCodeInvalid, PhoneCodeExpired) as exc:
            state["login"] = None
            await note.edit_text(f"❌ {type(exc).__name__} — run `/login` again.")
            return
        except Exception as exc:
            state["login"] = None
            await note.edit_text(f"❌ {str(exc)[:200]}")
            return
        await wrap_up_login(note)
        return

    if step == "password":
        try:
            await user.check_password(text)
        except PasswordHashInvalid:
            await note.edit_text("❌ Wrong password. Send it again, or `/cancel`.")
            return
        except Exception as exc:
            state["login"] = None
            await note.edit_text(f"❌ {str(exc)[:200]}")
            return
        await wrap_up_login(note)


# --------------------------------------------------------------- plain text --

@bot.on_message(filters.text & owner & ~filters.regex(r"^/"), group=1)
async def on_text(_, message):
    if state["login"]:
        await login_step(message)
        return

    found = links.find(message.text or "")
    if not found:
        return
    if await blocked(message):
        return
    for url in found[:10]:
        await run_single(message, url)


# -------------------------------------------------------------------- boot --

async def main():
    trouble = config.problems()
    if trouble:
        for line in trouble:
            log.error("config: %s", line)
        log.error("fill in config.env and start me again")
        return

    grab.sweep()
    await bot.start()
    me = await bot.get_me()
    log.info("bot online: @%s", me.username)

    await bring_online()

    hello = ("✅ tgsave is up. Send me a post link."
             if state["online"] else
             "✅ tgsave is up, but no account is linked yet — send `/login`.")
    try:
        await bot.send_message(config.ADMIN_ID, hello)
    except Exception as exc:
        log.warning("could not greet the owner: %s", exc)

    await idle()

    await bot.stop()
    try:
        await user.stop()
    except Exception:
        pass


if __name__ == "__main__":
    bot.run(main())
