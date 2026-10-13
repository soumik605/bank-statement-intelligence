# backend/scripts/render_statements.py
"""Render simulated statements as bank exports in sample_statements/.

    Bank A  CSV: info lines above the table, DD/MM/YYYY, Debit/Credit columns, Western grouping
    Bank B  PDF: grid lines, Txn/Value Date, Amount + Dr/Cr, Western grouping (245,000.00)
    Bank C  PDF: no grid lines, DD-Mon-YYYY, signed amounts, Indian grouping (2,45,000.00)

Each file gets a <name>_truth.json: the PipelinePayload with true categories and
the ambiguous-row labels in meta.

Usage (from backend/):
    uv run python scripts/render_statements.py
"""
import csv
import datetime as dt
import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.schema import PipelinePayload  # noqa: E402
from app.simulator import simulate  # noqa: E402

OUTPUT_DIR = BACKEND_DIR.parent / "sample_statements"
START = dt.date(2026, 1, 1)
STEM = "jan2026"


def western(x: float) -> str:
    return f"{x:,.2f}"


def indian(x: float) -> str:
    """2,45,000.00 style: last three digits, then groups of two."""
    whole, frac = f"{abs(x):.2f}".split(".")
    if len(whole) > 3:
        head, groups = whole[:-3], [whole[-3:]]
        while head:
            groups.insert(0, head[-2:])
            head = head[:-2]
        whole = ",".join(groups)
    return f"{'-' if x < 0 else ''}{whole}.{frac}"


def write_truth(payload: PipelinePayload, rendered: Path) -> Path:
    payload.meta["rendered_file"] = rendered.name
    out = rendered.with_name(f"{rendered.stem}_truth.json")
    out.write_text(payload.model_dump_json(indent=2) + "\n")
    return out


def page_number(canvas, doc) -> None:
    canvas.saveState()
    canvas.setFont("Helvetica", 7)
    canvas.drawRightString(A4[0] - doc.rightMargin, 20, f"Page {doc.page}")
    canvas.restoreState()


def bank_a(payload: PipelinePayload) -> Path:
    s, out = payload.statement, OUTPUT_DIR / f"bank_a_{STEM}.csv"
    with out.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Bank A", "Statement of Account"])
        w.writerow(["Account Holder", s.account_holder])
        w.writerow(["Account Number", s.account_number])
        w.writerow(["Period", f"{s.period_start:%d/%m/%Y} to {s.period_end:%d/%m/%Y}"])
        w.writerow([])
        w.writerow(["Date", "Narration", "Debit", "Credit", "Balance"])
        for t in payload.transactions:
            w.writerow([
                f"{t.date:%d/%m/%Y}",
                t.description,
                western(t.amount) if t.direction == "debit" else "",
                western(t.amount) if t.direction == "credit" else "",
                western(t.balance),
            ])
    return out


def bank_b(payload: PipelinePayload) -> Path:
    s, out = payload.statement, OUTPUT_DIR / f"bank_b_{STEM}.pdf"
    styles = getSampleStyleSheet()
    cell = ParagraphStyle("cell", parent=styles["Normal"], fontSize=7.5, leading=9)

    rows = []
    for t in payload.transactions:
        # Card payments settle a day after the transaction.
        settles_later = t.description.startswith(("IPS/", "VPS/", "POS", "ECOM"))
        value_date = t.date + dt.timedelta(days=1 if settles_later else 0)
        rows.append([
            f"{t.date:%d/%m/%Y}",
            f"{value_date:%d/%m/%Y}",
            Paragraph(t.description, cell),
            western(t.amount),
            "Dr" if t.direction == "debit" else "Cr",
            western(t.balance),
        ])

    table = Table([["Txn Date", "Value Date", "Description", "Amount", "Dr/Cr", "Balance"]] + rows,
                  colWidths=[55, 55, 235, 70, 33, 75], repeatRows=1)
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8edf3")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 7.5),
        ("ALIGN", (3, 1), (3, -1), "RIGHT"),
        ("ALIGN", (5, 1), (5, -1), "RIGHT"),
        ("ALIGN", (4, 1), (4, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))

    head = [
        Paragraph("BANK B - Statement of Account", styles["Title"]),
        Paragraph(f"Account Holder: {s.account_holder}", styles["Normal"]),
        Paragraph(f"Account No: {s.account_number}", styles["Normal"]),
        Paragraph(f"Period: {s.period_start:%d/%m/%Y} to {s.period_end:%d/%m/%Y}", styles["Normal"]),
        Paragraph(f"Opening Balance: {western(s.opening_balance)}", styles["Normal"]),
        Spacer(1, 12),
    ]
    tail = [Spacer(1, 12), Paragraph(f"Closing Balance: {western(s.closing_balance)}", styles["Normal"])]
    doc = SimpleDocTemplate(str(out), pagesize=A4, leftMargin=36, rightMargin=36, topMargin=40, bottomMargin=40)
    doc.build(head + [table] + tail, onFirstPage=page_number, onLaterPages=page_number)
    return out


def bank_c(payload: PipelinePayload) -> Path:
    s, out = payload.statement, OUTPUT_DIR / f"bank_c_{STEM}.pdf"
    styles = getSampleStyleSheet()
    title = ParagraphStyle("title", parent=styles["Normal"], fontName="Times-Bold", fontSize=16, leading=20)
    small = ParagraphStyle("small", parent=styles["Normal"], fontName="Times-Roman", fontSize=9, leading=12)
    cell = ParagraphStyle("cell", parent=styles["Normal"], fontName="Times-Roman", fontSize=8, leading=9.5)

    rows = [[
        f"{t.date:%d-%b-%Y}",
        t.reference or "",
        Paragraph(t.description, cell),
        indian(t.signed_amount),
        indian(t.balance),
    ] for t in payload.transactions]

    # No grid or backgrounds, so pdfplumber has to fall back to its text strategy.
    table = Table([["Date", "Ref No.", "Particulars", "Amount (INR)", "Balance (INR)"]] + rows,
                  colWidths=[58, 92, 215, 72, 78], repeatRows=1)
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "Times-Bold"),
        ("FONTNAME", (0, 1), (-1, -1), "Times-Roman"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("ALIGN", (3, 0), (4, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
    ]))

    summary = Table([
        ["Customer Name", s.account_holder, "Statement Period",
         f"{s.period_start:%d-%b-%Y} to {s.period_end:%d-%b-%Y}"],
        ["A/c No.", s.account_number, "Currency", s.currency],
        ["Opening Balance", indian(s.opening_balance), "Closing Balance", indian(s.closing_balance)],
    ], colWidths=[85, 150, 90, 190])
    summary.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "Times-Roman"),
        ("FONTNAME", (0, 0), (0, -1), "Times-Bold"),
        ("FONTNAME", (2, 0), (2, -1), "Times-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
    ]))

    story = [
        Paragraph("Bank C", title),
        Paragraph("Savings Account Statement", small),
        Spacer(1, 10),
        summary,
        Spacer(1, 14),
        table,
        Spacer(1, 14),
        Paragraph("*** End of Statement ***", small),
    ]
    doc = SimpleDocTemplate(str(out), pagesize=A4, leftMargin=40, rightMargin=40, topMargin=40, bottomMargin=40)
    doc.build(story, onFirstPage=page_number, onLaterPages=page_number)
    return out


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    # (renderer, narration style, seed): each bank gets its own customer and narration style.
    for render, style, seed in ((bank_a, "hdfc", 101), (bank_b, "icici", 202), (bank_c, "axis", 303)):
        payload = simulate(start=START, months=1, seed=seed, bank=style)
        out = render(payload)
        truth = write_truth(payload, out)
        print(f"Wrote {out.name} ({len(payload.transactions)} txns, "
              f"{len(payload.meta['ambiguous_rows'])} ambiguous) + {truth.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
