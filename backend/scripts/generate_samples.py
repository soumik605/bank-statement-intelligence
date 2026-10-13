"""Generate fake bank statements with known answers, for testing parsers.

Writes four statements to data/samples/, each in a different layout, and next to
each one a <name>.truth.json holding the correct parse of every row. All banks,
people and account numbers are invented; no real bank data is used. Output is
deterministic, so rerunning only changes files when this script changes.

Usage (from backend/):
    uv run python scripts/generate_samples.py               # write to data/samples/
    uv run python scripts/generate_samples.py --out DIR     # write somewhere else
"""
import argparse
import calendar
import csv
import datetime as dt
import json
import random
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, Field
from reportlab import rl_config
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.schema import Category, StatementInfo, Transaction  # noqa: E402

OUTPUT_DIR = BACKEND_DIR.parent / "data" / "samples"
SEED = 2026

# Same bytes on every run: no embedded timestamps or random document IDs.
rl_config.invariant = 1


# --- Ground truth -------------------------------------------------------------

class TruthRow(BaseModel):
    transaction: Transaction = Field(description="What a correct parser produces for this row")
    expected_category: Category
    expected_unusual: bool
    expected_unusual_reason: str | None = None
    trap: str | None = Field(default=None, description="Why this row is tricky, if it is")


class Truth(BaseModel):
    source_file: str
    statement: StatementInfo
    opening_balance: float
    closing_balance: float
    rows: list[TruthRow]


# Category and unusual-ness are not something a parser reads off the page, so the
# truth carries them as expected_* fields instead of inside the transaction.
_TRUTH_EXCLUDE = {"rows": {"__all__": {"transaction": {"category", "category_source", "is_unusual", "unusual_reason"}}}}


# --- Events -------------------------------------------------------------------

@dataclass
class Event:
    date: dt.date
    kind: str
    amount: Decimal
    type: str  # "credit" or "debit"
    category: Category
    description: str | None = None  # fixed text; otherwise built from the bank's style
    unusual_reason: str | None = None
    trap: str | None = None


@dataclass
class Row:
    event: Event
    description: str
    balance: Decimal

    @property
    def is_debit(self) -> bool:
        return self.event.type == "debit"


@dataclass
class Spec:
    stem: str
    suffix: str
    bank_name: str
    holder_name: str
    account_number: str
    period_start: dt.date
    period_end: dt.date
    opening: Decimal
    salary: Decimal
    rent: Decimal
    employer: str
    style: dict[str, str]
    writer: Callable[[Path, "Spec", list[Row], random.Random], None]
    extras: list[Event] = field(default_factory=list)


def month_events(rng: random.Random, year: int, month: int, spec: Spec) -> list[Event]:
    last = calendar.monthrange(year, month)[1]

    def on(lo: int, hi: int) -> dt.date:
        return dt.date(year, month, rng.randint(lo, min(hi, last)))

    def money(lo: int, hi: int) -> Decimal:
        return Decimal(rng.randint(lo * 100, hi * 100)) / 100

    events = [
        Event(dt.date(year, month, 1), "salary", spec.salary, "credit", Category.INCOME),
        Event(on(2, 5), "rent", spec.rent, "debit", Category.BILLS),
        Event(on(8, 14), "electricity", money(900, 2600), "debit", Category.BILLS),
        Event(on(3, 26), "atm", Decimal(rng.choice([2000, 3000, 5000])), "debit", Category.CASH),
        Event(on(5, 25), "grocery", money(1200, 4200), "debit", Category.FOOD),
        Event(on(5, 25), "amazon", money(600, 3500), "debit", Category.SHOPPING),
    ]
    events += [Event(on(1, 31), "swiggy", money(180, 850), "debit", Category.FOOD) for _ in range(rng.randint(4, 7))]
    events += [Event(on(1, 31), "uber", money(95, 640), "debit", Category.TRANSPORT) for _ in range(rng.randint(3, 6))]
    return events


def months(start: dt.date, end: dt.date) -> list[tuple[int, int]]:
    out, (y, m) = [], (start.year, start.month)
    while (y, m) <= (end.year, end.month):
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def build_rows(spec: Spec, rng: random.Random) -> list[Row]:
    events = [e for y, m in months(spec.period_start, spec.period_end) for e in month_events(rng, y, m, spec)]
    events += spec.extras
    events.sort(key=lambda e: e.date)  # stable: keeps salary first on the 1st

    rows, balance = [], spec.opening
    for e in events:
        balance += e.amount if e.type == "credit" else -e.amount
        if balance < 0:
            raise ValueError(f"{spec.stem}: balance goes negative on {e.date}; raise the opening balance")
        description = e.description or spec.style[e.kind].format(
            ref=f"{rng.randrange(10**12):012d}",
            ref6=f"{rng.randrange(10**6):06d}",
            emp=spec.employer,
            mon=e.date.strftime("%b%y").upper(),
            month_name=e.date.strftime("%B %Y").upper(),
        )
        rows.append(Row(e, description, balance))
    return rows


# --- Number formats -----------------------------------------------------------

def plain(d: Decimal) -> str:
    return f"{d:.2f}"


def western(d: Decimal) -> str:
    return f"{d:,.2f}"


def indian(d: Decimal) -> str:
    """1234567.5 -> 12,34,567.50 (lakh/crore grouping)."""
    whole, frac = f"{d:.2f}".split(".")
    head, groups = whole[:-3], [whole[-3:]]
    while head:
        groups.insert(0, head[-2:])
        head = head[:-2]
    return ",".join(groups) + "." + frac


# --- Writers: each bank has its own layout --------------------------------------

def write_northwind_csv(path: Path, spec: Spec, rows: list[Row], rng: random.Random) -> None:
    """Separate Withdrawal/Deposit columns, DD/MM/YYYY, plain numbers, no preamble."""
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Date", "Narration", "Chq./Ref.No.", "Value Dt", "Withdrawal Amt.", "Deposit Amt.", "Closing Balance"])
        for r in rows:
            # NEFT credits settle the next day, so Value Dt differs from Date.
            value_date = r.event.date + dt.timedelta(days=1 if r.event.kind == "salary" else 0)
            amount = plain(r.event.amount)
            w.writerow([
                r.event.date.strftime("%d/%m/%Y"),
                r.description,
                f"{rng.randrange(10**12):016d}",  # leading zeros: a trap for numeric coercion
                value_date.strftime("%d/%m/%Y"),
                amount if r.is_debit else "",
                "" if r.is_debit else amount,
                plain(r.balance),
            ])


def write_konark_csv(path: Path, spec: Spec, rows: list[Row], rng: random.Random) -> None:
    """Single Amount column plus a DR/CR marker, DD-Mon-YYYY, Indian grouping, preamble and footer rows."""
    period = f"{spec.period_start:%d-%b-%Y} to {spec.period_end:%d-%b-%Y}"
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow([spec.bank_name])
        w.writerow(["Account Holder", spec.holder_name])
        w.writerow(["Account Number", spec.account_number])
        w.writerow(["Statement Period", period])
        w.writerow(["Opening Balance", indian(spec.opening)])
        w.writerow([])
        w.writerow(["Txn Date", "Value Date", "Particulars", "Amount", "Dr/Cr", "Balance"])
        for r in rows:
            d = r.event.date.strftime("%d-%b-%Y")
            w.writerow([d, d, r.description, indian(r.event.amount), "DR" if r.is_debit else "CR", indian(r.balance)])
        w.writerow([])
        w.writerow(["*** End of Statement ***"])


def _pdf(path: Path, spec: Spec, header_lines: list[str], table: list[list], col_widths: list[float],
         wrap_col: int, right_cols: list[int], bold_rows: list[int]) -> None:
    styles = getSampleStyleSheet()
    cell = styles["BodyText"].clone("cell", fontSize=8, leading=10)

    def footer(canvas, doc) -> None:
        canvas.setFont("Helvetica", 7)
        canvas.drawString(15 * mm, 10 * mm, "This is a computer generated statement and does not require a signature.")
        canvas.drawRightString(A4[0] - 15 * mm, 10 * mm, f"Page {doc.page}")

    data = [table[0]] + [
        [Paragraph(c, cell) if i == wrap_col else c for i, c in enumerate(row)] for row in table[1:]
    ]
    t = Table(data, colWidths=col_widths, repeatRows=1)
    style = [
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8),
        ("FONT", (0, 1), (-1, -1), "Helvetica", 8),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#dde3ea")),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]
    style += [("ALIGN", (c, 1), (c, -1), "RIGHT") for c in right_cols]
    style += [("FONT", (0, r), (-1, r), "Helvetica-Bold", 8) for r in bold_rows]
    t.setStyle(TableStyle(style))

    story = [Paragraph(spec.bank_name, styles["Title"])]
    story += [Paragraph(line, styles["Normal"]) for line in header_lines]
    story += [Spacer(1, 6 * mm), t]
    doc = SimpleDocTemplate(
        str(path), pagesize=A4, title=f"{spec.bank_name} statement", author=spec.bank_name,
        leftMargin=15 * mm, rightMargin=15 * mm, topMargin=15 * mm, bottomMargin=18 * mm,
    )
    doc.build(story, onFirstPage=footer, onLaterPages=footer)


def write_meridian_pdf(path: Path, spec: Spec, rows: list[Row], rng: random.Random) -> None:
    """Separate Withdrawal/Deposit columns, DD Mon YYYY, balance with a Cr suffix,
    a B/F opening-balance row inside the table, and narrations that wrap."""
    table = [["Date", "Narration", "Chq./Ref.No.", "Withdrawal (Dr)", "Deposit (Cr)", "Balance"]]
    table.append([f"{spec.period_start:%d %b %Y}", "B/F OPENING BALANCE", "", "", "", f"{indian(spec.opening)} Cr"])
    for r in rows:
        amount = indian(r.event.amount)
        table.append([
            f"{r.event.date:%d %b %Y}",
            r.description,
            f"{rng.randrange(10**9):09d}",
            amount if r.is_debit else "",
            "" if r.is_debit else amount,
            f"{indian(r.balance)} Cr",
        ])
    header = [
        "Statement of Account",
        f"Account Holder: {spec.holder_name}",
        f"Account No: {spec.account_number}",
        f"Statement Period: {spec.period_start:%d/%m/%Y} - {spec.period_end:%d/%m/%Y}",
        f"Closing Balance: INR {indian(rows[-1].balance)}",
    ]
    _pdf(path, spec, header, table, [22 * mm, 66 * mm, 22 * mm, 23 * mm, 23 * mm, 24 * mm],
         wrap_col=1, right_cols=[3, 4, 5], bold_rows=[1])


def write_sahyadri_pdf(path: Path, spec: Spec, rows: list[Row], rng: random.Random) -> None:
    """Single Amount column with a Dr/Cr suffix, DD/MM/YY, western grouping, columns
    in a different order, and month-total rows that look like transactions."""
    table = [["Date", "Particulars", "Amount (INR)", "Balance (INR)"]]
    bold_rows = []
    for (y, m) in months(spec.period_start, spec.period_end):
        in_month = [r for r in rows if (r.event.date.year, r.event.date.month) == (y, m)]
        for r in in_month:
            marker = "Dr" if r.is_debit else "Cr"
            table.append([f"{r.event.date:%d/%m/%y}", r.description, f"{western(r.event.amount)} {marker}", western(r.balance)])
        label = dt.date(y, m, 1).strftime("%B %Y")
        debits = sum((r.event.amount for r in in_month if r.is_debit), Decimal(0))
        credits = sum((r.event.amount for r in in_month if not r.is_debit), Decimal(0))
        table.append(["", f"Total Debits - {label}", f"{western(debits)} Dr", ""])
        table.append(["", f"Total Credits - {label}", f"{western(credits)} Cr", ""])
        bold_rows += [len(table) - 2, len(table) - 1]
    header = [
        f"Customer: {spec.holder_name}",
        f"A/c: {spec.account_number}",
        f"From {spec.period_start:%d %B %Y} to {spec.period_end:%d %B %Y}",
        f"Opening Balance: {western(spec.opening)}",
    ]
    _pdf(path, spec, header, table, [22 * mm, 90 * mm, 34 * mm, 34 * mm],
         wrap_col=1, right_cols=[2, 3], bold_rows=bold_rows)


# --- The statements -----------------------------------------------------------

RAMESH = "Masked phone digits look like an amount or reference; it's a payment to a person (transfer), not a merchant"
REFUND = "Amazon keyword suggests shopping, but it's a credit: a refund is income, not spending"
SELF = "Moving money to the holder's own savings account; not an expense"
HUGE = "One-off amount far above anything else in the statement"


def specs() -> list[Spec]:
    return [
        Spec(
            stem="northwind_jan2026", suffix="csv",
            bank_name="Northwind Bank", holder_name="Ananya Rao", account_number="XXXXXXXX4821",
            period_start=dt.date(2026, 1, 1), period_end=dt.date(2026, 1, 31),
            opening=Decimal("48250.75"), salary=Decimal("115000"), rent=Decimal("28000"),
            employer="ACME TECHNOLOGIES PVT LTD",
            style={
                "salary": "NEFT CR-{emp}-SALARY {mon}",
                "rent": "IMPS-{ref}-PRAKASH NAIR-RENT",
                "electricity": "BILLPAY-BESCOM-{ref6}",
                "atm": "ATW-XXXXXX4821-MG ROAD BANGALORE",
                "grocery": "UPI-BIGBASKET-{ref}",
                "amazon": "AMAZON PAY INDIA, BANGALORE",
                "swiggy": "UPI-SWIGGY-{ref}",
                "uber": "UPI-UBER INDIA-{ref}",
            },
            writer=write_northwind_csv,
            extras=[
                Event(dt.date(2026, 1, 7), "person", Decimal("1500"), "debit", Category.TRANSFER,
                      "UPI/9876xxxx/RAMESH", trap=RAMESH),
                Event(dt.date(2026, 1, 12), "refund", Decimal("1299"), "credit", Category.INCOME,
                      "AMZN REFUND", trap=REFUND),
                Event(dt.date(2026, 1, 15), "self", Decimal("25000"), "debit", Category.TRANSFER,
                      "NEFT-SELF-SAVINGS", trap=SELF),
                Event(dt.date(2026, 1, 18), "swiggy", Decimal("412.50"), "debit", Category.FOOD,
                      "UPI-SWIGGY-604118227731", trap="Same merchant and amount as the next row on the same day; both are real orders, not a duplicate"),
                Event(dt.date(2026, 1, 18), "swiggy", Decimal("412.50"), "debit", Category.FOOD,
                      "UPI-SWIGGY-604118229054", trap="Same merchant and amount as the previous row; not a duplicate"),
                Event(dt.date(2026, 1, 21), "huge", Decimal("400000"), "credit", Category.TRANSFER,
                      "NEFT CR-RAO FAMILY TRUST-GIFT", trap=HUGE,
                      unusual_reason="One-off incoming transfer of 4,00,000.00, about 3.5x the monthly salary"),
            ],
        ),
        Spec(
            stem="konark_jan2026", suffix="csv",
            bank_name="Konark Co-operative Bank Ltd.", holder_name="Rohan Kulkarni", account_number="XXXX XXXX 7710",
            period_start=dt.date(2026, 1, 1), period_end=dt.date(2026, 1, 31),
            opening=Decimal("620400.00"), salary=Decimal("92500"), rent=Decimal("21000"),
            employer="BLUEPEAK LOGISTICS",
            style={
                "salary": "BY NEFT/{emp}/SAL {mon}",
                "rent": "TO IMPS/{ref}/MEERA JOSHI",
                "electricity": "TO BBPS/MSEDCL/{ref6}",
                "atm": "TO ATM WDL/PUNE CAMP/{ref6}",
                "grocery": "TO UPI/DMART/{ref}",
                "amazon": "TO UPI/AMAZON/{ref}",
                "swiggy": "TO UPI/SWIGGY/{ref}",
                "uber": "TO UPI/UBER/{ref}",
            },
            writer=write_konark_csv,
            extras=[
                Event(dt.date(2026, 1, 6), "person", Decimal("2200"), "debit", Category.TRANSFER,
                      "TO UPI/9876xxxx/RAMESH", trap=RAMESH),
                Event(dt.date(2026, 1, 13), "refund", Decimal("849"), "credit", Category.INCOME,
                      "BY UPI/AMZN REFUND/RF0091", trap=REFUND),
                Event(dt.date(2026, 1, 16), "self", Decimal("15000"), "debit", Category.TRANSFER,
                      "TO TRF/OWN A/C XX9012/SAVINGS", trap=SELF),
                Event(dt.date(2026, 1, 22), "huge", Decimal("500000"), "debit", Category.TRANSFER,
                      "TO RTGS/HOMESTEAD BUILDERS/FLAT BOOKING", trap=HUGE,
                      unusual_reason="One-off outgoing transfer of 5,00,000.00, about 5.4x the monthly salary"),
            ],
        ),
        Spec(
            stem="meridian_mar2026", suffix="pdf",
            bank_name="Meridian Bank", holder_name="Farah Siddiqui", account_number="******3307",
            period_start=dt.date(2026, 3, 1), period_end=dt.date(2026, 3, 31),
            opening=Decimal("310000.00"), salary=Decimal("138000"), rent=Decimal("35000"),
            employer="ORBIT ANALYTICS PRIVATE LIMITED",
            style={
                # Long on purpose so they wrap onto a second line inside the cell.
                "salary": "NEFT CR-MRDN0000123-{emp}-SALARY FOR {month_name}",
                "rent": "STANDING INSTRUCTION RENT PAYMENT TO RAJIV MENON A/C XXXXXX5521",
                "electricity": "BILL PAYMENT TATA POWER DDL CA NO {ref6}",
                "atm": "CASH WITHDRAWAL ATM CONNAUGHT PLACE {ref6}",
                "grocery": "UPI {ref} BLINKIT GROCERY",
                "amazon": "UPI {ref} AMAZON SELLER SERVICES",
                "swiggy": "UPI {ref} SWIGGY ORDER",
                "uber": "UPI {ref} UBER RIDE",
            },
            writer=write_meridian_pdf,
            extras=[
                Event(dt.date(2026, 3, 9), "person", Decimal("1800"), "debit", Category.TRANSFER,
                      "UPI/9876xxxx/RAMESH", trap=RAMESH),
                Event(dt.date(2026, 3, 11), "refund", Decimal("2499"), "credit", Category.INCOME,
                      "AMZN REFUND", trap=REFUND),
                Event(dt.date(2026, 3, 17), "self", Decimal("40000"), "debit", Category.TRANSFER,
                      "NEFT-SELF-SAVINGS", trap=SELF),
                Event(dt.date(2026, 3, 24), "huge", Decimal("275000"), "debit", Category.TRANSFER,
                      "IMPS-TRANSFER TO VIKRAM SHAH-PERSONAL LOAN", trap=HUGE,
                      unusual_reason="One-off outgoing transfer of 2,75,000.00, about 2x the monthly salary"),
            ],
        ),
        Spec(
            stem="sahyadri_feb-mar2026", suffix="pdf",
            bank_name="Sahyadri Gramin Bank", holder_name="Thomas Varghese", account_number="XXXXXX9012",
            period_start=dt.date(2026, 2, 1), period_end=dt.date(2026, 3, 31),
            opening=Decimal("72815.40"), salary=Decimal("84000"), rent=Decimal("18500"),
            employer="Coastline Shipping Co",
            style={
                "salary": "Salary Credit - {emp} - {mon}",
                "rent": "Rent Transfer to Kavita Deshpande",
                "electricity": "Electricity Bill - KSEB - Cons {ref6}",
                "atm": "ATM Cash Wdl - Kochi MG Road",
                "grocery": "UPI/{ref}/Reliance Fresh",
                "amazon": "UPI/{ref}/Amazon India",
                "swiggy": "UPI/{ref}/Swiggy",
                "uber": "UPI/{ref}/Uber India",
            },
            writer=write_sahyadri_pdf,
            extras=[
                Event(dt.date(2026, 2, 5), "person", Decimal("1500"), "debit", Category.TRANSFER,
                      "UPI/9876xxxx/RAMESH", trap=RAMESH),
                Event(dt.date(2026, 3, 5), "person", Decimal("1500"), "debit", Category.TRANSFER,
                      "UPI/9876xxxx/RAMESH", trap=RAMESH),
                Event(dt.date(2026, 2, 19), "refund", Decimal("649"), "credit", Category.INCOME,
                      "AMZN REFUND", trap=REFUND),
                Event(dt.date(2026, 3, 2), "self", Decimal("20000"), "debit", Category.TRANSFER,
                      "NEFT-SELF-SAVINGS", trap=SELF),
                Event(dt.date(2026, 3, 20), "huge", Decimal("840000"), "credit", Category.INCOME,
                      "NEFT CR-LIFECOVER INSURANCE-POLICY MATURITY", trap=HUGE,
                      unusual_reason="One-off incoming credit of 8,40,000.00, about 10x the monthly salary"),
            ],
        ),
    ]


# --- Main ---------------------------------------------------------------------

def generate(out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for spec in specs():
        rng = random.Random(f"{SEED}-{spec.stem}")
        rows = build_rows(spec, rng)
        source = out_dir / f"{spec.stem}.{spec.suffix}"
        spec.writer(source, spec, rows, rng)

        truth = Truth(
            source_file=source.name,
            statement=StatementInfo(
                bank_name=spec.bank_name, account_number=spec.account_number, holder_name=spec.holder_name,
                period_start=spec.period_start, period_end=spec.period_end,
            ),
            opening_balance=float(spec.opening),
            closing_balance=float(rows[-1].balance),
            rows=[
                TruthRow(
                    transaction=Transaction(
                        date=r.event.date, description=r.description, amount=float(r.event.amount),
                        type=r.event.type, balance=float(r.balance),
                    ),
                    expected_category=r.event.category,
                    expected_unusual=r.event.unusual_reason is not None,
                    expected_unusual_reason=r.event.unusual_reason,
                    trap=r.event.trap,
                )
                for r in rows
            ],
        )
        truth_path = out_dir / f"{spec.stem}.truth.json"
        truth_path.write_text(json.dumps(truth.model_dump(mode="json", exclude=_TRUTH_EXCLUDE), indent=2) + "\n")
        written += [source, truth_path]
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=OUTPUT_DIR, help=f"output directory (default: {OUTPUT_DIR})")
    args = parser.parse_args()
    for path in generate(args.out):
        print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
