"""Stage 6: a short plain-English summary, written by the LLM from computed numbers only.

The model never sees transactions to add up. It gets only the finished
figures as JSON: totals, top categories, flagged items. It is told not to
calculate. Then every number in its reply is checked against that JSON:
- if any number isn't there, it's asked once to rewrite;
- if that still fails, or the call fails, a template summary built from the
  same figures is used instead.
"""
import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Literal

from app import llm
from app.formatting import format_inr
from app.schema import Analytics, StatementInfo, Transaction

log = logging.getLogger(__name__)

Complete = Callable[[list[dict]], str]

MAX_FLAGGED_ITEMS = 10  # keeps the prompt small; unusual_count still gives the full number

SYSTEM_PROMPT = (
    "You write a short summary of a bank statement for the account holder.\n"
    "Write 4 to 6 lines of plain text: no headings, no bullet points, no markdown.\n"
    "Do not calculate or invent numbers. Use only the values provided.\n"
    "Copy amounts and percentages exactly as written in the input, e.g. ₹1,05,000. "
    "Never round, total, subtract, or work out new percentages.\n"
    "total_money_out is everything that left the account, including transfers; only "
    "total_spending is spending. Don't call total_money_out spending.\n"
    "Say where the money went (the top spending categories) and mention any unusual "
    "transactions with their reason."
)


@dataclass
class Summary:
    text: str
    source: Literal["llm", "template"]
    # Numbers from the last LLM attempt that weren't in the input; empty if it passed.
    unsupported_numbers: list[str] = field(default_factory=list)


def build_summary_input(statement: StatementInfo, analytics: Analytics, transactions: list[Transaction]) -> dict:
    """The only data the model sees: finished figures, already formatted for display.
    No account number or holder name; the summary doesn't need them."""
    spending = Decimal(str(analytics.total_spending))
    flagged = [t for t in transactions if t.is_unusual]
    return {
        "period": f"{statement.period_start:%d %b %Y} to {statement.period_end:%d %b %Y}",
        "transaction_count": analytics.transaction_count,
        "total_money_in": format_inr(analytics.total_credits),
        "total_money_out": format_inr(analytics.total_debits),
        "net_flow": format_inr(analytics.net_flow),
        "net_flow_direction": "more came in than went out" if analytics.net_flow >= 0 else "more went out than came in",
        "total_spending": format_inr(analytics.total_spending),
        "spending_excludes": "transfers to people and own accounts",
        "top_spending_categories": [
            {
                "category": c.value,
                "amount": format_inr(analytics.spending_by_category[c]),
                "share_of_spending": _percent(Decimal(str(analytics.spending_by_category[c])), spending),
            }
            for c in analytics.top_spending_categories
        ],
        "unusual_count": len(flagged),
        "unusual_transactions": [
            {
                "date": f"{t.date:%d %b %Y}",
                "description": t.description,
                "amount": format_inr(t.amount),
                "direction": "money out" if t.type == "debit" else "money in",
                "reason": t.unusual_reason,
            }
            for t in flagged[:MAX_FLAGGED_ITEMS]
        ],
    }


def _percent(part: Decimal, whole: Decimal) -> str:
    if not whole:
        return "0%"
    return f"{(part * 100 / whole).quantize(Decimal(1), rounding=ROUND_HALF_UP)}%"


# --- Number check -------------------------------------------------------------

_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _numbers(text: str) -> list[tuple[str, Decimal]]:
    found = []
    for raw in _NUMBER.findall(text):
        raw = raw.rstrip(",")  # "₹5,000, and" -> "5,000"
        try:
            found.append((raw, Decimal(raw.replace(",", ""))))
        except InvalidOperation:
            continue
    return found


def unsupported_numbers(summary: str, source: dict) -> list[str]:
    """Numbers in the summary that don't appear anywhere in the source JSON.

    Compared by value, so 1,05,000 matches 105000 and 01 matches 1. Signs are
    ignored ("net outflow of ₹43,103.05" is fine). Numbers written as words
    ("three") aren't caught.
    """
    allowed = {value for _, value in _numbers(json.dumps(source, ensure_ascii=False))}
    return [raw for raw, value in _numbers(summary) if value not in allowed]


# --- Summary ------------------------------------------------------------------

def summarize(
    statement: StatementInfo,
    analytics: Analytics,
    transactions: list[Transaction],
    complete: Complete | None = None,
) -> Summary:
    source = build_summary_input(statement, analytics, transactions)
    complete = complete or (lambda messages: llm.chat(messages, temperature=0, max_tokens=400))
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(source, ensure_ascii=False, indent=2)},
    ]

    bad: list[str] = []
    for attempt in range(2):
        try:
            text = _clean(complete(messages))
        except Exception as e:  # network, auth, rate limit
            log.warning("LLM summary failed, using template: %s", e)
            return Summary(template_summary(source), "template")
        if not text:
            break
        bad = unsupported_numbers(text, source)
        if not bad:
            return Summary(text, "llm")
        log.warning("LLM summary attempt %d used numbers not in the input: %s", attempt + 1, bad)
        messages += [
            {"role": "assistant", "content": text},
            {"role": "user", "content": (
                f"These numbers are not in the input: {', '.join(bad)}. Rewrite the summary using only "
                "numbers that appear in the input, copied exactly. Do not calculate anything."
            )},
        ]
    return Summary(template_summary(source), "template", bad)


def _clean(text: str) -> str:
    text = re.sub(r"```\w*", "", text or "")
    lines = [line.strip() for line in text.strip().splitlines()]
    return "\n".join(line for line in lines if line)


def template_summary(source: dict) -> str:
    """A plain summary built from the same figures; used when the LLM's can't be trusted."""
    lines = [
        f"From {source['period']} there were {source['transaction_count']} transactions: "
        f"{source['total_money_in']} came in and {source['total_money_out']} went out.",
        f"Net flow was {source['net_flow']} ({source['net_flow_direction']}).",
    ]
    top = source["top_spending_categories"]
    if top:
        parts = [f"{c['category']} ({c['amount']}, {c['share_of_spending']})" for c in top]
        listed = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
        lines.append(f"Spending, excluding {source['spending_excludes']}, was {source['total_spending']}, "
                     f"mostly on {listed}.")
    items = source["unusual_transactions"]
    if not items:
        lines.append("No transactions look unusual.")
    else:
        # At most two listed, to stay within 6 lines. "including" rather than "and N more",
        # because N would be a number the template computed itself.
        lead = "including" if source["unusual_count"] > 2 else "namely"
        lines.append(f"{source['unusual_count']} transaction(s) look unusual, {lead}:")
        lines += [f"{t['description']} on {t['date']} ({t['amount']} {t['direction']}): {t['reason']}."
                  for t in items[:2]]
    return "\n".join(lines)
