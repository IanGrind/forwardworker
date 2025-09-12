import asyncio
import logging 
import logging.config
from config import Config, temp
from database import db
from aiohttp import web
from plugins.route import routes  # Import routes from the plugin
from pyrogram import Client, __version__, idle
from pyrogram.raw.all import layer 
from pyrogram.enums import ParseMode
from pyrogram.errors import FloodWait 

logging.config.fileConfig('logging.conf')
logging.getLogger().setLevel(logging.INFO)
logging.getLogger("pyrogram").setLevel(logging.ERROR)

PORT = Config.PORT

async def web_server():
    """Initializes the web server application."""
    web_app = web.Application(client_max_size=30000000)
    web_app.add_routes(routes)
    return web_app

class Bot(Client): 
    def __init__(self):
        super().__init__(
            name=Config.BOT_SESSION,
            api_hash=Config.API_HASH,
            api_id=Config.API_ID,
            plugins={
                "root": "plugins"
            },
            bot_token=Config.BOT_TOKEN
        )
        self.log = logging

    async def start(self):
        try:
            await super().start()
        except FloodWait as e:
            self.log.warning(f"FloodWait on start: waiting for {e.value} seconds.")
            await asyncio.sleep(e.value)
            await super().start()
            
        me = await self.get_me()
        self.log.info(f"{me.first_name} with Pyrogram v{__version__} (Layer {layer}) started on @{me.username}.")
        
        temp.BANNED_USERS = await db.get_banned()

        # Start the web server here
        app = web.AppRunner(await web_server())
        await app.setup()
        bind_address = "0.0.0.0"
        await web.TCPSite(app, bind_address, PORT).start()
        self.log.info(f"Web server started on port {PORT}.")
        
        await idle()

    async def stop(self, *args):
        await super().stop()
        self.log.info("Bot has stopped.")
