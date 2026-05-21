import asyncio
from pyrogram import idle
from MusicBangla import app, assistant, calls, LOGGER
import config


async def main():
    LOGGER.info("🎶 MusicBangla বট চালু হচ্ছে...")

    # মূল বট
    await app.start()
    bot_info = await app.get_me()
    LOGGER.info(f"✅ বট চালু: @{bot_info.username}")

    # Assistant
    try:
        await assistant.start()
        me = await assistant.get_me()
        LOGGER.info(f"✅ Assistant চালু: {me.first_name} (@{me.username})")
    except Exception as e:
        LOGGER.error(f"❌ Assistant চালু হলো না: {e}")
        LOGGER.error("STRING_SESSION ঠিক করে আবার deploy করুন!")
        return

  # py-tgcalls
    await calls.start()
    LOGGER.info("✅ Voice Chat handler চালু")

    # Plugins লোড
    import MusicBangla.plugins  # noqa

    # Log group-এ startup message
    try:
        await app.send_message(
            chat_id=config.LOG_GROUP_ID,
            text=(
                f"🎉 <b>বট সফলভাবে চালু হয়েছে!</b>\n\n"
                f"🤖 <b>বট:</b> @{bot_info.username}\n"
                f"👤 <b>Assistant:</b> @{me.username}\n"
                f"📡 <b>স্ট্যাটাস:</b> অনলাইন ✅\n\n"
                f"মালিক: @{config.OWNER_USERNAME}"
            ),
        )
    except Exception as e:
        LOGGER.warning(f"Log group-এ মেসেজ পাঠানো গেল না: {e}")
        LOGGER.warning("বটকে log group-এ admin করতে ভুলবেন না!")

    LOGGER.info("🚀 বট সম্পূর্ণ প্রস্তুত! গান উপভোগ করুন।")
    await idle()
    await app.stop()
    await assistant.stop()
    LOGGER.info("বট বন্ধ হলো।")

if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(main())
