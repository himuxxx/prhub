# Telegram Video Downloader — 2 GB Local Bot API

A VPS-ready Telegram downloader using `yt-dlp`, FFmpeg, MongoDB and Telegram's Local Bot API server.

## What it does

- User sends a video URL to the bot.
- Downloads one video (no playlists).
- Produces MP4 when possible; converts other formats to H.264/AAC MP4.
- Adds MP4 `faststart` metadata for quicker playback.
- Sends it as a Telegram **video**, not a document.
- Enforces a 2 GiB maximum final file size.
- Cleans temporary files after completion/failure.
- Stores basic job history in MongoDB when `MONGO_URI` is configured.
- Uses a concurrency limit (default 1) to avoid exhausting VPS disk/RAM/bandwidth.

## Important Telegram limit

Telegram's cloud Bot API has a 50 MB upload limit. Telegram's Local Bot API server supports uploads up to 2000 MB and local file uploads. This project therefore runs the Local Bot API server beside the bot.

## 1. VPS requirements

Ubuntu/Debian VPS with Docker and Docker Compose plugin installed.

Recommended for large videos: at least 4 CPU cores, 8 GB RAM, and enough disk space for downloads/conversion. A 2 GB file can temporarily require several GB of free disk space.

## 2. Configure secrets

```bash
cp .env.example .env
nano .env
```

Fill in:

- `BOT_TOKEN` — BotFather token
- `API_ID` — Telegram app API ID
- `API_HASH` — Telegram app API hash
- `MONGO_URI` — your MongoDB connection string

Do not commit `.env` to GitHub.

## 3. First-time Local Bot API login

If this bot token has previously been used with the normal cloud Bot API, log it out from the cloud API before switching the bot to the Local Bot API server:

```bash
curl "https://api.telegram.org/bot$BOT_TOKEN/logOut"
```

Run this only when the bot is stopped and you are intentionally moving it to the local server.

Then start:

```bash
mkdir -p data
docker compose up -d --build
```

Check logs:

```bash
docker compose logs -f bot
```

Local Bot API logs:

```bash
docker compose logs -f telegram-bot-api
```

## 4. Test

Open the bot in Telegram and send a supported URL.

The bot will show:

`Downloading → Converting/Preparing → Uploading`

Then it sends an inline-playable Telegram video.

## 5. Stop / update

```bash
docker compose down
docker compose up -d --build
```

The Telegram Bot API data volume is persistent.

## MongoDB

Collection used:

`downloads`

Each completed/failed job stores chat ID, user ID, URL, title/status, size/duration and timing/error data.

## Security

Keep `.env` private. Do not publish BOT_TOKEN, API_HASH or MongoDB credentials. Restrict MongoDB network access to the VPS when possible.
