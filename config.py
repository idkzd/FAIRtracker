import os
from dotenv import load_dotenv

load_dotenv()

TG_BOT_TOKEN: str = os.getenv("TG_BOT_TOKEN", "")
TG_CHAT_ID: str = os.getenv("TG_CHAT_ID", "")
DIVERGENCE_THRESHOLD: float = float(os.getenv("DIVERGENCE_THRESHOLD", "4.0"))
SCAN_INTERVAL: float = float(os.getenv("SCAN_INTERVAL", "5"))
