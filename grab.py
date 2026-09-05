# tgsave — download / re-upload engine
#
# One job at a time. Files land in their own throwaway directory so two runs can
# never collide and the original filename (and extension) survives the trip.

import asyncio
import os
import shutil
import time
import uuid

from pyrogram.errors import FloodWait, MessageNotModified

import config

BAR_LEN = 18
UNITS = ("B", "KB", "MB", "GB", "TB")


class Cancelled(Exception):
    """Raised inside a progress callback when the owner asked us to stop."""


class Job:
    """Mutable handle for the transfer that is running right now."""

    def __init__(self, label: str = ""):
        self.label = label
        self.cancel = False
        self.dir = os.path.join(config.DL_DIR, uuid.uuid4().hex[:10])

    def scrap(self):
        shutil.rmtree(self.dir, ignore_errors=True)


def human(size) -> str:
    size = float(size or 0)
    for unit in UNITS:
        if size < 1024 or unit == UNITS[-1]:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def clock(seconds) -> str:
    seconds = int(max(0, seconds or 0))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s"
    return f"{seconds // 3600}h {(seconds % 3600) // 60}m"


class Progress:
    """Throttled progress bar. One instance per transfer."""

    def __init__(self, status_msg, job: Job, verb: str, note: str = ""):
        self.msg = status_msg
        self.job = job
        self.verb = verb
        self.note = note
        self.began = time.monotonic()
        self.last_edit = 0.0

    async def __call__(self, done, total):
        if self.job.cancel:
            raise Cancelled()
        if not self.msg:
            return

        now = time.monotonic()
        finished = total and done >= total
        if not finished and now - self.last_edit < config.PROGRESS_EVERY:
            return
        self.last_edit = now

        elapsed = max(0.001, now - self.began)
        speed = done / elapsed
        share = (done / total) if total else 0
        filled = int(BAR_LEN * share)
        bar = "█" * filled + "░" * (BAR_LEN - filled)
        eta = clock((total - done) / speed) if speed and total else "?"

        text = (
            f"{self.verb}\n"
            f"`{bar}` {share * 100:.1f}%\n"
            f"{human(done)} / {human(total)}  •  {human(speed)}/s\n"
            f"ETA {eta}"
        )
        if self.note:
            text = f"{self.note}\n\n{text}"
        try:
            await self.msg.edit_text(text)
        except MessageNotModified:
            pass
        except FloodWait as wait:
            self.last_edit = now + wait.value
        except Exception:
            pass


KINDS = ("video", "photo", "document", "audio", "animation",
         "voice", "video_note", "sticker")


def describe(msg):
    """Return (kind, size) for whatever media a message carries."""
    for kind in KINDS:
        obj = getattr(msg, kind, None)
        if obj:
            return kind, getattr(obj, "file_size", 0) or 0
    return None, 0


async def fetch_thumb(user, media, job):
    """Grab the source thumbnail so re-uploads keep their poster frame."""
    thumbs = getattr(media, "thumbs", None) or []
    if not thumbs:
        return None
    try:
        return await user.download_media(
            thumbs[-1].file_id, file_name=os.path.join(job.dir, "thumb", "")
        )
    except Exception:
        return None


async def pull(user, msg, status, job, note=""):
    """Download msg's media into job.dir. Returns the path, or None if stopped."""
    os.makedirs(job.dir, exist_ok=True)
    try:
        return await user.download_media(
            msg,
            file_name=os.path.join(job.dir, ""),
            progress=Progress(status, job, "⬇️  Downloading", note),
        )
    except Cancelled:
        return None
    except FloodWait as wait:
        if wait.value > 300:
            raise
        await asyncio.sleep(wait.value + 1)
        return await user.download_media(msg, file_name=os.path.join(job.dir, ""))


async def push(message, src, path, status, job, thumb=None, note=""):
    """Re-upload a downloaded file, keeping its original type and attributes."""
    kind, _ = describe(src)
    caption = (src.caption or "")[: config.CAPTION_LIMIT] or None
    extra = dict(caption=caption, progress=Progress(status, job, "⬆️  Uploading", note))

    if kind == "video" and src.video:
        clip = src.video
        return await message.reply_video(
            path, duration=clip.duration or 0, width=clip.width or 0,
            height=clip.height or 0, thumb=thumb, supports_streaming=True, **extra
        )
    if kind == "animation" and src.animation:
        gif = src.animation
        return await message.reply_animation(
            path, duration=gif.duration or 0, width=gif.width or 0,
            height=gif.height or 0, thumb=thumb, **extra
        )
    if kind == "audio" and src.audio:
        track = src.audio
        return await message.reply_audio(
            path, duration=track.duration or 0, performer=track.performer,
            title=track.title, thumb=thumb, **extra
        )
    if kind == "voice" and src.voice:
        return await message.reply_voice(path, duration=src.voice.duration or 0, **extra)
    if kind == "video_note" and src.video_note:
        note_clip = src.video_note
        return await message.reply_video_note(
            path, duration=note_clip.duration or 0, length=note_clip.length or 0,
            thumb=thumb, progress=extra["progress"]
        )
    if kind == "photo":
        return await message.reply_photo(path, **extra)
    if kind == "sticker":
        return await message.reply_sticker(path, progress=extra["progress"])

    doc = src.document
    return await message.reply_document(
        path, thumb=thumb, force_document=True,
        file_name=(doc.file_name if doc else None) or os.path.basename(path), **extra
    )


UPLOAD_CEILING = 2000 * 1024 * 1024     # what a bot account can push back out
WITH_THUMB = ("video", "animation", "audio", "document", "video_note")


async def deliver(user, message, src, status, job, note="") -> str:
    """Carry one source post all the way back to the owner. Returns an outcome word."""
    kind, size = describe(src)

    if not kind:
        body = src.text or src.caption
        if not body:
            return "empty"
        await message.reply_text(body[:4096])
        return "text"

    if size > UPLOAD_CEILING:
        await message.reply_text(
            f"⚠️ Skipped — {human(size)} is over the {human(UPLOAD_CEILING)} upload limit."
        )
        return "too big"

    try:
        path = await pull(user, src, status, job, note)
        if not path:
            return "cancelled" if job.cancel else "failed"
        thumb = None
        if kind in WITH_THUMB:
            thumb = await fetch_thumb(user, getattr(src, kind, None), job)
        await push(message, src, path, status, job, thumb, note)
        return "sent"
    except Cancelled:
        return "cancelled"
    finally:
        job.scrap()


def sweep():
    """Clear leftovers from a previous run so the disk starts clean."""
    shutil.rmtree(config.DL_DIR, ignore_errors=True)
    os.makedirs(config.DL_DIR, exist_ok=True)
