# STRING_SESSION জেনারেট করার স্ক্রিপ্ট
# এটি local machine-এ রান করুন (Heroku নয়!)

from pyrogram import Client
import asyncio

async def generate_session():
    """নতুন STRING_SESSION জেনারেট করুন"""
    
    # আপনার API_ID এবং API_HASH দিন (my.telegram.org থেকে)
    API_ID = "আপনার_API_ID"  # উদাহরণ: 30137409
    API_HASH = "আপনার_API_HASH"  # উদাহরণ: 3336d0f8c9de7cd33b55c655032fa7b3
    
    client = Client(
        name="MusicBanglaAssistant",
        api_id=API_ID,
        api_hash=API_HASH,
    )
    
    async with client:
        print("✅ অ্যাকাউন্টে লগইন হয়েছে")
        session_string = await client.export_session_string()
        print(f"\n🔐 আপনার STRING_SESSION:\n{session_string}\n")
        print("💾 এটি কপি করুন এবং Heroku Config Vars-এ পেস্ট করুন!")

if __name__ == "__main__":
    asyncio.run(generate_session())
