"""Stage 4: summary numbers for a statement, computed with plain arithmetic.

Spending rule:
- Spending is debits only, minus Transfer and Investments. Transfers move money
  to people or to other accounts, including the holder's own savings and credit
  card, and investments set money aside; counting either would overstate spending.
- Cash withdrawals count as spending: the money left the account to be spent.
- Refunds are credits, so they're in total_credits and never reduce spending.
  A refund can't be reliably matched to the purchase it reverses.
- A debit with no category counts as Other.

All sums are done in integer paise, so there is no float rounding drift.
"""
import pandas as pd

from app.schema import NON_SPENDING_CATEGORIES, Analytics, Category, Transaction


def compute_analytics(transactions: list[Transaction]) -> Analytics:
    df = pd.DataFrame(
        {
            "type": [t.type for t in transactions],
            "paise": [round(t.amount * 100) for t in transactions],
            "category": [t.category or Category.OTHER for t in transactions],
        },
        columns=["type", "paise", "category"],
    ).astype({"paise": "int64"})

    credits = int(df.loc[df["type"] == "credit", "paise"].sum())
    debits = int(df.loc[df["type"] == "debit", "paise"].sum())

    spending = df[(df["type"] == "debit") & ~df["category"].isin(NON_SPENDING_CATEGORIES)]
    by_category = spending.groupby("category")["paise"].sum()
    # Highest first; ties broken by name so the order never changes between runs.
    ranked = sorted(by_category.items(), key=lambda kv: (-kv[1], kv[0].value))

    return Analytics(
        transaction_count=len(df),
        total_credits=credits / 100,
        total_debits=debits / 100,
        net_flow=(credits - debits) / 100,
        total_spending=int(spending["paise"].sum()) / 100,
        spending_by_category={category: int(paise) / 100 for category, paise in ranked},
        top_spending_categories=[category for category, _ in ranked[:3]],
    )
