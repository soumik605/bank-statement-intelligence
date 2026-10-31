"""The AI layout fallback: used only when the rules can't read the layout,
shown only a few lines, and trusted only when the result adds up."""
import datetime as dt
import json

import pytest
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app import llm
from app.extract import HeaderNotFoundError, LayoutLine, layout_lines
from app.layout_ai import LayoutError, check, load_with_ai_layout, pick_lines
from app.normalize import ColumnMappingError, NormalizedStatement, load_statement
from app.pipeline import analyze_file
from app.schema import StatementInfo, Transaction

# Headings no rule knows, under a preamble with personal details.
ODD_CSV = (
    "Customer,Rohan Kulkarni,Branch Pune\n"
    "Account Number,50100123451234,Savings\n"
    "When,What,Out,In,Left\n"
    "01/05/2026,UPI/SWIGGY/604118227731,250.00,,9750.00\n"
    "01/06/2026,NEFT SALARY ACME,,5000.00,14750.00\n"
    "01/19/2026,ATM CASH 123456789012,1000.00,,13750.00\n"  # 19 proves month first
)
GOOD = {
    "columns": {"date": "When", "description": "What", "debit": "Out", "credit": "In", "balance": "Left"},
    "date_order": "month-first",
}


class FakeAI:
    def __init__(self, answer):
        self.answer, self.calls = answer, []

    def __call__(self, messages):
        self.calls.append(messages)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer if isinstance(self.answer, str) else json.dumps(self.answer)


@pytest.fixture
def odd_csv(tmp_path):
    path = tmp_path / "odd.csv"
    path.write_text(ODD_CSV)
    return path


def _answer(path, header_text, **changes):
    [header] = [line for line in layout_lines(path) if " ".join(c for c in line.cells if c) == header_text]
    return {**GOOD, "header_line": header.number, **changes}


def _load(path, ai):
    with pytest.raises(HeaderNotFoundError) as rules:
        load_statement(path)
    return load_with_ai_layout(path, None, rules_error=rules.value, complete=ai)


def test_reads_the_statement_with_the_ais_layout(odd_csv):
    statement = _load(odd_csv, FakeAI(_answer(odd_csv, "When What Out In Left")))
    assert [(t.date, t.type, t.amount, t.balance) for t in statement.transactions] == [
        (dt.date(2026, 1, 5), "debit", 250.0, 9750.0),  # month first, as the AI said
        (dt.date(2026, 1, 6), "credit", 5000.0, 14750.0),
        (dt.date(2026, 1, 19), "debit", 1000.0, 13750.0),
    ]


def test_the_ai_sees_only_headings_and_a_few_rows(odd_csv):
    ai = FakeAI(_answer(odd_csv, "When What Out In Left"))
    _load(odd_csv, ai)
    sent = ai.calls[0][1]["content"]
    assert "When | What | Out | In | Left" in sent and "NEFT SALARY ACME" in sent
    assert "Rohan Kulkarni" not in sent and "50100123451234" not in sent  # preamble isn't sent
    assert "123456789012" not in sent and "XXXXXXXX" in sent  # long numbers hidden in rows too


def test_at_most_five_sample_rows():
    rows = ["Date,Info,Amt", *[f"0{d}/01/2026,X,1.00" for d in range(1, 9)]]
    _, samples = pick_lines([LayoutLine(i, tuple(r.split(","))) for i, r in enumerate(rows)])
    assert len(samples) == 5


@pytest.mark.parametrize("changes, why", [
    ({"columns": {**GOOD["columns"], "debit": "In", "credit": "Out"}}, "don't add up to the balances"),
    ({"columns": {**GOOD["columns"], "description": "Narration"}}, "isn't in the header line"),
    ({"columns": {"date": "When", "description": "What"}}, "which column holds the amounts"),
    ({"date_order": "day-first"}, "unrecognised date '01/19/2026'"),
])
def test_a_reading_that_doesnt_check_out_is_rejected(odd_csv, changes, why):
    with pytest.raises(LayoutError, match=why) as e:
        _load(odd_csv, FakeAI(_answer(odd_csv, "When What Out In Left", **changes)))
    assert "no header row found" in str(e.value)  # the rules' reason is kept


def _statement(*rows):
    txns = [Transaction(date=d, description="X", amount=a, type=t, balance=b) for d, a, t, b in rows]
    info = StatementInfo(period_start=min(t.date for t in txns), period_end=max(t.date for t in txns))
    return NormalizedStatement(info, txns)


def test_check_accepts_newest_first_and_rejects_scrambled_dates():
    newest_first = _statement(
        (dt.date(2026, 1, 9), 100, "debit", 900), (dt.date(2026, 1, 5), 1000, "credit", 1000),
    )
    check(newest_first, has_balance=True)
    scrambled = _statement(*[(dt.date(2026, 1, d), 1, "debit", None) for d in (5, 20, 2, 28, 9)])
    with pytest.raises(LayoutError, match="don't run in order"):
        check(scrambled, has_balance=False)


@pytest.mark.parametrize("answer, why", [
    ("Sure! The header is the third line.", "wasn't in the expected format"),
    ({**GOOD, "header_line": 0}, "isn't one of the possible header lines"),
    (llm.LLMError("HTTP 401"), "couldn't be reached"),
])
def test_unusable_answers_are_rejected(odd_csv, answer, why):
    with pytest.raises(LayoutError, match=why):
        _load(odd_csv, FakeAI(answer))


def test_no_ai_call_when_nothing_looks_like_a_table(tmp_path):
    path = tmp_path / "junk.csv"
    path.write_text("a,b\n1,2\n")
    ai = FakeAI(GOOD)
    with pytest.raises(HeaderNotFoundError):
        _load(path, ai)
    assert ai.calls == []


def test_headings_found_but_not_understood(tmp_path):
    path = tmp_path / "info.csv"
    path.write_text("Date,Info,Debit,Credit,Balance\n05/01/2026,UPI-A,10.00,,990.00\n06/01/2026,UPI-B,,20.00,1010.00\n")
    with pytest.raises(ColumnMappingError):
        load_statement(path)
    answer = {
        "header_line": 0, "date_order": "day-first",
        "columns": {"date": "Date", "description": "Info", "debit": "Debit", "credit": "Credit", "balance": "Balance"},
    }
    statement = load_with_ai_layout(path, None, rules_error=ColumnMappingError("can't map"), complete=FakeAI(answer))
    assert [t.description for t in statement.transactions] == ["UPI-A", "UPI-B"]


@pytest.fixture
def odd_pdf(tmp_path):
    path = tmp_path / "odd.pdf"
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setFont("Helvetica", 9)
    c.drawString(40, 800, "Customer: Rohan Kulkarni   Account Number: 50100123451234")
    xs = [40, 110, 330, 400, 470]
    for x, text in zip(xs, ["When", "What", "Out", "In", "Left"]):
        c.drawString(x, 770, text)
    for i, row in enumerate(ODD_CSV.splitlines()[3:]):
        for x, text in zip(xs, row.split(",")):
            c.drawString(x, 755 - 14 * i, text)
    c.save()
    return path


def test_pdf_header_chosen_by_the_ai(odd_pdf):
    statement = _load(odd_pdf, FakeAI(_answer(odd_pdf, "When What Out In Left")))
    assert [(t.description, t.amount) for t in statement.transactions] == [
        ("UPI/SWIGGY/604118227731", 250.0), ("NEFT SALARY ACME", 5000.0), ("ATM CASH 123456789012", 1000.0),
    ]


def test_pipeline_falls_back_to_the_ai(odd_csv, monkeypatch):
    header_line = _answer(odd_csv, "When What Out In Left")["header_line"]

    def chat(messages, **kwargs):
        system = messages[0]["content"]
        if "layout of a bank statement" in system:
            return json.dumps({**GOOD, "header_line": header_line})
        return "[]" if "categorize" in system else "A quiet month."
    monkeypatch.setattr(llm, "chat", chat)
    result = analyze_file(odd_csv)
    assert result.summary.transaction_count == 3
