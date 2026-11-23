import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = os.getenv("SECRET_KEY", "dev-insecure-secret")
ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASS = os.getenv("ADMIN_PASS", "admin")
DB_PATH = str(BASE_DIR / os.getenv("DB_PATH", "panel.db"))
UPLOAD_DIR = BASE_DIR / os.getenv("UPLOAD_DIR", "uploads")
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "25"))
LOG_LINES = int(os.getenv("BOT_LOG_LINES", "400"))

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)