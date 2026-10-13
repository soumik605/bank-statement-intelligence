"""The whole pipeline for one statement file, in order:
extract -> normalize -> categorize (rules, then LLM) -> unusual -> analytics -> AI summary.
"""
from pathlib import Path

from app.analytics import compute_analytics
from app.categorize import categorize
from app.normalize import load_statement
from app.schema import AISummary, AnalyzeResponse, SkippedRowOut
from app.summary import summarize
from app.unusual import detect_unusual


def analyze_file(path: str | Path, source_file: str | None = None) -> AnalyzeResponse:
    """Raises ExtractionError or NormalizationError when the file can't be parsed.
    LLM failures don't raise; those steps fall back on their own."""
    statement = load_statement(path)
    transactions = detect_unusual(categorize(statement.transactions))
    analytics = compute_analytics(transactions)
    summary = summarize(statement.statement, analytics, transactions)
    return AnalyzeResponse(
        source_file=source_file or Path(path).name,
        account=statement.statement,
        transactions=transactions,
        summary=analytics,
        unusual=[t for t in transactions if t.is_unusual],
        ai_summary=AISummary(text=summary.text, source=summary.source, unsupported_numbers=summary.unsupported_numbers),
        skipped_rows=[SkippedRowOut(row=s.row, reason=s.reason) for s in statement.skipped],
    )
