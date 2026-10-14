import json
from decimal import Decimal

from app.analytics import compute_analytics
from app.schema import Analytics, Category, Transaction
from scripts.generate_samples import generate


def _txn(amount, txn_type, category=None):
    extra = {"category": category, "category_source": "rule"} if category else {}
    return Transaction(date="2026-01-05", description="X", amount=amount, type=txn_type, **extra)


def test_spending_rule():
    result = compute_analytics([
        _txn(100000, "credit", Category.SALARY),
        _txn(1299, "credit", Category.REFUND),  # money back: a credit, never negative spending
        _txn(0.1, "debit", Category.FOOD),
        _txn(0.2, "debit", Category.FOOD),
        _txn(25000, "debit", Category.TRANSFER),  # own savings: not spending
        _txn(2000, "debit", Category.CASH),
        _txn(500, "debit", Category.SHOPPING),
        _txn(500, "debit"),  # uncategorized counts as Other
    ])
    assert result == Analytics(
        transaction_count=8,
        total_credits=101299,
        total_debits=28000.3,
        net_flow=73298.7,
        total_spending=3000.3,
        spending_by_category={Category.CASH: 2000, Category.OTHER: 500, Category.SHOPPING: 500, Category.FOOD: 0.3},
        # Other and Shopping tie at 500; ties go alphabetically.
        top_spending_categories=[Category.CASH, Category.OTHER, Category.SHOPPING],
    )
    assert result.spending_by_category[Category.FOOD] == 0.3  # exact, not 0.30000000000000004


def test_empty():
    result = compute_analytics([])
    assert (result.transaction_count, result.total_credits, result.net_flow) == (0, 0, 0)
    assert result.spending_by_category == {} and result.top_spending_categories == []


def test_net_flow_matches_balance_change(tmp_path):
    generate(tmp_path)
    for path in tmp_path.glob("*.truth.json"):
        truth = json.loads(path.read_text())
        txns = [Transaction(**r["transaction"], category=r["expected_category"], category_source="rule")
                for r in truth["rows"]]
        result = compute_analytics(txns)
        change = Decimal(str(truth["closing_balance"])) - Decimal(str(truth["opening_balance"]))
        assert Decimal(str(result.net_flow)) == change
        assert result.transaction_count == len(txns)
        assert Category.TRANSFER not in result.spending_by_category
        assert round(sum(result.spending_by_category.values()), 2) == result.total_spending


def test_investments_are_not_spending():
    result = compute_analytics([
        _txn(10000, "debit", Category.INVESTMENTS),  # a SIP
        _txn(3000, "debit", Category.LOANS),  # an EMI is still money spent
        _txn(500, "debit", Category.FEES),
    ])
    assert result.total_debits == 13500
    assert result.total_spending == 3500
    assert Category.INVESTMENTS not in result.spending_by_category
