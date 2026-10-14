"""HTTP API. Run from backend/:

    uv run uvicorn app.main:app --reload

then open http://localhost:8000/docs to try POST /analyze with a file.
"""
import logging
import os
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Query, Response, UploadFile
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

from app import db
from app.extract import ExtractionError
from app.normalize import NormalizationError
from app.pipeline import analyze_file
from app.schema import AnalyzeResponse, HistoryPage

log = logging.getLogger(__name__)

SUPPORTED_SUFFIXES = {".csv", ".pdf"}
SAMPLES_DIR = Path(__file__).resolve().parents[2] / "data" / "samples"
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

app = FastAPI(
    title="Bank Statement Intelligence",
    description="Upload a bank statement (CSV or PDF) and get back clean transactions, categories, "
    "totals, unusual items and a plain-English summary.",
)

# Without this the browser blocks the Next.js app's requests (a different origin)
# before they reach us. Comma-separated; defaults to the Next.js dev server.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in os.environ.get("CORS_ORIGINS", "http://localhost:3000").split(",") if o.strip()],
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)


def _error(status: int, error: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"error": error, "message": message})


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/samples")
def list_samples() -> list[str]:
    """Synthetic statements anyone can try; see scripts/generate_samples.py."""
    return sorted(p.name for p in SAMPLES_DIR.glob("*") if p.suffix in SUPPORTED_SUFFIXES)


@app.get("/samples/{name}", response_class=FileResponse, responses={404: {"description": "No such sample"}})
def get_sample(name: str) -> FileResponse:
    # Only names from the listing, so a path like "../../.env" can never be served.
    if name not in list_samples():
        raise _error(404, "sample_not_found", f"No sample called {name!r}.")
    return FileResponse(SAMPLES_DIR / name, filename=name)


@app.post(
    "/analyze",
    response_model=AnalyzeResponse,
    responses={
        400: {"description": "Empty file"},
        413: {"description": "File larger than 10 MB"},
        415: {"description": "Not a CSV or PDF"},
        422: {"description": "The file couldn't be parsed as a bank statement; `message` says why"},
    },
)
def analyze(file: UploadFile = File(..., description="Bank statement, .csv or .pdf")) -> AnalyzeResponse:
    # A plain def, not async: the pipeline blocks on file parsing and LLM calls,
    # so FastAPI runs it in a worker thread instead of stalling the event loop.
    name = file.filename or "upload"
    suffix = Path(name).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise _error(415, "unsupported_file_type", f"{name!r} is not a CSV or PDF file.")

    content = file.file.read(MAX_UPLOAD_BYTES + 1)
    if not content:
        raise _error(400, "empty_file", f"{name!r} is empty.")
    if len(content) > MAX_UPLOAD_BYTES:
        raise _error(413, "file_too_large", f"{name!r} is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")

    # The parsers work on paths (pdfplumber needs a real file), so park the upload on disk.
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)
    try:
        result = analyze_file(tmp_path, source_file=name)
    except (ExtractionError, NormalizationError) as e:
        raise _error(422, "could_not_parse", f"Couldn't read {name!r} as a bank statement: {e}") from None
    except Exception as e:
        # pdfplumber/pandas raise their own types for corrupt files.
        if _is_parse_failure(e):
            raise _error(422, "could_not_parse", f"Couldn't open {name!r}; it may be damaged or not really a {suffix[1:].upper()}.") from None
        log.exception("analyze failed for %s", name)
        raise _error(500, "internal_error", "Something went wrong while analyzing the file.") from None
    finally:
        tmp_path.unlink(missing_ok=True)

    try:
        return db.save_analysis(result, content)
    except Exception:
        # The analysis worked; don't throw it away because the history couldn't be written.
        log.exception("couldn't save analysis of %s to history", name)
        return result


@app.get("/history", response_model=HistoryPage)
def history(limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)) -> HistoryPage:
    """Past analyses, newest first."""
    return db.list_analyses(limit, offset)


@app.get("/history/{analysis_id}", response_model=AnalyzeResponse, responses={404: {"description": "No such analysis"}})
def history_item(analysis_id: int) -> AnalyzeResponse:
    """A past analysis exactly as it was returned; nothing is re-run."""
    result = db.get_analysis(analysis_id)
    if result is None:
        raise _error(404, "analysis_not_found", f"No saved analysis with id {analysis_id}.")
    return result


@app.delete("/history/{analysis_id}", status_code=204, responses={404: {"description": "No such analysis"}})
def delete_history_item(analysis_id: int) -> Response:
    if not db.delete_analysis(analysis_id):
        raise _error(404, "analysis_not_found", f"No saved analysis with id {analysis_id}.")
    return Response(status_code=204)


def _is_parse_failure(e: Exception) -> bool:
    from pandas.errors import ParserError
    from pdfminer.pdfparser import PDFSyntaxError
    from pdfplumber.utils.exceptions import PdfminerException

    return isinstance(e, ParserError | PDFSyntaxError | PdfminerException | UnicodeDecodeError)
