# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A single-file Telegram bot (`insta_bot.py`) that listens for Instagram links sent by authorized users and reposts the content (photo/video/carousel) to a configured Telegram channel, preserving the original caption.

## Running

```bash
pip install -r requirements.txt
python insta_bot.py
```

Requires a `.env` file (loaded via `python-dotenv`) with:
- `BOT_TOKEN` — Telegram bot token from @BotFather
- `CHANNEL_ID` — target channel (`@channel` or `-100...` id); the bot must be a channel admin
- `ALLOWED_USER_IDS` — comma-separated Telegram user IDs allowed to invoke the bot
- `IG_USERNAME` — Instagram username; enables loading a saved session file for authenticated downloads

There are no automated tests, lint config, or build step in this repo — verification is manual (run the bot and send it an Instagram link).

### Docker

```bash
docker build -t insta-bot .
docker run -d --name insta-bot --env-file .env -v $(pwd)/session-<username>:/app/session-<username> insta-bot
```

CI (`.github/workflows/docker-publish.yml`) builds and pushes multi-arch (amd64/arm64) images to GHCR on `v*` tag pushes.

## Instagram session files

Downloading relies on `instaloader` session files named `session-<IG_USERNAME>`, generated locally via `instaloader.Instaloader().interactive_login(...)` + `save_session_to_file()` (see README for the full snippet). These files contain live login cookies:
- Never commit them or treat their contents as loggable/shareable. A `session-welderpbg` file exists in this working tree untracked — treat it as a secret, not a code artifact.
- In Docker, the session file is bind-mounted into `/app`, not baked into the image.
- `requirements.txt` pins `instaloader` to a specific PyPI release (currently `4.15.3`); bump deliberately and re-verify session/login behavior since Instagram-facing scrapers break easily across versions.

## Architecture (`insta_bot.py`)

Everything lives in one file with a linear flow:

1. **Startup**: env vars loaded and validated (exits via `sys.exit(1)` if `BOT_TOKEN`/`CHANNEL_ID` missing or `ALLOWED_USER_IDS` malformed). `ALLOWED_IDS` becomes a set of ints checked in `check_auth()` on every incoming message.
2. **Shared loader**: one module-level `instaloader.Instaloader` (`L`), obtained via `get_loader()`. It is built at startup and rebuilt automatically when the `session-<IG_USERNAME>` file's mtime changes, so replacing the (bind-mounted) session file takes effect without a restart. `build_loader()` loads the session and calls `test_login()` — a failed/expired session is only logged, downloads proceed anonymously.
3. **Handlers**: registered on a `python-telegram-bot` `ApplicationBuilder` — `/start` command, and a regex `MessageHandler` matching any text containing `instagram.com` → `handle_instagram_link`. Both are restricted to `filters.UpdateType.MESSAGE` (edited messages / channel posts are ignored).
4. **`handle_instagram_link`** does the whole job per message:
   - Re-checks auth, extracts the Instagram URL from the message text via regex (rejects if none).
   - Extracts the shortcode via regex from `/p/`, `/reel/`, `/reels/` or `/tv/` URLs.
   - Under `loader_lock`, runs `Post.from_shortcode` + `download_post` in `asyncio.to_thread` (instaloader blocks and `time.sleep`s for rate limiting), downloading into a per-message temp dir `temp_<message_id>`.
   - Truncates the caption to 900 chars, HTML-escapes it and the URL (captions are sent with `parse_mode='HTML'`), and appends a link back to the source post.
   - Classifies downloaded files by extension (`.jpg` → photo, `.mp4` → video) and sends either a single `send_photo`/`send_video` or, for multiple files, `InputMediaPhoto`/`InputMediaVideo` items via `send_media_group`, split into evenly sized chunks within Telegram's 2–10 items-per-group limit (caption only on the first item).
   - `finally` block always removes the temp download folder, including on error.

There is no persistence layer, database, queue, or multi-module structure — all state is either an env var, the in-memory `ALLOWED_IDS` set, the shared loader, or a Telegram-provided `context`/`update` object. When extending, keep in mind updates are processed sequentially by `application.run_polling()` (no `concurrent_updates`); `loader_lock` serializes access to the shared loader if that ever changes.
