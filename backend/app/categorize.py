"""Stage 3a: categorize the obvious transactions with keyword rules.

Rules are instant, free and always give the same answer. Anything they can't
place confidently is left with category=None for the LLM step.

A keyword matches as a whole word in the uppercased description, so "RENT"
doesn't fire on "TORRENT POWER" or "CURRENT A/C". Keywords are checked in
dictionary order and the first hit wins, which is why refunds and reversals
come first: "AMAZON REFUND" is money back, not Shopping.

Each category also has a direction it makes sense in. A keyword that hits on
the wrong side ("SWIGGY" on a credit, "RENT" on a credit) is skipped rather
than trusted, because it usually means a refund or something else unusual.
"""
import re

from app.schema import Category, Transaction, TransactionType

# Order is priority: the first keyword found in the description wins.
KEYWORDS: dict[str, Category] = {
    # Money coming back. Checked first so the merchant keyword below never claims it.
    "REFUND": Category.REFUND,
    "REFD": Category.REFUND,
    "REVERSAL": Category.REFUND,
    "REVERSED": Category.REFUND,
    "REV": Category.REFUND,
    "CHARGEBACK": Category.REFUND,
    "CASHBACK": Category.REFUND,
    # Salary before generic transfer words: "SALARY TRANSFER" is salary.
    "SALARY": Category.SALARY,
    "SAL": Category.SALARY,
    "PAYROLL": Category.SALARY,
    # Moving the holder's own money. Before utilities so "CREDIT CARD BILL PAYMENT" isn't a bill.
    "SELF": Category.TRANSFER,
    "OWN A/C": Category.TRANSFER,
    "OWN ACCOUNT": Category.TRANSFER,
    "CREDIT CARD": Category.TRANSFER,
    "CC PAYMENT": Category.TRANSFER,
    # Other income.
    "INTEREST": Category.INCOME,
    "INT PD": Category.INCOME,
    "DIVIDEND": Category.INCOME,
    "MATURITY": Category.INCOME,
    # Before the "TRANSFER" catch-all: "RENT TRANSFER TO ..." is rent.
    "RENT": Category.RENT,
    "ELECTRICITY": Category.UTILITIES,
    "BESCOM": Category.UTILITIES,
    "MSEDCL": Category.UTILITIES,
    "KSEB": Category.UTILITIES,
    "TATA POWER": Category.UTILITIES,
    "TORRENT POWER": Category.UTILITIES,
    "ADANI ELECTRICITY": Category.UTILITIES,
    "WATER BILL": Category.UTILITIES,
    "GAS BILL": Category.UTILITIES,
    "BROADBAND": Category.UTILITIES,
    "RECHARGE": Category.UTILITIES,
    "BBPS": Category.UTILITIES,
    "BILLPAY": Category.UTILITIES,
    "BILL PAYMENT": Category.UTILITIES,
    # Before "UBER" so Uber Eats is food, not travel.
    "UBER EATS": Category.FOOD,
    "SWIGGY": Category.FOOD,
    "INSTAMART": Category.FOOD,
    "ZOMATO": Category.FOOD,
    "BIGBASKET": Category.FOOD,
    "BLINKIT": Category.FOOD,
    "ZEPTO": Category.FOOD,
    "DMART": Category.FOOD,
    "RELIANCE FRESH": Category.FOOD,
    "GROCERY": Category.FOOD,
    "DOMINOS": Category.FOOD,
    "MCDONALDS": Category.FOOD,
    "STARBUCKS": Category.FOOD,
    "UBER": Category.TRAVEL,
    "OLA": Category.TRAVEL,
    "RAPIDO": Category.TRAVEL,
    "IRCTC": Category.TRAVEL,
    "INDIGO": Category.TRAVEL,
    "MAKEMYTRIP": Category.TRAVEL,
    "REDBUS": Category.TRAVEL,
    "FASTAG": Category.TRAVEL,
    "PETROL": Category.TRAVEL,
    "FUEL": Category.TRAVEL,
    "HPCL": Category.TRAVEL,
    "BPCL": Category.TRAVEL,
    "IOCL": Category.TRAVEL,
    "AMAZON": Category.SHOPPING,
    "AMZN": Category.SHOPPING,
    "FLIPKART": Category.SHOPPING,
    "MYNTRA": Category.SHOPPING,
    "AJIO": Category.SHOPPING,
    "NYKAA": Category.SHOPPING,
    "MEESHO": Category.SHOPPING,
    "ATM": Category.CASH,
    "ATW": Category.CASH,
    "CASH WDL": Category.CASH,
    "CASH WITHDRAWAL": Category.CASH,
    "CASH DEP": Category.CASH,
    "CASH DEPOSIT": Category.CASH,
    # Catch-all, last: a transfer with nothing more specific in it.
    "TRANSFER": Category.TRANSFER,
    "TRF": Category.TRANSFER,
}

# The only direction each category can take. Categories not listed (Transfer,
# Cash, Other) go either way.
DIRECTION: dict[Category, TransactionType] = {
    Category.SALARY: "credit",
    Category.INCOME: "credit",
    Category.REFUND: "credit",
    Category.RENT: "debit",
    Category.UTILITIES: "debit",
    Category.FOOD: "debit",
    Category.TRAVEL: "debit",
    Category.SHOPPING: "debit",
}

# Whole-word match: the keyword can't have a letter or digit right before or after it.
_PATTERNS = [
    (re.compile(rf"(?<![A-Z0-9]){re.escape(kw)}(?![A-Z0-9])"), category) for kw, category in KEYWORDS.items()
]


def match_rule(description: str, txn_type: TransactionType) -> Category | None:
    """The category for this description, or None if no rule applies."""
    text = " ".join(description.upper().split())
    for pattern, category in _PATTERNS:
        if pattern.search(text) and DIRECTION.get(category, txn_type) == txn_type:
            return category
    return None


def categorize_by_rules(transactions: list[Transaction]) -> list[Transaction]:
    """Fill in category with source "rule" wherever a rule matches. Transactions
    that already have a category, or that no rule matches, are returned unchanged."""
    out = []
    for txn in transactions:
        category = None if txn.category is not None else match_rule(txn.description, txn.type)
        out.append(txn.model_copy(update={"category": category, "category_source": "rule"}) if category else txn)
    return out
