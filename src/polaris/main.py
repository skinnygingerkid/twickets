# main.py
import os
import discord
from discord.ext import commands
from discord import app_commands
from .config import get_guild, load_config, load_config_from_db, get_token
from .ticket_cog import TicketCog

_config_source = os.getenv("CONFIG_SOURCE", "db").lower()
if _config_source == "db":
    config = load_config_from_db()
    print("[main] Config loaded from SQLite database")
else:
    config = load_config()
    print("[main] Config loaded from config.yml")

class Client(commands.Bot):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.synced = False  # Prevent multiple syncs

    async def setup_hook(self):
        await self.add_cog(TicketCog(self, config))
        print(f"Cogs loaded: {list(self.cogs.keys())}")
        print(f"Commands in tree after loading cogs: {len(self.tree.get_commands())}")
        for cmd in self.tree.get_commands():
            print(f"   - {cmd.name}: {cmd.description}")
        print("=== SETUP HOOK COMPLETED (No sync yet) ===")

    async def on_ready(self):
        print(f'Logged in as {self.user}')
        print(f'Application ID: {self.application_id}')
        print(f'Serving {len(self.guilds)} guild(s)')
        if not self.synced:
            try:
                guild = discord.Object(id=get_guild())  # Replace with your guild ID
                commands_to_sync = self.tree.get_commands()
                print(f"About to sync {len(commands_to_sync)} commands:")
                for cmd in commands_to_sync:
                    print(f"   - {cmd.name}: {cmd.description}")
                if len(commands_to_sync) == 0:
                    print("NO COMMANDS TO SYNC!")
                    print(f"Loaded cogs: {list(self.cogs.keys())}")
                    return
                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
                print(f'Successfully synced {len(synced)} slash commands')
                for command in synced:
                    print(f"   - /{command.name}: {command.description}")
                self.synced = True
            except Exception as e:
                print(f'Error syncing commands: {e}')

intents = discord.Intents.default()
intents.message_content = True
client = Client(command_prefix='!', intents=intents)
client.run(get_token())