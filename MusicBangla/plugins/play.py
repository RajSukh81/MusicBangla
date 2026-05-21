import os
import asyncio
import yt_dlp
from youtubesearchpython.__future__ import VideosSearch
from pyrogram import filters
from pyrogram.types import Message
from pytgcalls.types import MediaStream

import config
from MusicBangla import app, assistant, calls, LOGGER


YDL_OPTS = {
    "format": "bestaudio/best",
    "outtmpl": "downloads/%(id)s.%(ext)s",
    "geo_bypass": True,
    "nocheckcertificate": True,
    "quiet": True,
    "no_warnings": True,
    "default_search": "auto",
    "noplaylist": True,
}

os.makedirs("downloads", exist_ok=True)
ACTIVE_CHATS = {}  # chat_id -> video_info


async def search_youtube(query: str):
    results = VideosSearch(query, limit=1)
    data = await results.next()
    if not data["result"]:
        return None
    r = data["result"][0]
    return {
        "title": r["title"],
        "duration": r.get("duration") or "Live",
        "link": r["link"],
        "thumb": r["thumbnails"][0]["url"].split("?")[0],
        "channel": r["channel"]["name"],
        "id": r["id"],
    }


def download_audio(url: str) -> str:
    with yt_dlp.YoutubeDL(YDL_OPTS) as ydl:
        info = ydl.extract_info(url, download=True)
        path = ydl.prepare_filename(info)
        return path


async def safe_react(client, message, emoji):
    try:
        await client.send_reaction(chat_id=message.chat.id, message_id=message.id, emoji=emoji)
    except Exception:
        pass


@app.on_message(filters.command(["play", "p"]) & filters.group)
async def play_cmd(client, message: Message):
    await safe_react(client, message, config.random_emoji())

    if len(message.command) < 2 and not message.reply_to_message:
        return await message.reply_text(
            "❌ <b>গানের নাম দাও!</b>\n\nউদাহরণ: <code>/play tum hi ho</code>"
        )

    query = " ".join(message.command[1:]) if len(message.command) > 1 else (message.reply_to_message.text or "")
    status = await message.reply_text("🔎 <b>খুঁজছি আপনার গান...</b>")

    info = await search_youtube(query)
    if not info:
        return await status.edit("❌ কোনো গান পাওয়া যায়নি। অন্য নাম দিয়ে চেষ্টা করুন।")

    await status.edit(f"📥 <b>ডাউনলোড হচ্ছে:</b>\n<code>{info['title']}</code>")
    try:
        loop = asyncio.get_event_loop()
        audio_path = await loop.run_in_executor(None, download_audio, info["link"])
    except Exception as e:
        LOGGER.error(f"Download error: {e}")
        return await status.edit("❌ ডাউনলোড করতে সমস্যা হলো। আবার চেষ্টা করুন।")

    # Assistant কে চ্যাটে invite করার চেষ্টা
    try:
        try:
            await assistant.get_chat(message.chat.id)
        except Exception:
            invite = await app.export_chat_invite_link(message.chat.id)
            await assistant.join_chat(invite)
            await asyncio.sleep(2)
    except Exception as e:
        LOGGER.warning(f"Assistant join failed: {e}")
        return await status.edit(
            "❌ Assistant অ্যাকাউন্ট গ্রুপে যোগ হতে পারেনি।\n\n"
            "🔧 Assistant অ্যাকাউন্টটি manually গ্রুপে add করুন, তারপর আবার <code>/play</code> দিন।"
        )

    # VC join + stream
    try:
        await calls.play(message.chat.id, MediaStream(audio_path))
        ACTIVE_CHATS[message.chat.id] = info
    except Exception as e:
        LOGGER.error(f"Play error: {e}")
        return await status.edit(
            f"❌ গান চালানো গেল না।\n\n<b>Error:</b> <code>{e}</code>\n\n"
            "নিশ্চিত করুন:\n• Voice Chat চালু আছে\n• বট admin (Manage VC permission সহ)\n• Assistant গ্রুপে আছে"
        )

    await status.delete()
    caption = (
        f"╭───❀ ✦ ❀───╮\n"
        f"   🎶 <b>এখন বাজছে</b>\n"
        f"╰───❀ ✦ ❀───╯\n\n"
        f"🎵 <b>শিরোনাম:</b> {info['title']}\n"
        f"⏱ <b>সময়:</b> <code>{info['duration']}</code>\n"
        f"📺 <b>চ্যানেল:</b> {info['channel']}\n"
        f"🙋 <b>অনুরোধকারী:</b> {message.from_user.mention}\n\n"
        f"▫️ ⏸ /pause  ▶️ /resume  ⏭ /skip  🛑 /stop"
    )
    await message.reply_photo(photo=info["thumb"], caption=caption)

    # Play sticker
    try:
        await asyncio.sleep(0.3)
        await message.reply_sticker(config.random_play_sticker())
    except Exception:
        pass
