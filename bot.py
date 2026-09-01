import asyncio
import logging
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.filters import CommandStart
from aiogram.types import Message, FSInputFile
from motor.motor_asyncio import AsyncIOMotorClient
from yt_dlp import YoutubeDL

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
MONGO_URI = os.getenv("MONGO_URI", "").strip()
MONGO_DB = os.getenv("MONGO_DB", "tg_downloader")
BOT_API_URL = os.getenv("BOT_API_URL", "http://telegram-bot-api:8081").strip().rstrip("/")
MAX_SIZE = int(os.getenv("MAX_FILE_SIZE", str(2 * 1024**3)))
DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR", "/data/downloads"))
MAX_CONCURRENT = max(1, int(os.getenv("MAX_CONCURRENT", "1")))
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("tg-downloader")
dp = Dispatcher()
sem = asyncio.Semaphore(MAX_CONCURRENT)

URL_RE = re.compile(r"https?://[^\s]+", re.I)

mongo = AsyncIOMotorClient(MONGO_URI) if MONGO_URI else None
collection = mongo[MONGO_DB].downloads if mongo else None


def get_video(url: str, outdir: Path):
    opts = {
        "format": "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b",
        "merge_output_format": "mp4",
        "outtmpl": str(outdir / "%(title).180s [%(id)s].%(ext)s"),
        "noplaylist": True,
        "restrictfilenames": True,
        "quiet": True,
        "no_warnings": True,
        "retries": 3,
        "fragment_retries": 3,
        "concurrent_fragment_downloads": 4,
    }
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        path = Path(ydl.prepare_filename(info))
        if path.suffix.lower() != ".mp4":
            mp4 = path.with_suffix(".mp4")
            if mp4.exists():
                path = mp4
        return info, path


def probe(path: Path):
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration,size", "-of", "default=noprint_wrappers=1:nokey=1", str(path)]
    out = subprocess.check_output(cmd, text=True).splitlines()
    duration = float(out[0]) if out and out[0] else 0
    size = int(float(out[1])) if len(out) > 1 else path.stat().st_size
    return duration, size


def faststart(path: Path):
    tmp = path.with_name(path.stem + ".faststart.mp4")
    subprocess.run([
        "ffmpeg", "-y", "-i", str(path), "-map", "0", "-c", "copy",
        "-movflags", "+faststart", str(tmp)
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    tmp.replace(path)


def transcode_mp4(src: Path) -> Path:
    dst = src.with_suffix(".mp4")
    if dst == src:
        return src
    subprocess.run([
        "ffmpeg", "-y", "-i", str(src), "-c:v", "libx264", "-preset", "veryfast",
        "-crf", "23", "-c:a", "aac", "-movflags", "+faststart", str(dst)
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return dst


async def db_log(doc):
    if collection is not None:
        try:
            await collection.insert_one(doc)
        except Exception:
            log.exception("MongoDB write failed")


@dp.message(CommandStart())
async def start(message: Message):
    await message.answer(
        "🎬 Send a supported video URL.\n\n"
        "I will download it and return it as a playable Telegram video.\n"
        "Maximum output size: 2 GB."
    )


@dp.message(F.text)
async def handle_url(message: Message):
    m = URL_RE.search(message.text or "")
    if not m:
        await message.answer("❌ Please send a valid video URL.")
        return

    url = m.group(0).rstrip(").,]}>\"'")
    job = DOWNLOAD_DIR / str(message.chat.id) / str(message.message_id)
    job.mkdir(parents=True, exist_ok=True)
    status = await message.answer("⏳ Added to queue…")
    started = time.time()

    async with sem:
        try:
            await status.edit_text("⏬ Downloading…")
            info, path = await asyncio.to_thread(get_video, url, job)

            if not path.exists():
                videos = [p for p in job.glob("*") if p.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov", ".m4v"}]
                if not videos:
                    raise RuntimeError("Downloaded video was not found.")
                path = max(videos, key=lambda p: p.stat().st_size)

            if path.stat().st_size > MAX_SIZE:
                raise RuntimeError("Downloaded file is larger than the 2 GB limit.")

            if path.suffix.lower() != ".mp4":
                await status.edit_text("🔄 Converting to MP4…")
                path = await asyncio.to_thread(transcode_mp4, path)
                if path.stat().st_size > MAX_SIZE:
                    raise RuntimeError("Converted MP4 is larger than the 2 GB limit.")
            else:
                await status.edit_text("⚙️ Preparing video…")
                await asyncio.to_thread(faststart, path)

            duration, size = await asyncio.to_thread(probe, path)
            if size > MAX_SIZE:
                raise RuntimeError("Final video is larger than the 2 GB limit.")

            await status.edit_text("⬆️ Uploading to Telegram…")
            title = info.get("title") or path.stem
            caption = title[:1024]

            await message.answer_video(
                video=FSInputFile(path),
                caption=caption,
                duration=max(0, int(duration)),
                width=info.get("width"),
                height=info.get("height"),
                supports_streaming=True,
            )

            await db_log({
                "chat_id": message.chat.id,
                "user_id": message.from_user.id if message.from_user else None,
                "url": url,
                "title": title,
                "size": size,
                "duration": duration,
                "status": "done",
                "created_at": time.time(),
                "elapsed": time.time() - started,
            })
            await status.delete()
        except Exception as exc:
            log.exception("Job failed")
            await db_log({
                "chat_id": message.chat.id,
                "user_id": message.from_user.id if message.from_user else None,
                "url": url,
                "status": "failed",
                "error": str(exc)[:2000],
                "created_at": time.time(),
            })
            try:
                await status.edit_text(f"❌ Failed: {str(exc)[:1000]}")
            except Exception:
                pass
        finally:
            shutil.rmtree(job, ignore_errors=True)


async def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not set")
    if not MONGO_URI:
        log.warning("MONGO_URI is empty; MongoDB logging is disabled")

    session = AiohttpSession(api=TelegramAPIServer.from_base(BOT_API_URL))
    bot = Bot(BOT_TOKEN, session=session)
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        if mongo:
            mongo.close()


if __name__ == "__main__":
    asyncio.run(main())
