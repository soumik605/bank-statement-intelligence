# backend/app/routes.py
import os
from pathlib import Path

from fastapi import APIRouter

from app.schema import PipelinePayload

router = APIRouter(prefix="/api/v1")

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_SUFFIXES = {".csv", ".pdf"}


def samples_dir() -> Path:
    """SAMPLES_DIR env var if set (e.g. when deployed without the repo layout), else <repo>/sample_statements."""
    return Path(os.getenv("SAMPLES_DIR") or REPO_ROOT / "sample_statements")


@router.get("/schema")
def payload_schema():
    """JSON Schema of PipelinePayload as the API returns it."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        **PipelinePayload.model_json_schema(mode="serialization"),
    }


@router.get("/samples")
def list_samples() -> list[str]:
    """Sample statement files (CSV/PDF) the UI can offer as one-click demos."""
    directory = samples_dir()
    if not directory.is_dir():
        return []
    return sorted(p.name for p in directory.iterdir() if p.is_file() and p.suffix.lower() in SAMPLE_SUFFIXES)
