"""Print the safe API-key preview and SEC user agent from .env."""

from dotenv import load_dotenv
import os

load_dotenv()

api_key = os.environ.get("API_KEY", "")
user_agent = os.environ.get("SEC_USER_AGENT", "")

print(f"{api_key[:4]}...")
print(user_agent)
