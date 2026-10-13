import datetime as dt
import json

import pytest

from app.analytics import compute_analytics
from app.schema import Category, StatementInfo, Transaction
from app.summary import SYSTEM_PROMPT, build_summary_input, summarize, template_summary, unsupported_numbers
from app.unusual import detect_unusual

STATEMENT = StatementInfo(account_number="XXXX3307", holder_name="Farah Siddiqui",
                          period_start=dt.date(2026, 3, 1), period_end=dt.date(2026, 3, 31))


def _txn(description, amount, txn_type, category, day=5):
    return Transaction(date=dt.date(2026, 3, day), description=description, amount=amount, type=txn_type,
                       category=category, category_source="rule")


TXNS = detect_unusual([
    _txn("SALARY", 138000, "credit", Category.SALARY, 1),
    _txn("RENT", 35000, "debit", Category.RENT, 3),
    *[_txn(f"UPI-SWIGGY-{i}", 400, "debit", Category.FOOD, 4 + i) for i in range(10)],
    _txn("AMAZON", 2464.56, "debit", Category.SHOPPING, 15),
    _txn("NEFT-SELF-SAVINGS", 40000, "debit", Category.TRANSFER, 17),
    _txn("IMPS-TRANSFER TO VIKRAM SHAH", 275000, "debit", Category.TRANSFER, 24),
])
ANALYTICS = compute_analytics(TXNS)
SOURCE = build_summary_input(STATEMENT, ANALYTICS, TXNS)

GOOD = (
    "In March 2026 ₹1,38,000 came in and ₹3,56,464.56 went out, so more went out than came in.\n"
    "Spending was ₹41,464.56, mostly Rent (₹35,000, 84%).\n"
    "One transfer of ₹2,75,000 on 24 Mar 2026 looks unusual."
)


class FakeLLM:
    def __init__(self, *replies, error=None):
        self.replies, self.error, self.calls = list(replies), error, []

    def __call__(self, messages):
        self.calls.append([dict(m) for m in messages])
        if self.error:
            raise self.error
        return self.replies.pop(0)


def test_input_has_only_computed_figures():
    assert SOURCE["total_spending"] == "₹41,464.56"
    assert SOURCE["top_spending_categories"][0] == {"category": "Rent", "amount": "₹35,000", "share_of_spending": "84%"}
    assert [t["description"] for t in SOURCE["unusual_transactions"]] == ["IMPS-TRANSFER TO VIKRAM SHAH"]
    sent = json.dumps(SOURCE)
    assert "3307" not in sent and "Farah" not in sent  # account details never leave
    assert "SWIGGY" not in sent  # individual transactions aren't sent, only flagged ones


def test_prompt_forbids_math():
    fake = FakeLLM(GOOD)
    summarize(STATEMENT, ANALYTICS, TXNS, complete=fake)
    assert "Do not calculate or invent numbers. Use only the values provided." in fake.calls[0][0]["content"]
    assert json.loads(fake.calls[0][1]["content"]) == SOURCE


def test_good_summary_is_used():
    result = summarize(STATEMENT, ANALYTICS, TXNS, complete=FakeLLM(f"```\n{GOOD}\n```"))
    assert (result.text, result.source, result.unsupported_numbers) == (GOOD, "llm", [])


def test_invented_number_triggers_one_retry():
    fake = FakeLLM("Spending was about ₹41,500, roughly 85% on rent.", GOOD)
    result = summarize(STATEMENT, ANALYTICS, TXNS, complete=fake)
    assert result.source == "llm" and len(fake.calls) == 2
    assert "41,500, 85" in fake.calls[1][-1]["content"]


def test_falls_back_to_template_when_still_wrong():
    fake = FakeLLM("You spent ₹1.4 lakh.", "Again, ₹1.4 lakh.")
    result = summarize(STATEMENT, ANALYTICS, TXNS, complete=fake)
    assert result.source == "template" and result.unsupported_numbers == ["1.4"]
    assert result.text == template_summary(SOURCE)


@pytest.mark.parametrize("fake", [FakeLLM(error=RuntimeError("HTTP 401")), FakeLLM("")])
def test_falls_back_on_failure_or_empty_reply(fake):
    assert summarize(STATEMENT, ANALYTICS, TXNS, complete=fake).source == "template"


def test_template_passes_its_own_check():
    text = template_summary(SOURCE)
    assert unsupported_numbers(text, SOURCE) == []
    assert 4 <= len(text.splitlines()) <= 6


@pytest.mark.parametrize("summary, bad", [
    ("Rent was ₹35,000.", []),
    ("Rent was 35000 rupees, 84% of spending.", []),  # same values, different formatting
    ("Net outflow of ₹2,18,464.56 on 01 Mar.", []),  # sign and leading zero don't matter
    ("Rent was ₹35,000, then shopping.", []),  # trailing comma
    ("Rent was about ₹35k.", ["35"]),
    ("Spending rose 37% over last month.", ["37"]),
])
def test_unsupported_numbers(summary, bad):
    assert unsupported_numbers(summary, SOURCE) == bad
