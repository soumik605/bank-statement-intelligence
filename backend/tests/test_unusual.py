import json

from app.schema import Category, Transaction
from app.unusual import detect_unusual, format_inr, payee_key
from scripts.generate_samples import generate


def _txn(description, amount, txn_type="debit", category=Category.OTHER, date="2026-01-10", balance=None):
    return Transaction(date=date, description=description, amount=amount, type=txn_type,
                       category=category, category_source="rule", balance=balance)


# Twenty everyday debits put the median at ₹500.
EVERYDAY = [_txn(f"UPI-SHOP{chr(65 + i)}-1", 500, date="2026-01-02") for i in range(20)]


def _flags(*txns):
    return {t.description: t.unusual_reason for t in detect_unusual([*EVERYDAY, *txns]) if t.is_unusual}


def test_large_debit_against_median():
    flags = _flags(_txn("CROMA ELECTRONICS", 30000, category=Category.SHOPPING))
    assert flags == {"CROMA ELECTRONICS": "₹30,000 is 60.0× your typical debit of ₹500"}


def test_large_debit_needs_the_floor():
    # 3x the median is only ₹1,500; a ₹4,000 grocery run is normal.
    assert _flags(_txn("BIGBASKET", 4000, category=Category.FOOD)) == {}


def test_salary_rent_and_credits_not_flagged_for_size():
    assert _flags(
        _txn("ACME SALARY", 150000, "credit", Category.SALARY),
        _txn("RENT TO LANDLORD", 60000, category=Category.RENT),
        _txn("BESCOM", 12000, category=Category.UTILITIES),
        _txn("INTEREST", 12345.67, "credit", Category.INCOME),
    ) == {}


def test_round_number_either_direction():
    flags = _flags(_txn("NEFT CR-FAMILY TRUST", 85000, "credit", Category.TRANSFER))
    assert flags == {"NEFT CR-FAMILY TRUST": "Large round-number amount of ₹85,000 in"}


def test_possible_duplicate_within_a_day():
    flags = _flags(
        _txn("UPI-SWIGGY-111", 412.5, date="2026-01-18"),
        _txn("UPI-SWIGGY-222", 412.5, date="2026-01-19"),
        _txn("UPI-ZOMATO-333", 300, date="2026-01-18"),
        _txn("UPI-ZOMATO-444", 300, date="2026-01-21"),  # 3 days later: not a duplicate
    )
    assert flags == {"UPI-SWIGGY-222": "Possible duplicate: same amount (₹412.50) to the same payee as on 18 Jan"}


def test_first_time_payee_large_amount():
    flags = _flags(
        _txn("IMPS-VIKRAM SHAH-123", 60123.45, category=Category.TRANSFER, date="2026-01-05"),
        _txn("IMPS-VIKRAM SHAH-456", 60500.10, category=Category.TRANSFER, date="2026-01-20"),
    )
    assert flags == {"IMPS-VIKRAM SHAH-123": "First payment to this payee, and a large one (₹60,123.45)"}


def test_balance_drop():
    flags = _flags(_txn("MEDICAL", 8000, category=Category.OTHER, balance=4000))
    assert flags == {"MEDICAL": "Balance dropped 67% in one debit, from ₹12,000 to ₹4,000"}


def test_reasons_combine():
    [txn] = [t for t in detect_unusual([*EVERYDAY, _txn("RTGS-BUILDER", 500000, balance=100000)]) if t.is_unusual]
    assert txn.unusual_reason.count(";") == 3  # large debit, round, first payee, balance drop


def test_samples_against_truth(tmp_path):
    generate(tmp_path)
    false_alarms = []
    for path in sorted(tmp_path.glob("*.truth.json")):
        rows = json.loads(path.read_text())["rows"]
        txns = [Transaction(**r["transaction"], category=r["expected_category"], category_source="rule") for r in rows]
        for txn, row in zip(detect_unusual(txns), rows):
            if row["expected_unusual"]:
                assert txn.is_unusual, txn.description
            elif txn.is_unusual:
                false_alarms.append(txn.description)
    # The planted trap: two identical Swiggy orders on one day. Without times the
    # duplicate rule can't tell that from a double charge, so it asks.
    assert false_alarms == ["UPI-SWIGGY-604118229054"]


def test_helpers():
    assert payee_key("UPI/9876xxxx/RAMESH") == "UPI RAMESH"
    assert payee_key("UPI-SWIGGY-604118227731") == payee_key("UPI-SWIGGY-604118229054")
    assert format_inr(500000) == "₹5,00,000"
    assert format_inr(12345678.5) == "₹1,23,45,678.50"
