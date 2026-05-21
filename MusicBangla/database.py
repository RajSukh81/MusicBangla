from motor.motor_asyncio import AsyncIOMotorClient
import config

mongo = AsyncIOMotorClient(config.MONGO_DB_URI)
db = mongo.MusicBangla

chatsdb = db.chats
usersdb = db.users


async def add_user(user_id: int):
    await usersdb.update_one({"_id": user_id}, {"$set": {"_id": user_id}}, upsert=True)


async def add_chat(chat_id: int):
    await chatsdb.update_one({"_id": chat_id}, {"$set": {"_id": chat_id}}, upsert=True)


async def stats():
    users = await usersdb.count_documents({})
    chats = await chatsdb.count_documents({})
    return users, chats
