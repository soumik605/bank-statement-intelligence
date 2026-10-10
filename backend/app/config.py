import os
from dotenv import load_dotenv

load_dotenv()  # reads backend/.env

LLM_API_KEY = os.getenv("LLM_API_KEY")
LLM_MODEL = os.getenv("LLM_MODEL")
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",")

if not LLM_API_KEY:
    raise RuntimeError("LLM_API_KEY is missing. Copy .env.example to .env and fill it in.")