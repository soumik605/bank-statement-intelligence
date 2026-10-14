"""Stage 3: categorize transactions, rules first, then one LLM call for the rest.

Rules are instant, free and always give the same answer. Anything they can't
place confidently is left with category=None for the LLM step, which sends
all of those in one batch (see categorize_with_llm).

A keyword matches as a whole word in the uppercased description, so "RENT"
doesn't fire on "TORRENT POWER" or "CURRENT A/C". Keywords are checked in
dictionary order and the first hit wins, which is why refunds and reversals
come first: "AMAZON REFUND" is money back, not Shopping.

Each category also has a direction it makes sense in. A keyword that hits on
the wrong side ("SWIGGY" on a credit, "RENT" on a credit) is skipped rather
than trusted, because it usually means a refund or something else unusual.
"""
import json
import logging
import re
from collections.abc import Callable

from app import llm
from app.schema import Category, Transaction, TransactionType

log = logging.getLogger(__name__)

# Order is priority: the first keyword found in the description wins. Groups
# that must beat others come first; each group says why.
_GROUPS: list[tuple[Category, list[str]]] = [
    # Money coming back. First, so the merchant keyword never claims it.
    (Category.REFUND, ["REFUND", "REFD", "REVERSAL", "REVERSED", "REV", "CHARGEBACK", "CASHBACK"]),
    # Salary before generic transfer words: "SALARY TRANSFER" is salary.
    (Category.SALARY, ["SALARY", "SAL", "PAYROLL", "STIPEND"]),
    # The holder's own money. Before utilities, so "CREDIT CARD BILL PAYMENT" isn't a bill.
    (Category.TRANSFER, ["SELF", "OWN A/C", "OWN ACCOUNT", "CREDIT CARD", "CC PAYMENT", "CARD PAYMENT"]),
    # Before income, so "FIXED DEPOSIT MATURITY" is your own money back, not income.
    # (No "NACH": banks use it for SIPs and EMIs alike.)
    (Category.INVESTMENTS, [
        "SIP", "MUTUAL FUND", "MUTUAL FUNDS", "ZERODHA", "GROWW", "UPSTOX", "KUVERA", "PAYTM MONEY",
        "INDIAN CLEARING CORP", "ICCL", "CAMS", "KFINTECH", "BSE STAR MF", "NSE CLEARING", "PPF", "NPS",
        "FIXED DEPOSIT", "RECURRING DEPOSIT", "SOVEREIGN GOLD BOND",
    ]),
    (Category.INCOME, ["INTEREST", "INT PD", "INT.PD", "SB INT", "DIVIDEND", "DIV", "MATURITY", "BONUS"]),
    # Specific loan words only: a bare "LOAN" also fires on "PERSONAL LOAN" to a friend.
    (Category.LOANS, [
        "EMI", "LOAN EMI", "LOAN REPAYMENT", "LOAN RECOVERY", "LOAN INSTALLMENT", "LOAN DISBURSEMENT",
        "HOME LOAN", "CAR LOAN", "EDUCATION LOAN", "BAJAJ FINANCE", "BAJAJ FIN", "HOME CREDIT",
    ]),
    # After income, so an "INSURANCE ... MATURITY" credit is income. (No bare "PREMIUM":
    # that's YouTube Premium too.)
    (Category.INSURANCE, [
        "INSURANCE", "LIC", "LIFE INSURANCE", "HDFC LIFE", "ICICI PRU", "ICICI LOMBARD", "SBI LIFE", "MAX LIFE",
        "TATA AIA", "STAR HEALTH", "NIVA BUPA", "CARE HEALTH", "POLICYBAZAAR", "ACKO", "DIGIT INSURANCE",
    ]),
    (Category.FEES, [
        "GST", "TDS", "INCOME TAX", "ADVANCE TAX", "PROFESSIONAL TAX", "PROPERTY TAX", "CBDT", "STAMP DUTY",
        "CHARGES", "CHRG", "CHGS", "SMS CHARGES", "ANNUAL FEE", "LATE FEE", "PROCESSING FEE", "PENALTY",
        "PENAL", "MIN BAL", "NON MAINT",
    ]),
    # Before the "TRANSFER" catch-all: "RENT TRANSFER TO ..." is rent.
    (Category.RENT, ["RENT", "HOUSE RENT", "NOBROKER"]),
    (Category.UTILITIES, [
        "ELECTRICITY", "BESCOM", "MSEDCL", "KSEB", "TATA POWER", "TORRENT POWER", "ADANI ELECTRICITY", "CESC",
        "WBSEDCL", "BSES", "TPDDL", "TANGEDCO", "TNEB", "UPPCL", "PSPCL", "JVVNL", "APSPDCL",
        "WATER BILL", "GAS BILL", "MAHANAGAR GAS", "INDRAPRASTHA GAS", "IGL", "PIPED GAS", "INDANE",
        "HP GAS", "BHARAT GAS", "LPG", "BROADBAND", "FIBERNET", "ACT FIBERNET", "HATHWAY", "AIRTEL", "JIO",
        "VODAFONE", "BSNL", "TATA PLAY", "DISH TV", "D2H",
    ]),
    # Before shopping, so "AMAZON PHARMACY" is health.
    (Category.HEALTH, [
        "PHARMACY", "PHARMACIES", "MEDICAL STORE", "MEDICAL STORES", "MEDICAL HALL", "MEDICALS", "CHEMIST",
        "APOLLO", "MEDPLUS", "PHARMEASY", "NETMEDS", "1MG", "HOSPITAL", "CLINIC", "DIAGNOSTIC", "DIAGNOSTICS",
        "PATHLABS", "PATHOLOGY", "THYROCARE", "PRACTO", "DENTAL",
    ]),
    (Category.EDUCATION, [
        "SCHOOL", "COLLEGE", "UNIVERSITY", "TUITION", "COACHING", "EXAM FEE", "BYJUS", "UNACADEMY",
        "VEDANTU", "PHYSICSWALLAH", "UPGRAD", "SIMPLILEARN", "COURSERA", "UDEMY",
    ]),
    # Before shopping, so "AMAZON PRIME" is entertainment.
    (Category.ENTERTAINMENT, [
        "NETFLIX", "HOTSTAR", "JIOHOTSTAR", "DISNEY", "PRIME VIDEO", "AMAZON PRIME", "SPOTIFY", "YOUTUBE",
        "ZEE5", "SONYLIV", "JIOCINEMA", "GAANA", "WYNK", "AUDIBLE", "BOOKMYSHOW", "PVR", "INOX", "CINEPOLIS",
        "STEAM", "PLAYSTATION", "DREAM11",
    ]),
    # Before "UBER", so Uber Eats is food.
    (Category.FOOD, [
        "UBER EATS", "SWIGGY", "INSTAMART", "ZOMATO", "DUNZO", "BIGBASKET", "BLINKIT", "ZEPTO", "DMART",
        "JIOMART", "RELIANCE FRESH", "SPENCERS", "MORE RETAIL", "NATURES BASKET", "BIG BAZAAR", "STAR BAZAAR",
        "LICIOUS", "FRESHTOHOME", "COUNTRY DELIGHT", "MILKBASKET", "DAIRY", "KIRANA", "SUPERMARKET",
        "GROCERY", "GROCERIES", "DOMINOS", "DOMINO", "PIZZA HUT", "KFC", "MCDONALDS", "BURGER KING",
        "STARBUCKS", "CHAAYOS", "CAFE COFFEE DAY", "CCD", "HALDIRAM", "HALDIRAMS", "BARBEQUE NATION",
        "RESTAURANT", "CAFE", "BAKERY", "BAKERS", "SWEETS", "DHABA", "MOMO", "MOMOS",
    ]),
    # "TOTO" is an e-rickshaw. (No bare "METRO": Metro Cash & Carry is a shop.)
    (Category.TRAVEL, [
        "UBER", "OLA", "RAPIDO", "BLUSMART", "NAMMA YATRI", "TOTO", "IRCTC", "INDIANRAILWAYS",
        "INDIAN RAILWAYS", "DMRC", "BMRCL", "METRO RAIL", "REDBUS", "ABHIBUS", "MAKEMYTRIP", "GOIBIBO",
        "CLEARTRIP", "YATRA", "IXIGO", "EASEMYTRIP", "INDIGO", "AIR INDIA", "VISTARA", "SPICEJET",
        "AKASA AIR", "OYO", "AIRBNB", "FASTAG", "TOLL", "NHAI", "PARKING", "PETROL", "FUEL", "HPCL", "BPCL",
        "IOCL", "INDIAN OIL", "SHELL", "NAYARA", "FILLING STATION",
    ]),
    (Category.SHOPPING, [
        "AMAZON", "AMZN", "FLIPKART", "MYNTRA", "AJIO", "NYKAA", "MEESHO", "TATA CLIQ", "SNAPDEAL",
        "DECATHLON", "IKEA", "CROMA", "RELIANCE DIGITAL", "RELIANCE TRENDS", "VIJAY SALES", "LIFESTYLE",
        "SHOPPERS STOP", "PANTALOONS", "WESTSIDE", "MAX FASHION", "ZARA", "H&M", "UNIQLO", "LENSKART",
        "PEPPERFRY", "URBAN LADDER", "PURPLLE", "FIRSTCRY", "BEWAKOOF",
    ]),
    # Generic bill words, after every merchant: "FASTAG RECHARGE" is travel, "TATA POWER BILLPAY" is
    # already utilities, and a bare "MOBILE RECHARGE" still lands here.
    (Category.UTILITIES, ["RECHARGE", "POSTPAID", "BBPS", "BILLPAY", "BILL PAYMENT"]),
    (Category.CASH, ["ATM", "ATW", "NWD", "CDM", "CASH WDL", "CASH WITHDRAWAL", "CASH DEP", "CASH DEPOSIT"]),
    # Catch-all, last: a transfer with nothing more specific in it.
    (Category.TRANSFER, ["TRANSFER", "TRF"]),
]

KEYWORDS: dict[str, Category] = {}
for _category, _words in _GROUPS:
    for _word in _words:
        KEYWORDS.setdefault(_word, _category)  # a word listed twice keeps its first, higher-priority group


# The only direction each category can take. Categories not listed (Transfer,
# Cash, Loans & EMI, Investments, Other) go either way.
DIRECTION: dict[Category, TransactionType] = {
    Category.SALARY: "credit",
    Category.INCOME: "credit",
    Category.REFUND: "credit",
    Category.RENT: "debit",
    Category.UTILITIES: "debit",
    Category.FOOD: "debit",
    Category.TRAVEL: "debit",
    Category.SHOPPING: "debit",
    Category.HEALTH: "debit",
    Category.ENTERTAINMENT: "debit",
    Category.EDUCATION: "debit",
    Category.INSURANCE: "debit",
    Category.FEES: "debit",
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


# --- LLM fallback -------------------------------------------------------------

# Takes chat messages, returns the reply text. Swappable so tests don't call the API.
Complete = Callable[[list[dict]], str]

_CATEGORY_GUIDE = {
    Category.SALARY: "salary or wages from an employer",
    Category.INCOME: "interest, dividends, insurance or deposit maturity, other non-salary income",
    Category.REFUND: "money back from a merchant: refunds, reversals, cashback",
    Category.RENT: "house rent, including rent paid to a named landlord",
    Category.UTILITIES: "electricity, water, gas, phone, internet, recharges",
    Category.FOOD: "restaurants, food delivery, groceries, supermarkets",
    Category.TRAVEL: "cabs, trains, flights, buses, fuel, tolls",
    Category.SHOPPING: "online and retail shopping",
    Category.HEALTH: "pharmacies, medical stores, doctors, hospitals, diagnostic labs",
    Category.ENTERTAINMENT: "streaming subscriptions, movies, events, games",
    Category.EDUCATION: "school and college fees, tuition, coaching, online courses",
    Category.INSURANCE: "life, health, motor and other insurance premiums",
    Category.FEES: "bank charges, penalties, annual fees, GST, TDS, income and property tax",
    Category.LOANS: "loan EMIs and repayments (money out), loan disbursements (money in)",
    Category.INVESTMENTS: "SIPs, mutual funds, stocks, fixed and recurring deposits, PPF, NPS; "
                          "redemptions coming back in",
    Category.TRANSFER: "money to or from a person or another account: UPI/IMPS/NEFT to a name, "
                       "own accounts, gifts, loans, credit card bills, property payments",
    Category.CASH: "ATM withdrawals and cash deposits",
    Category.OTHER: "anything that fits none of the above",
}

SYSTEM_PROMPT = (
    "You categorize transactions from Indian bank statements.\n"
    "Choose exactly one category per transaction, only from this list:\n"
    + "\n".join(f"- {c.value}: {why}" for c, why in _CATEGORY_GUIDE.items())
    + "\n\nThe type field says whether money came in (credit) or went out (debit).\n"
    "Reply with only a JSON array containing every id exactly once, and nothing else: "
    "no explanation, no markdown. Example:\n"
    '[{"id": 3, "category": "Transfer"}, {"id": 7, "category": "Food"}]'
)


def categorize_with_llm(transactions: list[Transaction], complete: Complete | None = None) -> list[Transaction]:
    """Categorize every transaction that still has no category, in one LLM call.

    The reply is never trusted: anything missing, unparseable, not in the
    category list, or in the wrong direction for its category becomes Other.
    """
    pending = {i: t for i, t in enumerate(transactions) if t.category is None}
    if not pending:
        return transactions
    if complete is None:
        # Roughly 20 tokens per {"id": .., "category": ".."} entry, plus slack.
        max_tokens = 64 + 24 * len(pending)
        complete = lambda messages: llm.chat(messages, temperature=0, max_tokens=max_tokens)  # noqa: E731

    batch = [
        {"id": i, "description": t.description, "amount": t.amount, "type": t.type}
        for i, t in pending.items()
    ]
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(batch, ensure_ascii=False)},
    ]
    try:
        reply = complete(messages)
    except Exception as e:  # network, auth, rate limit: degrade, don't crash the pipeline
        log.warning("LLM categorization failed, using Other for %d transactions: %s", len(pending), e)
        reply = ""
    chosen = parse_llm_reply(reply, set(pending))

    out = list(transactions)
    for i, txn in pending.items():
        category = chosen.get(i, Category.OTHER)
        if DIRECTION.get(category, txn.type) != txn.type:
            log.warning("LLM put %s transaction %d (%r) in %s; using Other", txn.type, i, txn.description, category)
            category = Category.OTHER
        out[i] = txn.model_copy(update={"category": category, "category_source": "llm"})
    missing = set(pending) - set(chosen)
    if missing:
        log.warning("LLM gave no usable category for ids %s; using Other", sorted(missing))
    return out


_FENCE = re.compile(r"```(?:json)?", re.IGNORECASE)
_CATEGORY_BY_NAME = {c.value.lower(): c for c in Category}


def parse_llm_reply(text: str, valid_ids: set[int]) -> dict[int, Category]:
    """Pull {id: category} out of the model's reply, keeping only valid entries.

    Tolerates markdown fences and chatty text around the JSON array. Returns
    an empty dict if there's no parseable array at all.
    """
    cleaned = _FENCE.sub("", text or "")
    start, end = cleaned.find("["), cleaned.rfind("]")
    try:
        items = json.loads(cleaned[start : end + 1]) if 0 <= start < end else None
    except ValueError:
        items = None
    if not isinstance(items, list):
        log.warning("LLM reply has no JSON array: %r", (text or "")[:200])
        return {}

    chosen: dict[int, Category] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        raw_id, raw_category = item.get("id"), item.get("category")
        try:
            txn_id = int(raw_id) if not isinstance(raw_id, bool) else None
        except (TypeError, ValueError):
            txn_id = None
        if txn_id not in valid_ids or txn_id in chosen:
            continue  # unknown id, or a repeat: keep the first answer
        category = _CATEGORY_BY_NAME.get(str(raw_category).strip().lower())
        if category is None:
            log.warning("LLM returned unknown category %r for id %s; using Other", raw_category, txn_id)
            category = Category.OTHER
        chosen[txn_id] = category
    return chosen


def categorize(transactions: list[Transaction], complete: Complete | None = None) -> list[Transaction]:
    """Rules first; one LLM call for whatever they leave."""
    return categorize_with_llm(categorize_by_rules(transactions), complete)
