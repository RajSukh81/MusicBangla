import os
import asyncio
import time
import re
import yt_dlp
import httpx
from pyrogram import filters
from pyrogram.types import Message
from pytgcalls.types import MediaStream
from youtubesearchpython import VideosSearch

import config
from MusicBangla import app, assistant, calls, LOGGER


os.makedirs("downloads", exist_ok=True)
ACTIVE_CHATS = {}

# --- Rate limiting & flood protection ---
_USER_COOLDOWN = {}
_COOLDOWN_SECONDS = 5
_GLOBAL_SPAM = {}
_MAX_CONCURRENT = 3  # max concurrent plays

# --- Cookies ---
# Write YT_COOKIES env var to file if it exists (for Heroku deployments)
_COOKIE_FILE = None
if os.environ.get("YT_COOKIES"):
    try:
        with open("cookies.txt", "w") as f:
            f.write(os.environ["YT_COOKIES"])
        _COOKIE_FILE = "cookies.txt"
        LOGGER.info("cookies.txt written from YT_COOKIES env var")
    except Exception as e:
        LOGGER.error(f"Failed to write cookies.txt from env: {e}")
elif os.path.exists("cookies.txt"):
    _COOKIE_FILE = "cookies.txt"
    LOGGER.info("cookies.txt loaded from file")

if not _COOKIE_FILE:
    LOGGER.warning("No YouTube cookies available — playback may fail!")


# =====================================================
# YT-DLP OPTIONS — optimized for current YouTube
# =====================================================

def _base_opts():
    """Base yt-dlp options common to all strategies"""
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 15,
        "retries": 5,
        "fragment_retries": 5,
        "geo_bypass": True,
        "nocheckcertificate": True,
        "no_check_formats": True,
        "check_formats": False,
        "source_address": "0.0.0.0",
        "format_sort": ["abr", "asr"],
        "extractor_args": {"youtube": {"player_skip": ["configs"]}},
    }
    if _COOKIE_FILE:
        opts["cookiefile"] = _COOKIE_FILE
    return opts


# Ordered list of (format_string, player_client, description)
# Each is tried in order. First success wins.
_STRATEGIES = [
    # --- Phase 1: web_creator with cookies (most reliable mid-2026) ---
    ("bestaudio/best", ["web_creator"], "web_creator+bestaudio"),
    ("best", ["web_creator"], "web_creator+best"),

    # --- Phase 2: mweb client ---
    ("bestaudio/best", ["mweb"], "mweb+bestaudio"),
    ("best", ["mweb"], "mweb+best"),

    # --- Phase 3: ios client ---
    ("bestaudio/best", ["ios"], "ios+bestaudio"),

    # --- Phase 4: web + default with cookies ---
    ("bestaudio/best", ["web"], "web+bestaudio"),
    ("bestaudio/best", None, "default+bestaudio"),

    # --- Phase 5: android fallbacks ---
    ("bestaudio/best", ["android"], "android+bestaudio"),
    ("best", ["android"], "android+best"),

    # --- Phase 6: absolute fallback ---
    ("worst", ["web_creator"], "web_creator+worst"),
    ("worst", None, "default+worst"),
]


def cleanup_downloads():
    """Delete download files to save disk space"""
    try:
        for f in os.listdir("downloads"):
            fpath = os.path.join("downloads", f)
            if os.path.isfile(fpath):
                try:
                    os.remove(fpath)
                except Exception:
                    pass
    except Exception:
        pass


# =====================================================
# SEARCH
# =====================================================

def yt_search_sync(query: str):
    """YouTube search via youtube-search-python (sync)"""
    try:
        search = VideosSearch(query, limit=1)
        result = search.result()

        if not result or not result.get("result"):
            LOGGER.warning(f"No results for: {query}")
            return None

        video = result["result"][0]
        vid = video.get("id")
        title = video.get("title", "Unknown")

        dur_text = video.get("duration", "0:00")
        dur_parts = str(dur_text).split(":")
        try:
            if len(dur_parts) == 3:
                duration = int(dur_parts[0]) * 3600 + int(dur_parts[1]) * 60 + int(dur_parts[2])
            elif len(dur_parts) == 2:
                duration = int(dur_parts[0]) * 60 + int(dur_parts[1])
            else:
                duration = 0
        except Exception:
            duration = 0

        thumbs = video.get("thumbnails")
        thumb = thumbs[-1]["url"] if thumbs else f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"
        channel = video.get("channel", {}).get("name", "YouTube")

        return {
            "title": title,
            "duration": duration,
            "link": f"https://www.youtube.com/watch?v={vid}",
            "thumb": thumb,
            "channel": channel,
            "id": vid,
        }
    except Exception as e:
        LOGGER.error(f"Search error: {e}")
        return None


# =====================================================
# COBALT API FALLBACK — when yt-dlp completely fails
# =====================================================

_COBALT_APIS = [
    "https://cobalt-api.ayo.tf",
    "https://cobalt.api.timelessnesses.me",
    "https://api.cobalt.best",
]


def _cobalt_get_url(youtube_url: str, video: bool = False) -> str:
    """Get stream URL via Cobalt API (public instances)"""
    payload = {
        "url": youtube_url,
        "downloadMode": "auto" if video else "audio",
        "audioFormat": "opus",
    }
    if video:
        payload["videoQuality"] = "720"

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    for api_base in _COBALT_APIS:
        try:
            LOGGER.info(f"Cobalt attempt: {api_base}")
            with httpx.Client(timeout=20, follow_redirects=True) as client:
                resp = client.post(f"{api_base}/", json=payload, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    dl_url = data.get("url") or data.get("audio")
                    if dl_url:
                        LOGGER.info(f"Cobalt success from {api_base}")
                        return dl_url
                    LOGGER.warning(f"Cobalt {api_base} returned no URL: {data}")
                else:
                    LOGGER.warning(f"Cobalt {api_base} HTTP {resp.status_code}")
        except Exception as e:
            LOGGER.warning(f"Cobalt {api_base} error: {str(e)[:60]}")
        time.sleep(1)

    return None


# =====================================================
# PIPED API FALLBACK
# =====================================================

_PIPED_APIS = [
    "https://pipedapi.kavin.rocks",
    "https://piped-api.privacy.com.de",
    "https://api.piped.yt",
]


def _piped_get_url(video_id: str, video: bool = False) -> str:
    """Get stream URL via Piped API"""
    for api_base in _PIPED_APIS:
        try:
            LOGGER.info(f"Piped attempt: {api_base}")
            with httpx.Client(timeout=15, follow_redirects=True) as client:
                resp = client.get(f"{api_base}/streams/{video_id}")
                if resp.status_code == 200:
                    data = resp.json()
                    if not video:
                        # Get audio stream
                        streams = data.get("audioStreams", [])
                        if streams:
                            # Sort by bitrate, pick best
                            streams.sort(key=lambda x: x.get("bitrate", 0), reverse=True)
                            url = streams[0].get("url")
                            if url:
                                LOGGER.info(f"Piped audio success from {api_base}")
                                return url
                    else:
                        # Get video stream
                        streams = data.get("videoStreams", [])
                        # Filter <=720p with audio
                        good = [s for s in streams if s.get("videoOnly") is False and (s.get("height", 9999) <= 720)]
                        if not good:
                            good = [s for s in streams if s.get("height", 9999) <= 720]
                        if not good:
                            good = streams
                        if good:
                            url = good[0].get("url")
                            if url:
                                LOGGER.info(f"Piped video success from {api_base}")
                                return url
                else:
                    LOGGER.warning(f"Piped {api_base} HTTP {resp.status_code}")
        except Exception as e:
            LOGGER.warning(f"Piped {api_base} error: {str(e)[:60]}")
        time.sleep(1)

    return None


# =====================================================
# YT-DLP EXTRACTION — smart retry
# =====================================================

def _ytdlp_get_url(url: str, video: bool) -> str:
    """Try yt-dlp with multiple player client strategies"""
    for fmt_str, player_client, desc in _STRATEGIES:
        LOGGER.info(f"yt-dlp strategy: {desc}")
        opts = _base_opts()
        opts["format"] = fmt_str
        if player_client:
            yt_args = opts.get("extractor_args", {}).get("youtube", {}).copy()
            yt_args["player_client"] = player_client
            opts["extractor_args"] = {"youtube": yt_args}

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)

                # Direct URL
                stream_url = info.get("url")
                if stream_url:
                    LOGGER.info(f"yt-dlp success [{desc}]: format={info.get('format', '?')}")
                    return stream_url

                # Merged format
                req_fmts = info.get("requested_formats")
                if req_fmts:
                    if not video:
                        for f in req_fmts:
                            if f.get("acodec") != "none" and f.get("url"):
                                LOGGER.info(f"yt-dlp merged audio [{desc}]")
                                return f["url"]
                    # For video or fallback, return first with URL
                    for f in req_fmts:
                        if f.get("url"):
                            LOGGER.info(f"yt-dlp merged [{desc}]")
                            return f["url"]

                LOGGER.warning(f"yt-dlp [{desc}]: extracted but no URL in result")

        except Exception as e:
            LOGGER.warning(f"yt-dlp [{desc}] failed: {str(e)[:80]}")

        time.sleep(2)

    return None


def _ytdlp_download(url: str, video: bool) -> str:
    """Download via yt-dlp as last resort"""
    suffix = "_v" if video else ""
    outtmpl = f"downloads/%(id)s{suffix}.%(ext)s"
    all_exts = [".m4a", ".webm", ".opus", ".mp3", ".ogg", ".wav",
                ".mp4", ".mkv", ".3gp", ".flv"]

    # Only try the most reliable strategies for download
    download_strats = [
        ("bestaudio/best", ["web_creator"], "dl:web_creator"),
        ("bestaudio/best", ["mweb"], "dl:mweb"),
        ("bestaudio/best", ["ios"], "dl:ios"),
        ("best", ["web_creator"], "dl:web_creator+best"),
        ("best", None, "dl:default"),
    ]

    for fmt_str, player_client, desc in download_strats:
        LOGGER.info(f"Download strategy: {desc}")
        opts = _base_opts()
        opts["format"] = fmt_str
        opts["outtmpl"] = outtmpl
        if player_client:
            yt_args = opts.get("extractor_args", {}).get("youtube", {}).copy()
            yt_args["player_client"] = player_client
            opts["extractor_args"] = {"youtube": yt_args}

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                fname = ydl.prepare_filename(info)

                base = os.path.splitext(fname)[0]
                for ext in all_exts:
                    if os.path.exists(base + ext):
                        return base + ext
                if os.path.exists(fname):
                    return fname

                vid_id = info.get("id", "")
                if vid_id:
                    prefix = f"downloads/{vid_id}{suffix}"
                    for ext in all_exts:
                        if os.path.exists(prefix + ext):
                            return prefix + ext

        except Exception as e:
            LOGGER.warning(f"Download [{desc}] failed: {str(e)[:80]}")

        time.sleep(2)

    return None


# =====================================================
# MAIN MEDIA GETTER — 4-layer fallback
# =====================================================

def get_media(url: str, video: bool):
    """
    4-layer fallback system:
      1. yt-dlp stream URL (multiple player clients)
      2. Cobalt API (public instances)
      3. Piped API (public instances)
      4. yt-dlp download (file to disk)
    """
    # Extract video ID for API fallbacks
    vid_match = re.search(r'(?:v=|/)([a-zA-Z0-9_-]{11})', url)
    video_id = vid_match.group(1) if vid_match else None

    # --- Layer 1: yt-dlp stream URL ---
    LOGGER.info("Layer 1: yt-dlp stream URL")
    result = _ytdlp_get_url(url, video)
    if result:
        LOGGER.info("Layer 1 SUCCESS")
        return result

    # --- Layer 2: Cobalt API ---
    LOGGER.info("Layer 2: Cobalt API")
    result = _cobalt_get_url(url, video)
    if result:
        LOGGER.info("Layer 2 SUCCESS (Cobalt)")
        return result

    # --- Layer 3: Piped API ---
    if video_id:
        LOGGER.info("Layer 3: Piped API")
        result = _piped_get_url(video_id, video)
        if result:
            LOGGER.info("Layer 3 SUCCESS (Piped)")
            return result

    # --- Layer 4: yt-dlp download to disk ---
    LOGGER.info("Layer 4: yt-dlp download")
    cleanup_downloads()
    result = _ytdlp_download(url, video)
    if result:
        LOGGER.info("Layer 4 SUCCESS (downloaded)")
        return result

    raise Exception("All 4 layers failed. YouTube may be blocking. Try later.")


# =====================================================
# HELPERS
# =====================================================

def fmt_dur(s):
    if not s:
        return "Live"
    try:
        s = int(s)
        return f"{s // 60}:{s % 60:02d}"
    except Exception:
        return str(s)


async def safe_react(client, message, emoji):
    try:
        await client.send_reaction(
            chat_id=message.chat.id,
            message_id=message.id,
            emoji=emoji,
        )
    except Exception:
        pass


async def ensure_assistant(chat_id: int):
    """Ensure assistant is in the group"""
    try:
        me = await assistant.get_me()
        await assistant.get_chat_member(chat_id, me.id)
        LOGGER.info(f"Assistant already in {chat_id}")
        return True
    except Exception:
        LOGGER.info(f"Assistant not in {chat_id}, joining...")

    try:
        invite = await app.export_chat_invite_link(chat_id)
        await assistant.join_chat(invite)
        await asyncio.sleep(5)
        LOGGER.info("Assistant joined via invite")
        return True
    except Exception as e:
        LOGGER.warning(f"Invite join failed: {e}")

    try:
        chat = await app.get_chat(chat_id)
        if chat.username:
            await assistant.join_chat(chat.username)
            await asyncio.sleep(5)
            LOGGER.info(f"Assistant joined via @{chat.username}")
            return True
    except Exception as e:
        LOGGER.warning(f"Username join failed: {e}")

    LOGGER.error(f"Could not join {chat_id}")
    return False


async def try_play_stream(chat_id, media_path, video, max_retries=4):
    """Play in voice chat with retry logic — 3s intervals"""
    if video:
        stream = MediaStream(media_path, video_flags=MediaStream.Flags.AUTO_DETECT)
    else:
        stream = MediaStream(media_path, video_flags=MediaStream.Flags.IGNORE)

    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            LOGGER.info(f"Play attempt {attempt}/{max_retries}")
            await calls.play(chat_id, stream)
            LOGGER.info(f"Playing in {chat_id}")
            return True
        except Exception as e:
            last_error = e
            err = str(e).lower()
            LOGGER.error(f"Play attempt {attempt} failed: {e}")

            if "no active group call" in err or "group_call_invalid" in err:
                if attempt < max_retries:
                    await asyncio.sleep(3)
                    continue
                return "NO_VC"
            elif "chat_admin_required" in err or "not found" in err:
                return "NO_PERM"
            else:
                if attempt < max_retries:
                    await asyncio.sleep(3)
                    continue
                return f"ERROR: {str(e)[:100]}"

    return f"FAILED: {str(last_error)[:100]}"


# =====================================================
# SECURITY
# =====================================================

def _sanitize_query(text: str) -> str:
    """Sanitize user input"""
    text = text.strip()
    if len(text) > 200:
        text = text[:200]
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
    # Block shell injection characters
    text = re.sub(r'[;&|`$(){}]', '', text)
    return text


def _check_rate_limit(user_id: int) -> bool:
    """Returns True if user should wait"""
    now = time.time()
    last = _USER_COOLDOWN.get(user_id, 0)
    if now - last < _COOLDOWN_SECONDS:
        return True
    _USER_COOLDOWN[user_id] = now
    return False


def _check_concurrent(chat_id: int) -> bool:
    """Check if too many concurrent requests in this chat"""
    now = time.time()
    # Clean old entries
    _GLOBAL_SPAM[chat_id] = [t for t in _GLOBAL_SPAM.get(chat_id, []) if now - t < 30]
    if len(_GLOBAL_SPAM.get(chat_id, [])) >= _MAX_CONCURRENT:
        return True
    _GLOBAL_SPAM.setdefault(chat_id, []).append(now)
    return False


# =====================================================
# MAIN PLAY FUNCTION
# =====================================================

async def _play(client, message: Message, video: bool):
    """Main play handler with 4-layer fallback and security"""
    await safe_react(client, message, config.random_emoji())
    cmd = "vplay" if video else "play"

    # Security: check if user is banned
    try:
        from MusicBangla.plugins.security import is_banned, is_url_blocked, log_action
        if is_banned(message.from_user.id):
            log_action(f"BLOCKED: banned user {message.from_user.id} tried /{cmd}")
            return
    except ImportError:
        pass

    # Security: rate limiting per user
    if _check_rate_limit(message.from_user.id):
        return await message.reply_text(
            f"⏳ <b>{_COOLDOWN_SECONDS} সেকেন্ড অপেক্ষা করুন।</b>"
        )

    # Security: anti-flood per chat
    if _check_concurrent(message.chat.id):
        return await message.reply_text(
            "⏳ <b>একসাথে অনেক রিকোয়েস্ট!</b> কিছুক্ষণ পর চেষ্টা করুন।"
        )

    if len(message.command) < 2 and not message.reply_to_message:
        return await message.reply_text(
            f"<b>গানের নাম দাও!</b>\n\nউদাহরণ: <code>/{cmd} tum hi ho</code>"
        )

    raw_query = (
        " ".join(message.command[1:])
        if len(message.command) > 1
        else (message.reply_to_message.text or "")
    )

    query = _sanitize_query(raw_query)
    if not query:
        return await message.reply_text("<b>সঠিক গানের নাম দাও!</b>")

    # Security: block URLs that aren't YouTube
    if query.startswith("http"):
        if not re.match(
            r'https?://(www\.)?(youtube\.com|youtu\.be|music\.youtube\.com)/', query
        ):
            return await message.reply_text("<b>শুধু YouTube লিংক সমর্থিত!</b>")
        # Check blocked URL patterns
        try:
            from MusicBangla.plugins.security import is_url_blocked
            if is_url_blocked(query):
                return await message.reply_text("🔒 <b>এই URL ব্লক করা হয়েছে।</b>")
        except ImportError:
            pass

    status = await message.reply_text("🔎 <b>খুঁজছি...</b>")

    try:
        # Step 1: Search
        LOGGER.info(f"Searching: {query}")
        loop = asyncio.get_event_loop()
        try:
            info = await asyncio.wait_for(
                loop.run_in_executor(None, yt_search_sync, query),
                timeout=15,
            )
        except asyncio.TimeoutError:
            return await status.edit("⏱ সার্চ টাইমআউট। আবার চেষ্টা করুন।")
        except Exception as e:
            LOGGER.error(f"Search failed: {e}")
            return await status.edit(f"❌ সার্চ ব্যর্থ: <code>{str(e)[:80]}</code>")

        if not info:
            return await status.edit(
                f"❌ <b>'{query}'</b> খুঁজে পাওয়া যায়নি।\nঅন্য নাম দিয়ে চেষ্টা করুন।"
            )

        # Security: block very long videos (>3 hours)
        if info.get("duration", 0) > 10800:
            return await status.edit("❌ <b>৩ ঘণ্টার বেশি লম্বা ভিডিও সমর্থিত নয়।</b>")

        # Step 2: Status
        icon = "🎬" if video else "🎵"
        await status.edit(
            f"📥 <b>মিডিয়া লোড হচ্ছে...</b>\n\n"
            f"{icon} <code>{info['title'][:50]}</code>\n"
            f"⏱ <code>{fmt_dur(info['duration'])}</code>\n\n"
            f"⏳ 4-layer fallback দিয়ে চেষ্টা করছি..."
        )

        # Step 3: Assistant + Media (parallel)
        LOGGER.info(f"Getting media: {info['link']}")

        assistant_ok, media_path = await asyncio.gather(
            ensure_assistant(message.chat.id),
            loop.run_in_executor(None, get_media, info["link"], video),
            return_exceptions=True,
        )

        if isinstance(assistant_ok, Exception) or assistant_ok is False:
            LOGGER.error(f"Assistant error: {assistant_ok}")
            return await status.edit(
                "❌ <b>Assistant গ্রুপে যোগ হতে পারেনি!</b>\n\n"
                "🔧 Assistant অ্যাকাউন্ট manually গ্রুপে add করুন,\n"
                "বটকে admin করুন, তারপর <code>/play</code> দিন।"
            )

        if isinstance(media_path, Exception):
            LOGGER.error(f"Media error: {media_path}")
            return await status.edit(
                f"❌ <b>ডাউনলোড ব্যর্থ!</b>\n<code>{str(media_path)[:100]}</code>\n\n"
                f"🔧 আবার চেষ্টা করুন বা অন্য গান দিন।"
            )

        if not media_path:
            return await status.edit("❌ মিডিয়া পাওয়া যায়নি। অন্য গান দিয়ে চেষ্টা করুন।")

        LOGGER.info(f"Media ready: {media_path}")

        # Step 4: Play with retry
        await status.edit("🎶 <b>Voice Chat-এ যোগ হচ্ছে...</b>")
        await asyncio.sleep(1)

        result = await try_play_stream(message.chat.id, str(media_path), video)

        if result is True:
            ACTIVE_CHATS[message.chat.id] = info
        elif result == "NO_VC":
            return await status.edit(
                "❌ <b>Voice Chat চালু নেই!</b>\n\n"
                "🔧 গ্রুপে Voice Chat শুরু করুন,\n"
                "তারপর <code>/play</code> দিন।"
            )
        elif result == "NO_PERM":
            return await status.edit(
                "❌ <b>Permission নেই!</b>\n\n"
                "🔧 Assistant-কে admin করুন\n"
                "(Manage Voice Chats permission দিন)।"
            )
        else:
            return await status.edit(
                f"❌ <b>স্ট্রিমিং ব্যর্থ!</b>\n<code>{result}</code>\n\n"
                f"<code>/stop</code> করে আবার <code>/play</code> দিন।"
            )

        # Step 5: Success
        try:
            await status.delete()
        except Exception:
            pass

        caption = (
            f"╭───❀ ✦ ❀───╮\n"
            f"  {icon} <b>এখন {'ভিডিও' if video else 'গান'} বাজছে</b>\n"
            f"╰───❀ ✦ ❀───╯\n\n"
            f"🎵 <b>শিরোনাম:</b> {info['title']}\n"
            f"⏱ <b>সময়:</b> <code>{fmt_dur(info['duration'])}</code>\n"
            f"📺 <b>চ্যানেল:</b> {info['channel']}\n"
            f"🙋 <b>অনুরোধকারী:</b> {message.from_user.mention}\n\n"
            f"▫️ ⏸ <code>/pause</code> ▶️ <code>/resume</code> ⏭ <code>/skip</code> 🛑 <code>/stop</code>"
        )
        try:
            await message.reply_photo(photo=info["thumb"], caption=caption)
        except Exception:
            await message.reply_text(caption)

        try:
            await asyncio.sleep(0.3)
            await message.reply_sticker(config.random_play_sticker())
        except Exception:
            pass

    except Exception as e:
        LOGGER.error(f"Play error: {e}")
        import traceback
        traceback.print_exc()
        try:
            await status.edit(f"❌ ত্রুটি: <code>{str(e)[:100]}</code>")
        except Exception:
            pass


@app.on_message(filters.command(["play", "p"]) & filters.group)
async def play_cmd(client, message: Message):
    await _play(client, message, video=False)


@app.on_message(filters.command(["vplay", "vp"]) & filters.group)
async def vplay_cmd(client, message: Message):
    await _play(client, message, video=True)
