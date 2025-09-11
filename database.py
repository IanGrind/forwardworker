import motor.motor_asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from config import Config, temp
from os import environ

DB_NAME = Config.DB_NAME
DB_URL = Config.DB_URL

db = None

def initialize_database():
    global db
    if db is None:
        db = Database(DB_URL, DB_NAME)

async def mongodb_version():
    client = AsyncIOMotorClient(Config.DB_URL)
    server_info = await client.server_info()
    return server_info['version']

class Database:

    def __init__(self, uri, database_name):
        self._client = motor.motor_asyncio.AsyncIOMotorClient(uri)
        self.db = self._client[database_name]
        self.bot = self.db.bots
        self.col = self.db.user
        self.chl = self.db.channels
        self.worker = self.db.workers
        self.manager = self.db.manager_userbot

    def new_user(self, id, name):
        return dict(
            id = id,
            name = name,
            ban_status=dict(
                is_banned=False,
                ban_reason="",
            ),
        )

    async def add_user(self, id, name):
        user = self.new_user(id, name)
        await self.col.insert_one(user)

    async def is_user_exist(self, id):
        user = await self.col.find_one({'id':int(id)})
        return bool(user)

    async def total_users_bots_count(self):
        bcount = await self.bot.count_documents({})
        count = await self.col.count_documents({})
        return count, bcount

    async def total_channels(self):
        return await self.chl.count_documents({})

    async def remove_ban(self, id):
        ban_status = dict(is_banned=False, ban_reason='')
        await self.col.update_one({'id': id}, {'$set': {'ban_status': ban_status}})

    async def ban_user(self, user_id, ban_reason="No Reason"):
        ban_status = dict(is_banned=True, ban_reason=ban_reason)
        await self.col.update_one({'id': user_id}, {'$set': {'ban_status': ban_status}})

    async def get_ban_status(self, id):
        default = dict(is_banned=False, ban_reason='')
        user = await self.col.find_one({'id':int(id)})
        return user.get('ban_status', default) if user else default

    async def get_all_users(self):
        return self.col.find({})

    async def delete_user(self, user_id):
        await self.col.delete_many({'id': int(user_id)})
        
    async def reset_user_data(self, user_id):
        user_doc = await self.col.find_one_and_delete({'id': int(user_id)})
        await self.bot.delete_many({'user_id': int(user_id)})
        await self.chl.delete_many({'user_id': int(user_id)})
        if user_doc:
             await self.add_user(user_id, user_doc.get('name', str(user_id)))

    async def get_banned(self):
        users = self.col.find({'ban_status.is_banned': True})
        b_users = [user['id'] async for user in users]
        temp.BANNED_USERS = b_users
        return b_users

    async def update_configs(self, id, configs):
        await self.col.update_one({'id': int(id)}, {'$set': {'configs': configs}})

    async def get_configs(self, id):
        default = {
            'caption': None, 'duplicate': True, 'forward_tag': False,
            'file_size': 0, 'size_limit': None, 'extension': None,
            'keywords': None, 'protect': None, 'button': None,
            'db_uri': None, 'forward_delay': 1.0,
            'filters': {'poll': True, 'text': True, 'audio': True, 'voice': True, 'video': True, 'photo': True, 'document': True, 'animation': True, 'sticker': True}
        }
        user = await self.col.find_one({'id':int(id)})
        if user:
            user_configs = user.get('configs', {})
            final_configs = default.copy()
            final_configs.update(user_configs)
            if 'filters' in user_configs:
                final_configs['filters'] = default['filters'].copy()
                final_configs['filters'].update(user_configs['filters'])
            return final_configs
        return default

    async def add_bot(self, datas):
       await self.bot.insert_one(datas)

    async def remove_bot(self, user_id, bot_id):
       await self.bot.delete_one({'user_id': int(user_id), 'id': int(bot_id)})
       await self.manager.delete_one({'user_id': int(user_id), 'id': int(bot_id)})

    async def get_bot(self, user_id: int, bot_id: int):
       return await self.bot.find_one({'user_id': user_id, 'id': bot_id})

    async def get_bots(self, user_id: int):
        return [bot async for bot in self.bot.find({'user_id': user_id})]

    async def is_bot_exist(self, user_id, bot_id):
       return bool(await self.bot.find_one({'user_id': user_id, 'id': bot_id}))

    async def in_channel(self, user_id: int, chat_id: int) -> bool:
       return bool(await self.chl.find_one({"user_id": int(user_id), "chat_id": int(chat_id)}))

    async def add_channel(self, user_id: int, chat_id: int, title, username):
       if await self.in_channel(user_id, chat_id): return False
       return await self.chl.insert_one({"user_id": user_id, "chat_id": chat_id, "title": title, "username": username})

    async def remove_channel(self, user_id: int, chat_id: int):
       if not await self.in_channel(user_id, chat_id ): return False
       return await self.chl.delete_many({"user_id": int(user_id), "chat_id": int(chat_id)})

    async def get_channel_details(self, user_id: int, chat_id: int):
       return await self.chl.find_one({"user_id": int(user_id), "chat_id": int(chat_id)})

    async def get_user_channels(self, user_id: int):
       return [channel async for channel in self.chl.find({"user_id": int(user_id)})]

    async def add_worker_bot(self, datas):
        await self.worker.insert_one(datas)

    async def remove_worker_bot(self, user_id, bot_id):
        await self.worker.delete_one({'user_id': int(user_id), 'id': int(bot_id)})

    async def get_worker_bots(self, user_id: int):
        return [worker async for worker in self.worker.find({'user_id': user_id})]

    async def is_worker_bot_exist(self, user_id, bot_id):
        return bool(await self.worker.find_one({'user_id': user_id, 'id': bot_id}))

    async def set_manager_userbot(self, user_id, bot_id):
        await self.manager.delete_many({'user_id': user_id})
        userbot = await self.bot.find_one({'user_id': user_id, 'id': bot_id, 'is_bot': False})
        if userbot:
            await self.manager.insert_one(userbot)

    async def get_manager_userbot(self, user_id):
        return await self.manager.find_one({'user_id': user_id})
