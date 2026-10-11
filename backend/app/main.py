# backend/app/main.py
import os

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.schema import PipelinePayload

load_dotenv()

# Comma-separated list, e.g. "http://localhost:3000,https://app.example.com"
ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",")
    if origin.strip()
]

app = FastAPI(title="Bank Statement Intelligence")
app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS,
                   allow_methods=["*"], allow_headers=["*"])

@app.get("/health")
def health():
    return {"status": "ok", "version": "0.1.0"}

@app.get("/api/v1/schema")
def payload_schema():
    """JSON Schema of PipelinePayload as the API returns it."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        **PipelinePayload.model_json_schema(mode="serialization"),
    }

