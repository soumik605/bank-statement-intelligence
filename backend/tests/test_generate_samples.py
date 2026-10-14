import json
from decimal import Decimal

import pytest

from app.schema import Transaction
from scripts.generate_samples import generate


@pytest.fixture(scope="module")
def out_dir(tmp_path_factory):
    path = tmp_path_factory.mktemp("samples")
    generate(path)
    return path


@pytest.fixture(scope="module")
def truths(out_dir):
    return [json.loads(p.read_text()) for p in sorted(out_dir.glob("*.truth.json"))]


def test_writes_csv_and_pdf(out_dir, truths):
    assert len(truths) == 6
    assert [t["source_file"] for t in truths if t["password"]] == ["sahyadri_feb-mar2026_locked.pdf"]
    assert {t["source_file"].rsplit(".", 1)[1] for t in truths} == {"csv", "pdf"}
    assert all((out_dir / t["source_file"]).stat().st_size > 0 for t in truths)


def test_csv_layouts_differ(out_dir):
    first_lines = {p.read_text().splitlines()[0] for p in out_dir.glob("*.csv")}
    assert len(first_lines) == 2


def test_balances_add_up(truths):
    for t in truths:
        balance = Decimal(str(t["opening_balance"]))
        for row in t["rows"]:
            txn = Transaction(**row["transaction"])
            balance += Decimal(str(txn.amount)) * (1 if txn.type == "credit" else -1)
            assert Decimal(str(txn.balance)) == balance
        assert balance == Decimal(str(t["closing_balance"]))


def test_tricky_rows_present(truths):
    descriptions = [row["transaction"]["description"] for t in truths for row in t["rows"]]
    for text in ["UPI/9876xxxx/RAMESH", "AMZN REFUND", "NEFT-SELF-SAVINGS"]:
        assert text in descriptions
    for t in truths:
        assert sum(row["expected_unusual"] for row in t["rows"]) == 1


def test_deterministic(tmp_path, out_dir):
    generate(tmp_path)
    for p in out_dir.iterdir():
        assert (tmp_path / p.name).read_bytes() == p.read_bytes(), p.name
