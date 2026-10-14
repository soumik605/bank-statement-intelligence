"""HTTP API. Run from backend/:

    uv run uvicorn app.main:app --reload

then open http://localhost:8000/docs to try POST /analyze with a file.
"""
import logging
import os
import tempfile
import traceback
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Query, Response, UploadFile
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

from app import db
from app.extract import ExtractionError, IncorrectPasswordError, PasswordRequiredError
from app.normalize import NormalizationError
from app.pipeline import analyze_file
from app.schema import AnalyzeResponse, FailureItem, FailurePage, HistoryPage

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


def _error(status: int, error: str, message: str, failure_id: int | None = None) -> HTTPException:
    detail = {"error": error, "message": message}
    if failure_id is not None:
        detail["failure_id"] = failure_id  # the upload was kept for review under this id
    return HTTPException(status_code=status, detail=detail)


MEDIA_TYPES = {".pdf": "application/pdf", ".csv": "text/csv; charset=utf-8"}


def _serve_upload(path: Path, name: str) -> FileResponse:
    # inline: show in the browser (a PDF opens in its viewer) rather than download.
    media_type = MEDIA_TYPES.get(Path(name).suffix.lower(), "application/octet-stream")
    return FileResponse(path, media_type=media_type, filename=name, content_disposition_type="inline")


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
        422: {
            "description": "The file couldn't be parsed as a bank statement; `message` says why. "
            '`error` is "password_required" or "incorrect_password" for a locked PDF: ask for the '
            "password and send the file again with it."
        },
    },
)
def analyze(
    file: UploadFile = File(..., description="Bank statement, .csv or .pdf"),
    password: str | None = Form(None, description="Only for password-protected PDFs. Used to open the file; never stored."),
) -> AnalyzeResponse:
    # A plain def, not async: the pipeline blocks on file parsing and LLM calls,
    # so FastAPI runs it in a worker thread instead of stalling the event loop.
    name = file.filename or "upload"
    suffix = Path(name).suffix.lower()
    content = file.file.read(MAX_UPLOAD_BYTES + 1)

    def fail(status: int, code: str, message: str, exc: BaseException | None = None, keep_file: bool = True):
        """Record the failed upload (with its file, for reproducing it later), then raise."""
        failure_id = None
        try:
            failure_id = db.save_failure(
                source_file=name,
                content=content if keep_file else None,
                http_status=status,
                error_code=code,
                message=message,
                exception=exc,
                traceback="".join(traceback.format_exception(exc)) if exc else None,
                password_supplied=bool(password),
            )
        except Exception:
            log.exception("couldn't record failed upload of %s", name)
        return _error(status, code, message, failure_id)

    if suffix not in SUPPORTED_SUFFIXES:
        raise fail(415, "unsupported_file_type", f"{name!r} is not a CSV or PDF file.")
    if not content:
        raise fail(400, "empty_file", f"{name!r} is empty.", keep_file=False)
    if len(content) > MAX_UPLOAD_BYTES:
        raise fail(413, "file_too_large", f"{name!r} is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.", keep_file=False)

    # The parsers work on paths (pdfplumber needs a real file), so park the upload on disk.
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)
    try:
        result = analyze_file(tmp_path, source_file=name, password=password or None)
    # Asking for a password is the normal flow for a locked PDF, not a failure: not recorded.
    except PasswordRequiredError:
        raise _error(422, "password_required", f"{name!r} is password protected. Enter its password to continue.") from None
    except IncorrectPasswordError:
        raise _error(422, "incorrect_password", f"That password didn't open {name!r}. Check it and try again.") from None
    except (ExtractionError, NormalizationError) as e:
        raise fail(422, "could_not_parse", f"Couldn't read {name!r} as a bank statement: {e}", e) from None
    except Exception as e:
        # pdfplumber/pandas raise their own types for corrupt files.
        if _is_parse_failure(e):
            message = f"Couldn't open {name!r}; it may be damaged or not really a {suffix[1:].upper()}."
            raise fail(422, "could_not_parse", message, e) from None
        log.exception("analyze failed for %s", name)
        raise fail(500, "internal_error", "Something went wrong while analyzing the file.", e) from None
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


@app.get("/history/{analysis_id}/file", response_class=FileResponse, responses={404: {"description": "No such analysis, or its file wasn't kept"}})
def history_file(analysis_id: int) -> FileResponse:
    """The uploaded statement, exactly as uploaded (a protected PDF is still protected)."""
    found = db.get_analysis_file(analysis_id)
    if found is None:
        raise _error(404, "file_not_found", "The original file isn't available for this analysis.")
    return _serve_upload(*found)


@app.delete("/history/{analysis_id}", status_code=204, responses={404: {"description": "No such analysis"}})
def delete_history_item(analysis_id: int) -> Response:
    if not db.delete_analysis(analysis_id):
        raise _error(404, "analysis_not_found", f"No saved analysis with id {analysis_id}.")
    return Response(status_code=204)


@app.get("/failures", response_model=FailurePage)
def failures(limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)) -> FailurePage:
    """Failed uploads, newest first, with errors and tracebacks, for improving the parser."""
    return db.list_failures(limit, offset)


@app.get("/failures/{failure_id}", response_model=FailureItem, responses={404: {"description": "No such failure"}})
def failure(failure_id: int) -> FailureItem:
    found = db.get_failure(failure_id)
    if found is None:
        raise _error(404, "failure_not_found", f"No recorded failure with id {failure_id}.")
    return found


@app.get("/failures/{failure_id}/file", response_class=FileResponse, responses={404: {"description": "No such failure, or no file kept"}})
def failure_file(failure_id: int) -> FileResponse:
    found = db.get_failure_file(failure_id)
    if found is None:
        raise _error(404, "file_not_found", "No file was kept for this failure.")
    return _serve_upload(*found)


@app.delete("/failures/{failure_id}", status_code=204, responses={404: {"description": "No such failure"}})
def delete_failure(failure_id: int) -> Response:
    if not db.delete_failure(failure_id):
        raise _error(404, "failure_not_found", f"No recorded failure with id {failure_id}.")
    return Response(status_code=204)


def _is_parse_failure(e: Exception) -> bool:
    from pandas.errors import ParserError
    from pdfminer.pdfparser import PDFSyntaxError
    from pdfplumber.utils.exceptions import PdfminerException

    return isinstance(e, ParserError | PDFSyntaxError | PdfminerException | UnicodeDecodeError)
