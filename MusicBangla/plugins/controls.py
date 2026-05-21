from pyrogram import filters
from pyrogram.types import Message

from MusicBangla import app, calls, LOGGER
from MusicBangla.plugins.play import ACTIVE_CHATS


async def react(client, message, emoji):
    try:
        await client.send_reaction(chat_id=message.chat.id, message_id=message.id, emoji=emoji)
    except Exception:
        pass


@app.on_message(filters.command("pause") & filters.group)
async def pause_cmd(client, message: Message):
    await react(client, message, "⏸")
    try:
        await calls.pause_stream(message.chat.id)
        await message.reply_text("⏸ <b>গান পজ করা হলো।</b>")
    except Exception as e:
        LOGGER.error(e)
        await message.reply_text("❌ পজ করা যাচ্ছে না — কোনো গান বাজছে কি?")


@app.on_message(filters.command("resume") & filters.group)
async def resume_cmd(client, message: Message):
    await react(client, message, "▶️")
    try:
      await calls.resume_stream(message.chat.id)
        await message.reply_text("▶️ <b>গান আবার চালু হলো।</b>")
    except Exception as e:
        LOGGER.error(e)
        await message.reply_text("❌ Resume করা যাচ্ছে না।")


@app.on_message(filters.command(["skip", "next"]) & filters.group)
async def skip_cmd(client, message: Message):
    await react(client, message, "⏭")
    try:
        await calls.leave_call(message.chat.id)
        ACTIVE_CHATS.pop(message.chat.id, None)
        await message.reply_text("⏭ <b>গান স্কিপ করা হলো।</b>\n\nনতুন গান চালাতে <code>/play</code> দাও।")
    except Exception as e:
        LOGGER.error(e)
        await message.reply_text("❌ স্কিপ করা যাচ্ছে না।")


@app.on_message(filters.command(["stop", "end"]) & filters.group)
async def stop_cmd(client, message: Message):
    await react(client, message, "🛑")
      try:
        await calls.leave_call(message.chat.id)
        ACTIVE_CHATS.pop(message.chat.id, None)
        await message.reply_text("🛑 <b>স্ট্রিম বন্ধ করা হলো।</b>\n\nধন্যবাদ গান উপভোগ করার জন্য 💝")
    except Exception as e:
        LOGGER.error(e)
        await message.reply_text("❌ স্টপ করা যাচ্ছে না।")
