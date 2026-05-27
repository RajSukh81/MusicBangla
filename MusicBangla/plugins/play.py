import os
import asyncio
import time
import re
import yt_dlp
import httpx
from pyrogram import filters
from pyrogram.types import Message
from pytgcalls.types import MediaStream

import config
from MusicBangla import app, assistant, calls, LOGGER


os.makedirs("downloads", exist_ok=True)
ACTIVE_CHATS = {}

# --- Rate limiting & flood protection ---
_USER_COOLDOWN = {}
_COOLDOWN_SECONDS = 5
_GLOBAL_SPAM = {}
_MAX_CONCURRENT = 3


# --- Cookies (for YouTube fallback) ---
_COOKIE_FILE = None

def _fix_cookie_line(line: str) -> str:
    line = line.strip()
    if not line or line.startswith("#"):
        return line
    parts = line.split()
    if len(parts) >= 7:
        return "\t".join(parts[:6]) + "\t" + " ".join(parts[6:])
    elif len(parts) == 6:
        return "\t".join(parts) + "\t"
    return line

if os.environ.get("YT_COOKIES"):
    try:
        raw = os.environ["YT_COOKIES"].replace("\\n", "\n")
        lines = raw.split("\n")
        fixed = ["# Netscape HTTP Cookie File"]
        for line in lines:
            line = line.strip()
            if not line:
                continue
            if "Netscape" in line or "HTTP Cookie" in line:
                continue
            if line.startswith("#"):
                fixed.append(line)
                continue
            fixed.append(_fix_cookie_line(line))
        with open("cookies.txt", "w") as f:
            f.write("\n".join(fixed) + "\n")
        _COOKIE_FILE = "cookies.txt"
        cookie_count = sum(1 for l in fixed if l and not l.startswith("#"))
        LOGGER.info(f"YouTube cookies loaded: {cookie_count} cookies")
    except Exception as e:
        LOGGER.error(f"Cookie write error: {e}")
elif os.path.exists("cookies.txt"):
    _COOKIE_FILE = "cookies.txt"


# =====================================================
# YT-DLP BASE OPTIONS
# =====================================================

def _base_opts():
    return {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 20,
        "retries": 3,
        "fragment_retries": 3,
        "geo_bypass": True,
        "nocheckcertificate": True,
        "no_check_formats": True,
        "check_formats": False,
        "source_address": "0.0.0.0",
        "format_sort": ["abr", "asr"],
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
        },
    }


def cleanup_downloads():
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
# SOURCE 1: SOUNDCLOUD (Primary — works on Heroku)
# =====================================================

def _soundcloud_search_and_get(query: str, video: bool):
    """
    Search SoundCloud via yt-dlp scsearch and return stream info.
    Returns (stream_url_or_path, info_dict) or (None, None).
    SoundCloud is audio-only, video flag is ignored.
    """
    LOGGER.info(f"SoundCloud search: {query}")
    opts = _base_opts()
    opts["format"] = "http_mp3_0_0/bestaudio/best"
    opts["default_search"] = "scsearch1"

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"scsearch1:{query}", download=False)

            # scsearch returns a playlist with entries
            if info.get("_type") == "playlist" and info.get("entries"):
                entries = list(info["entries"])
                if entries:
                    info = entries[0]

            if not info:
                LOGGER.warning("SoundCloud: no results")
                return None, None

            title = info.get("title", "Unknown")
            duration = info.get("duration", 0)
            uploader = info.get("uploader", "SoundCloud")
            thumb = info.get("thumbnail", "")
            webpage = info.get("webpage_url", "")

            # Get direct stream URL
            stream_url = info.get("url")
            if not stream_url:
                # Try formats
                fmts = info.get("formats", [])
                # Prefer http_mp3 over hls
                for f in fmts:
                    fid = f.get("format_id", "")
                    if "http_mp3" in fid and f.get("url"):
                        stream_url = f["url"]
                        LOGGER.info(f"SoundCloud: using format {fid}")
                        break
                if not stream_url:
                    for f in fmts:
                        if f.get("url"):
                            stream_url = f["url"]
                            LOGGER.info(f"SoundCloud: using format {f.get('format_id')}")
                            break

            if stream_url:
                LOGGER.info(f"SoundCloud SUCCESS: {title}")
                return stream_url, {
                    "title": title,
                    "duration": int(duration) if duration else 0,
                    "channel": uploader,
                    "thumb": thumb,
                    "link": webpage,
                    "source": "SoundCloud",
                }

            LOGGER.warning("SoundCloud: found track but no stream URL")
            return None, None

    except Exception as e:
        LOGGER.error(f"SoundCloud error: {e}")
        return None, None


# =====================================================
# SOURCE 2: JIOSAAVN API (Secondary — Indian music)
# =====================================================

_JIOSAAVN_APIS = [
    "https://jiosaavn-api.vercel.app",
    "https://jiosaavn-api-v3.vercel.app",
]


def _jiosaavn_search_and_get(query: str, video: bool):
    """
    Search JioSaavn via public API and return stream info.
    Returns (stream_url_or_path, info_dict) or (None, None).
    """
    LOGGER.info(f"JioSaavn search: {query}")

    for api_base in _JIOSAAVN_APIS:
        try:
            with httpx.Client(timeout=15, follow_redirects=True) as client:
                # Step 1: Search
                resp = client.get(f"{api_base}/search", params={"query": query})
                if resp.status_code != 200:
                    LOGGER.warning(f"JioSaavn {api_base} search HTTP {resp.status_code}")
                    continue

                data = resp.json()
                results = data.get("results", data.get("data", []))
                if not results:
                    LOGGER.warning(f"JioSaavn {api_base}: no results")
                    continue

                # Get first result
                song = results[0] if isinstance(results, list) else None
                if not song:
                    continue

                song_id = song.get("id", "")
                title = song.get("title", song.get("name", "Unknown"))

                if not song_id:
                    continue

                # Step 2: Get song details with download URL
                resp2 = client.get(f"{api_base}/song", params={"id": song_id})
                if resp2.status_code != 200:
                    LOGGER.warning(f"JioSaavn song detail HTTP {resp2.status_code}")
                    continue

                song_data = resp2.json()

                # Extract media URL
                media_url = song_data.get("media_url", "")
                media_urls = song_data.get("media_urls", {})

                # Prefer 320kbps > 160kbps > 96kbps
                stream_url = None
                for quality in ["320_KBPS", "160_KBPS", "96_KBPS"]:
                    if media_urls.get(quality):
                        stream_url = media_urls[quality]
                        LOGGER.info(f"JioSaavn: got {quality}")
                        break

                if not stream_url:
                    stream_url = media_url

                if not stream_url:
                    LOGGER.warning(f"JioSaavn {api_base}: no media URL")
                    continue

                # Get metadata
                duration_str = song_data.get("duration", song_data.get("more_info", {}).get("duration", "0"))
                try:
                    duration = int(duration_str)
                except (ValueError, TypeError):
                    duration = 0

                artist = (
                    song_data.get("more_info", {}).get("singers", "")
                    or song_data.get("subtitle", "")
                    or "JioSaavn"
                )
                image = song_data.get("image", "")
                if isinstance(image, list) and image:
                    image = image[-1].get("link", "") if isinstance(image[-1], dict) else image[-1]

                LOGGER.info(f"JioSaavn SUCCESS: {title}")
                return stream_url, {
                    "title": title,
                    "duration": duration,
                    "channel": artist,
                    "thumb": image,
                    "link": song_data.get("perma_url", ""),
                    "source": "JioSaavn",
                }

        except Exception as e:
            LOGGER.warning(f"JioSaavn {api_base} error: {str(e)[:80]}")
            continue

    return None, None


# =====================================================
# SOURCE 3: YOUTUBE (Fallback — may not work on Heroku)
# =====================================================

_YT_STRATEGIES = [
    ("bestaudio/best", ["web_creator"], "yt:web_creator"),
    ("bestaudio/best", ["mweb"], "yt:mweb"),
    ("bestaudio/best", ["ios"], "yt:ios"),
    ("bestaudio/best", ["web"], "yt:web"),
    ("bestaudio/best", None, "yt:default"),
]


def _youtube_search_sync(query: str):
    """YouTube search — may fail if blocked"""
    try:
        from youtubesearchpython import VideosSearch
        search = VideosSearch(query, limit=1)
        result = search.result()
        if not result or not result.get("result"):
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
            "source": "YouTube",
        }
    except Exception as e:
        LOGGER.error(f"YT search error: {e}")
        return None


def _youtube_get_stream(url: str, video: bool) -> str:
    """Try yt-dlp with multiple strategies for YouTube"""
    for fmt_str, player_client, desc in _YT_STRATEGIES:
        LOGGER.info(f"yt-dlp strategy: {desc}")
        opts = _base_opts()
        opts["format"] = fmt_str
        if _COOKIE_FILE:
            opts["cookiefile"] = _COOKIE_FILE
        if player_client:
            opts["extractor_args"] = {"youtube": {"player_client": player_client}}

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
                stream_url = info.get("url")
                if stream_url:
                    LOGGER.info(f"YouTube stream [{desc}] success")
                    return stream_url
                req_fmts = info.get("requested_formats")
                if req_fmts:
                    for f in req_fmts:
                        if f.get("url"):
                            return f["url"]
        except Exception as e:
            LOGGER.warning(f"YouTube [{desc}] failed: {str(e)[:60]}")

        time.sleep(1)

    return None


def _youtube_download(url: str, video: bool) -> str:
    """Download from YouTube as last resort"""
    cleanup_downloads()
    suffix = "_v" if video else ""
    outtmpl = f"downloads/%(id)s{suffix}.%(ext)s"
    all_exts = [".m4a", ".webm", ".opus", ".mp3", ".ogg", ".mp4"]

    for fmt_str, player_client, desc in _YT_STRATEGIES[:3]:
        opts = _base_opts()
        opts["format"] = fmt_str
        opts["outtmpl"] = outtmpl
        if _COOKIE_FILE:
            opts["cookiefile"] = _COOKIE_FILE
        if player_client:
            opts["extractor_args"] = {"youtube": {"player_client": player_client}}
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
        except Exception as e:
            LOGGER.warning(f"YT download [{desc}] failed: {str(e)[:60]}")
        time.sleep(1)

    return None


# =====================================================
# MASTER SEARCH + STREAM: multi-source fallback
# =====================================================

def search_and_get_media(query: str, video: bool):
    """
    Multi-source search and stream extraction:
      1. SoundCloud (yt-dlp scsearch — always works)
      2. JioSaavn API (good for Indian/Bollywood music)
      3. YouTube (fallback — may be blocked on Heroku)

    Returns (stream_url_or_path, info_dict) or raises Exception.
    """
    errors = []

    # --- Source 1: SoundCloud ---
    LOGGER.info("=== Source 1: SoundCloud ===")
    try:
        stream, info = _soundcloud_search_and_get(query, video)
        if stream and info:
            return stream, info
        errors.append("SoundCloud: no results")
    except Exception as e:
        errors.append(f"SoundCloud: {str(e)[:60]}")
        LOGGER.error(f"SoundCloud failed: {e}")

    # --- Source 2: JioSaavn ---
    LOGGER.info("=== Source 2: JioSaavn ===")
    try:
        stream, info = _jiosaavn_search_and_get(query, video)
        if stream and info:
            return stream, info
        errors.append("JioSaavn: no results or API down")
    except Exception as e:
        errors.append(f"JioSaavn: {str(e)[:60]}")
        LOGGER.error(f"JioSaavn failed: {e}")

    # --- Source 3: YouTube (likely blocked on Heroku) ---
    LOGGER.info("=== Source 3: YouTube (fallback) ===")
    try:
        yt_info = _youtube_search_sync(query)
        if yt_info:
            # Try stream URL
            stream = _youtube_get_stream(yt_info["link"], video)
            if stream:
                return stream, yt_info
            # Try download
            path = _youtube_download(yt_info["link"], video)
            if path:
                return path, yt_info
        errors.append("YouTube: blocked or no results")
    except Exception as e:
        errors.append(f"YouTube: {str(e)[:60]}")
        LOGGER.error(f"YouTube failed: {e}")

    raise Exception(f"All sources failed: {'; '.join(errors)}")


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
    text = text.strip()
    if len(text) > 200:
        text = text[:200]
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
    text = re.sub(r'[;&|`$(){}]', '', text)
    return text


def _check_rate_limit(user_id: int) -> bool:
    now = time.time()
    last = _USER_COOLDOWN.get(user_id, 0)
    if now - last < _COOLDOWN_SECONDS:
        return True
    _USER_COOLDOWN[user_id] = now
    return False


def _check_concurrent(chat_id: int) -> bool:
    now = time.time()
    _GLOBAL_SPAM[chat_id] = [t for t in _GLOBAL_SPAM.get(chat_id, []) if now - t < 30]
    if len(_GLOBAL_SPAM.get(chat_id, [])) >= _MAX_CONCURRENT:
        return True
    _GLOBAL_SPAM.setdefault(chat_id, []).append(now)
    return False


# =====================================================
# MAIN PLAY FUNCTION
# =====================================================

async def _play(client, message: Message, video: bool):
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

    if _check_rate_limit(message.from_user.id):
        return await message.reply_text(
            f"<b>{_COOLDOWN_SECONDS} সেকেন্ড অপেক্ষা করুন।</b>"
        )

    if _check_concurrent(message.chat.id):
        return await message.reply_text(
            "<b>একসাথে অনেক রিকোয়েস্ট!</b> কিছুক্ষণ পর চেষ্টা করুন।"
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

    # Allow YouTube URLs as direct input
    is_yt_url = False
    if query.startswith("http"):
        if re.match(
            r'https?://(www\.)?(youtube\.com|youtu\.be|music\.youtube\.com)/', query
        ):
            is_yt_url = True
        else:
            return await message.reply_text("<b>শুধু YouTube লিংক বা গানের নাম দাও!</b>")
        try:
            from MusicBangla.plugins.security import is_url_blocked
            if is_url_blocked(query):
                return await message.reply_text("<b>এই URL ব্লক করা হয়েছে।</b>")
        except ImportError:
            pass

    status = await message.reply_text("<b>খুঁজছি...</b> (SoundCloud + JioSaavn + YouTube)")

    try:
        loop = asyncio.get_event_loop()

        if is_yt_url:
            # Direct YouTube URL — try YouTube first
            LOGGER.info(f"Direct YouTube URL: {query}")
            await status.edit("<b>YouTube লিংক থেকে লোড হচ্ছে...</b>")

            yt_info = await loop.run_in_executor(None, _youtube_search_sync,
                                                  query.split("?v=")[-1].split("&")[0] if "v=" in query else query)
            if not yt_info:
                yt_info = {"title": "YouTube Audio", "duration": 0, "channel": "YouTube",
                           "thumb": "", "link": query, "source": "YouTube"}

            assistant_ok = await ensure_assistant(message.chat.id)
            media_path = await loop.run_in_executor(None, _youtube_get_stream, query, video)

            if not media_path:
                # YouTube blocked, extract song name and try other sources
                song_name = yt_info.get("title", "")
                if song_name and song_name != "YouTube Audio":
                    await status.edit(f"<b>YouTube ব্লক! '{song_name}' অন্য সোর্সে খুঁজছি...</b>")
                    media_path, yt_info = await loop.run_in_executor(
                        None, search_and_get_media, song_name, video
                    )
                else:
                    return await status.edit(
                        "<b>YouTube এই সার্ভার থেকে ব্লক!</b>\n"
                        "গানের নাম দিয়ে চেষ্টা করুন: <code>/play tum hi ho</code>"
                    )

            if isinstance(assistant_ok, Exception) or assistant_ok is False:
                return await status.edit(
                    "<b>Assistant গ্রুপে যোগ হতে পারেনি!</b>\n"
                    "Assistant manually গ্রুপে add করুন।"
                )

            info = yt_info

        else:
            # Text query — use multi-source search
            LOGGER.info(f"Multi-source search: {query}")

            try:
                assistant_task = ensure_assistant(message.chat.id)
                media_task = loop.run_in_executor(None, search_and_get_media, query, video)

                assistant_ok, media_result = await asyncio.gather(
                    assistant_task, media_task, return_exceptions=True
                )
            except Exception as e:
                LOGGER.error(f"Gather error: {e}")
                return await status.edit(f"<b>ত্রুটি:</b> <code>{str(e)[:100]}</code>")

            if isinstance(assistant_ok, Exception) or assistant_ok is False:
                return await status.edit(
                    "<b>Assistant গ্রুপে যোগ হতে পারেনি!</b>\n"
                    "Assistant manually গ্রুপে add করুন।"
                )

            if isinstance(media_result, Exception):
                LOGGER.error(f"Media error: {media_result}")
                return await status.edit(
                    f"<b>সব সোর্স ব্যর্থ!</b>\n<code>{str(media_result)[:120]}</code>\n\n"
                    "অন্য গানের নাম দিয়ে চেষ্টা করুন।"
                )

            media_path, info = media_result

        if not media_path:
            return await status.edit("<b>মিডিয়া পাওয়া যায়নি।</b> অন্য গান দিয়ে চেষ্টা করুন।")

        LOGGER.info(f"Media ready from {info.get('source', '?')}: {media_path}")

        # Play
        source_name = info.get("source", "Unknown")
        icon = "🎵"
        await status.edit(
            f"🎶 <b>Voice Chat-এ যোগ হচ্ছে...</b>\n"
            f"📡 সোর্স: {source_name}"
        )
        await asyncio.sleep(1)

        result = await try_play_stream(message.chat.id, str(media_path), video)

        if result is True:
            ACTIVE_CHATS[message.chat.id] = info
        elif result == "NO_VC":
            return await status.edit(
                "<b>Voice Chat চালু নেই!</b>\n"
                "গ্রুপে Voice Chat শুরু করুন, তারপর <code>/play</code> দিন।"
            )
        elif result == "NO_PERM":
            return await status.edit(
                "<b>Permission নেই!</b>\n"
                "Assistant-কে admin করুন (Manage Voice Chats permission দিন)।"
            )
        else:
            return await status.edit(
                f"<b>স্ট্রিমিং ব্যর্থ!</b>\n<code>{result}</code>\n\n"
                f"<code>/stop</code> করে আবার <code>/play</code> দিন।"
            )

        # Success
        try:
            await status.delete()
        except Exception:
            pass

        caption = (
            f"╭───❀ ✦ ❀───╮\n"
            f"  {icon} <b>এখন গান বাজছে</b>\n"
            f"╰───❀ ✦ ❀───╯\n\n"
            f"🎵 <b>শিরোনাম:</b> {info.get('title', 'Unknown')}\n"
            f"⏱ <b>সময়:</b> <code>{fmt_dur(info.get('duration'))}</code>\n"
            f"📺 <b>শিল্পী:</b> {info.get('channel', 'Unknown')}\n"
            f"📡 <b>সোর্স:</b> {source_name}\n"
            f"🙋 <b>অনুরোধকারী:</b> {message.from_user.mention}\n\n"
            f"⏸ <code>/pause</code> ▶️ <code>/resume</code> ⏭ <code>/skip</code> 🛑 <code>/stop</code>"
        )
        thumb = info.get("thumb", "")
        try:
            if thumb and thumb.startswith("http"):
                await message.reply_photo(photo=thumb, caption=caption)
            else:
                await message.reply_text(caption)
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
            await status.edit(f"<b>ত্রুটি:</b> <code>{str(e)[:100]}</code>")
        except Exception:
            pass


@app.on_message(filters.command(["play", "p"]) & filters.group)
async def play_cmd(client, message: Message):
    await _play(client, message, video=False)


@app.on_message(filters.command(["vplay", "vp"]) & filters.group)
async def vplay_cmd(client, message: Message):
    await _play(client, message, video=True)
