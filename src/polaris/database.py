import aiosqlite
from .config import init_config_db, CONFIG_DB_PATH

class Database:
    def __init__(self, db_path="tickets.db"):
        self.db_path = db_path

    async def setup(self):
        # Ensure config tables exist in the config DB
        init_config_db(CONFIG_DB_PATH)

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS tickets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    description TEXT,
                    status TEXT
                )
            """)
            await db.commit()
