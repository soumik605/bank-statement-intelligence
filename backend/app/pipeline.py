"""The whole pipeline for one statement file, in order:
extract -> normalize (rules, then the AI layout fallback if they can't read the
layout) -> categorize (rules, then LLM) -> unusual -> analytics -> AI summary.

Each step logs what it found in plain English, so the log reads as a story of
the upload for someone who doesn't know the code.
"""
import logging
import time
from collections import Counter
from pathlib import Path

from app import llm
from app.analytics import compute_analytics
from app.categorize import categorize
from app.extract import HeaderNotFoundError
from app.formatting import format_inr, plural
from app.layout_ai import load_with_ai_layout
from app.normalize import ColumnMappingError, load_statement
from app.schema import AISummary, AnalyzeResponse, SkippedRowOut
from app.summary import summarize
from app.unusual import detect_unusual

log = logging.getLogger(__name__)


def analyze_file(
    path: str | Path, source_file: str | None = None, password: str | None = None, model: str | None = None
) -> AnalyzeResponse:
    """Raises ExtractionError or NormalizationError when the file can't be parsed
    (PasswordRequiredError / IncorrectPasswordError for a locked PDF). LLM
    failures don't raise; those steps fall back on their own.

    Opening the file is the first step, so a locked PDF fails in well under a
    second, before any LLM call. `model` is a key from llm.MODELS (None: the
    default); an unknown key raises llm.UnknownModelError before any work."""
    chosen = llm.resolve_model(model)
    name = source_file or Path(path).name
    started = time.monotonic()
    log.info("━━ Analysing %r with the AI model %s", name, chosen.label)

    log.info("Step 1 of 5: reading the statement%s", " (opening it with the password given)" if password else "")
    try:
        statement = load_statement(path, password)
    except (HeaderNotFoundError, ColumnMappingError) as e:
        log.info("  The usual rules couldn't work out this statement's layout, so asking the AI to")
        statement = load_with_ai_layout(path, password, rules_error=e, model=chosen.key)
    txns = statement.transactions
    log.info(
        "  Found %s, %s to %s", plural(len(txns), "transaction"),
        f"{statement.statement.period_start:%d %b %Y}", f"{statement.statement.period_end:%d %b %Y}",
    )
    if statement.skipped:
        reasons = Counter(s.reason for s in statement.skipped)
        log.info(
            "  Left out %s that %s transactions, like headings and totals (%s)",
            plural(len(statement.skipped), "row"), "isn't a" if len(statement.skipped) == 1 else "aren't",
            ", ".join(f"{count} with {reason}" for reason, count in reasons.most_common()),
        )

    log.info("Step 2 of 5: sorting transactions into categories")
    categorized = categorize(txns, model=chosen.key)
    counts = Counter(t.category.value for t in categorized if t.category)
    log.info("  Categories: %s", ", ".join(f"{c} {n}" for c, n in counts.most_common()))

    log.info("Step 3 of 5: looking for unusual transactions")
    transactions = detect_unusual(categorized)
    flagged = [t for t in transactions if t.is_unusual]
    if flagged:
        log.info("  Flagged %s as unusual:", plural(len(flagged), "transaction"))
        for t in flagged:
            log.info("    • %s %s %r: %s", f"{t.date:%d %b}", format_inr(t.amount), t.description, t.unusual_reason)
    else:
        log.info("  Nothing unusual found")

    log.info("Step 4 of 5: adding up the totals")
    analytics = compute_analytics(transactions)
    log.info(
        "  Money in %s, money out %s, of which spending %s (biggest: %s)",
        format_inr(analytics.total_credits), format_inr(analytics.total_debits), format_inr(analytics.total_spending),
        ", ".join(c.value for c in analytics.top_spending_categories) or "none",
    )

    log.info("Step 5 of 5: writing the summary")
    summary = summarize(statement.statement, analytics, transactions, model=chosen.key)
    log.info("  %s", "Summary written by the AI" if summary.source == "llm" else "Used the built-in summary instead of the AI's")

    log.info("━━ Finished %r in %.1f s", name, time.monotonic() - started)
    return AnalyzeResponse(
        source_file=name,
        account=statement.statement,
        transactions=transactions,
        summary=analytics,
        unusual=flagged,
        ai_summary=AISummary(text=summary.text, source=summary.source, unsupported_numbers=summary.unsupported_numbers),
        skipped_rows=[SkippedRowOut(row=s.row, reason=s.reason) for s in statement.skipped],
        ai_model=chosen.id,
    )
