import aiosqlite
from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL,
    pw_hash TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS bots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    name TEXT NOT NULL,
    bot_token TEXT NOT NULL,
    api_id TEXT DEFAULT '',
    api_hash TEXT DEFAULT '',
    session_string TEXT DEFAULT '',
    filename TEXT NOT NULL,
    file_path TEXT NOT NULL,
    entry_file TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',   -- pending|running|stopped|rejected|error
    reject_reason TEXT DEFAULT '',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""

async def init():
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.executescript(SCHEMA)
        await db.commit()

def conn():
    return aiosqlite.connect(config.DB_PATH)