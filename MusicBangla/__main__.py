import os
import asyncio
from pyrogram import idle
from MusicBangla import app, assistant, calls, LOGGER
import config

# Heroku Config Var থেকে YouTube cookies লোড করার কোড
_yt_cookies = os.environ.get("YT_COOKIES")
if _yt_cookies:
    with open("cookies.txt", "w") as _f:
        _f.write(_yt_cookies)
    LOGGER.info("✅ YouTube cookies loaded")


async def main():
    LOGGER.info("🎶 MusicBangla বট চালু হচ্ছে...")

    # ✅ Step 1: মূল বট শুরু করুন
    try:
        await app.start()
        bot_info = await app.get_me()
        LOGGER.info(f"✅ বট চালু: @{bot_info.username} (ID: {bot_info.id})")
    except Exception as e:
        LOGGER.error(f"❌ বট চালু ব্যর্থ: {e}")
        return

    # ✅ Step 2: Assistant userbot শুরু করুন (Voice Chat এর জন্য)
    try:
        await assistant.start()
        me = await assistant.get_me()
        LOGGER.info(f"✅ Assistant চালু: {me.first_name} (@{me.username}, ID: {me.id})")
    except Exception as e:
        LOGGER.error(f"❌ Assistant চালু ব্যর্থ: {e}")
        LOGGER.error("🔧 সমাধান: STRING_SESSION সঠিক কিনা চেক করুন (my.telegram.org থেকে জেনারেট করুন)")
        return

    # ✅ Step 3: PyTgCalls (Voice Chat Handler) শুরু করুন
    try:
        await calls.start()
        LOGGER.info("✅ Voice Chat handler চালু (PyTgCalls)")
    except Exception as e:
        LOGGER.error(f"❌ Voice Chat handler চালু ব্যর্থ: {e}")
        LOGGER.warning("⚠️ Node.js নেই অথবা buildpack সেট করা নেই।")
        LOGGER.warning("🔧 Heroku-তে করুন: heroku buildpacks:add --index 1 heroku/nodejs")
        LOGGER.warning("🔧 আমি বিনা Voice Chat এ চেষ্টা করছি...")

    # ✅ Step 4: Plugins লোড করুন (সব কমান্ড হ্যান্ডলার)
    try:
        import MusicBangla.plugins  # noqa
        LOGGER.info("✅ সব Plugins লোড হয়েছে")
    except Exception as e:
        LOGGER.error(f"❌ Plugins লোড ব্যর্থ: {e}")

    # ✅ Step 5: Log group-এ startup message পাঠান
    try:
        startup_text = (
            f"🎉 <b>MusicBangla সফলভাবে চালু হয়েছে!</b>\n\n"
            f"🤖 <b>বট:</b> @{bot_info.username} (ID: <code>{bot_info.id}</code>)\n"
            f"👤 <b>Assistant:</b> @{me.username} (ID: <code>{me.id}</code>)\n"
            f"📡 <b>স্ট্যাটাস:</b> <b style='color: green'>অনলাইন ✅</b>\n"
            f"🕐 <b>সময়:</b> <code>{asyncio.get_event_loop().time()}</code>\n"
            f"👨‍💻 <b>মালিক:</b> @{config.OWNER_USERNAME}\n\n"
            f"<b>Features:</b>\n"
            f"✓ Music Streaming\n"
            f"✓ Video Streaming\n"
            f"✓ Games\n"
            f"✓ Smart Tagging\n"
            f"✓ Auto-welcome"
        )
        await app.send_message(chat_id=config.LOG_GROUP_ID, text=startup_text)
        LOGGER.info(f"✅ Startup message পাঠানো হয়েছে Log group-এ ({config.LOG_GROUP_ID})")
    except Exception as e:
        LOGGER.warning(f"⚠️ Log message পাঠানো গেল না: {e}")
        LOGGER.warning("🔧 নিশ্চিত করুন: বটকে log group-এ admin করেছেন")

    # ✅ Step 6: সম্পূর্ণ প্রস্তুত বার্তা
    LOGGER.info("=" * 60)
    LOGGER.info("🚀 MusicBangla সম্পূর্ণ প্রস্তুত!")
    LOGGER.info("=" * 60)
    LOGGER.info(f"📊 বট Username: @{bot_info.username}")
    LOGGER.info(f"👤 Assistant: @{me.username}")
    LOGGER.info(f"🎵 গান প্লে করতে: /play গানের নাম")
    LOGGER.info(f"🎬 ভিডিও প্লে করতে: /vplay গানের নাম")
    LOGGER.info(f"🎮 গেম খেলতে: /games")
    LOGGER.info("=" * 60)

    # ✅ Step 7: বট চলমান রাখুন (Idle mode)
    await idle()

    # ✅ Step 8: বট বন্ধ হওয়ার সময় cleanup
    LOGGER.info("🛑 বট বন্ধ হচ্ছে...")
    try:
        await app.send_message(
            chat_id=config.LOG_GROUP_ID,
            text="🛑 <b>MusicBangla বন্ধ হয়েছে</b>"
        )
    except Exception:
        pass
    await app.stop()
    await assistant.stop()
    LOGGER.info("✅ বট সম্পূর্ণভাবে বন্ধ হয়েছে")


if __name__ == "__main__":
    try:
        asyncio.get_event_loop().run_until_complete(main())
    except KeyboardInterrupt:
        LOGGER.info("⏹️ ইউজার দ্বারা বন্ধ করা হয়েছে")
    except Exception as e:
        LOGGER.error(f"❌ মূল ত্রুটি: {e}")
        import traceback
        traceback.print_exc()
