# backend/app/merchants.py
"""Catalogue of common Indian merchants, grouped by category.

Used to categorise transactions (match_merchant), flag unusual amounts, and
generate realistic sample statements. Amount ranges are typical per-transaction
values in INR, not hard limits.
"""
from dataclasses import dataclass

from app.schema import Category, Direction


@dataclass(frozen=True)
class Merchant:
    name: str
    category: Category
    aliases: tuple[str, ...]  # Strings that appear in bank narrations
    amount_range: tuple[float, float]  # Typical (low, high) in INR
    direction: Direction = "debit"


MERCHANTS: list[Merchant] = [
    # --- Income ---
    Merchant("Salary", Category.INCOME, ("SALARY", "SAL CREDIT", "SAL CR"), (25_000, 250_000), "credit"),
    Merchant("Savings interest", Category.INCOME, ("INT PD", "INTEREST", "INT CREDIT"), (10, 5_000), "credit"),
    Merchant("Dividend", Category.INCOME, ("DIVIDEND", "DIV"), (50, 20_000), "credit"),
    Merchant("Refund", Category.INCOME, ("REFUND", "REVERSAL", "CASHBACK"), (20, 10_000), "credit"),

    # --- Transfer (own accounts, card bills, investments) ---
    Merchant("CRED", Category.TRANSFER, ("CRED", "CRED CLUB"), (1_000, 100_000)),
    Merchant("Zerodha", Category.TRANSFER, ("ZERODHA",), (500, 50_000)),
    Merchant("Groww", Category.TRANSFER, ("GROWW",), (500, 50_000)),
    Merchant("Upstox", Category.TRANSFER, ("UPSTOX",), (500, 50_000)),

    # --- Bills ---
    Merchant("BESCOM", Category.BILLS, ("BESCOM",), (300, 4_000)),
    Merchant("Tata Power", Category.BILLS, ("TATA POWER", "TPDDL"), (400, 5_000)),
    Merchant("Adani Electricity", Category.BILLS, ("ADANI ELECTRICITY", "AEML"), (400, 5_000)),
    Merchant("MSEDCL", Category.BILLS, ("MSEDCL", "MAHAVITARAN"), (300, 4_000)),
    Merchant("BWSSB", Category.BILLS, ("BWSSB",), (200, 1_500)),
    Merchant("Indane Gas", Category.BILLS, ("INDANE",), (800, 1_100)),
    Merchant("HP Gas", Category.BILLS, ("HP GAS",), (800, 1_100)),
    Merchant("Jio", Category.BILLS, ("JIO", "RELIANCE JIO"), (199, 999)),
    Merchant("Airtel", Category.BILLS, ("AIRTEL", "BHARTI AIRTEL"), (199, 1_499)),
    Merchant("Vi", Category.BILLS, ("VODAFONE IDEA", "VODAFONE", "VI PREPAID", "VI POSTPAID"), (199, 999)),
    Merchant("ACT Fibernet", Category.BILLS, ("ACT FIBERNET", "ATRIA CONVERGENCE"), (600, 1_500)),
    Merchant("Tata Play", Category.BILLS, ("TATA PLAY", "TATASKY", "TATA SKY"), (250, 700)),
    Merchant("Netflix", Category.BILLS, ("NETFLIX",), (149, 649)),
    Merchant("Spotify", Category.BILLS, ("SPOTIFY",), (119, 199)),
    Merchant("Amazon Prime", Category.BILLS, ("AMAZON PRIME", "PRIME VIDEO"), (299, 1_499)),
    Merchant("JioHotstar", Category.BILLS, ("JIOHOTSTAR", "JIO HOTSTAR", "HOTSTAR"), (149, 1_499)),
    Merchant("LIC", Category.BILLS, ("LIC", "LIFE INSURANCE CORP"), (1_000, 25_000)),
    Merchant("Bajaj Finserv EMI", Category.BILLS, ("BAJAJ FINSERV", "BAJAJ FINANCE", "BAJAJ FIN", "EMI"), (1_000, 15_000)),
    Merchant("NoBroker Rent", Category.BILLS, ("NOBROKER", "RENT"), (8_000, 60_000)),

    # --- Food (delivery, restaurants, groceries) ---
    Merchant("Swiggy", Category.FOOD, ("SWIGGY",), (150, 1_200)),
    Merchant("Swiggy Instamart", Category.FOOD, ("SWIGGY INSTAMART", "INSTAMART"), (200, 2_500)),
    Merchant("Zomato", Category.FOOD, ("ZOMATO",), (150, 1_200)),
    Merchant("Blinkit", Category.FOOD, ("BLINKIT", "GROFERS"), (100, 2_500)),
    Merchant("Zepto", Category.FOOD, ("ZEPTO", "KIRANAKART"), (100, 2_000)),
    Merchant("BigBasket", Category.FOOD, ("BIGBASKET", "BIG BASKET", "SUPERMARKET GROCERY"), (300, 4_000)),
    Merchant("DMart", Category.FOOD, ("DMART", "AVENUE SUPERMARTS"), (500, 5_000)),
    Merchant("JioMart", Category.FOOD, ("JIOMART",), (200, 3_000)),
    Merchant("Domino's", Category.FOOD, ("DOMINOS", "JUBILANT FOODWORKS"), (200, 1_000)),
    Merchant("McDonald's", Category.FOOD, ("MCDONALDS", "HARDCASTLE", "CONNAUGHT PLAZA"), (150, 700)),
    Merchant("Starbucks", Category.FOOD, ("STARBUCKS", "TATA STARBUCKS"), (250, 900)),

    # --- Shopping ---
    Merchant("Amazon", Category.SHOPPING, ("AMAZON", "AMZN", "AMAZON PAY"), (200, 10_000)),
    Merchant("Flipkart", Category.SHOPPING, ("FLIPKART", "FKRT"), (200, 10_000)),
    Merchant("Myntra", Category.SHOPPING, ("MYNTRA",), (400, 5_000)),
    Merchant("Ajio", Category.SHOPPING, ("AJIO",), (400, 4_000)),
    Merchant("Nykaa", Category.SHOPPING, ("NYKAA", "FSN ECOMMERCE"), (300, 3_000)),
    Merchant("Meesho", Category.SHOPPING, ("MEESHO",), (150, 1_500)),
    Merchant("Croma", Category.SHOPPING, ("CROMA", "INFINITI RETAIL"), (1_000, 60_000)),
    Merchant("Reliance Digital", Category.SHOPPING, ("RELIANCE DIGITAL",), (1_000, 60_000)),
    Merchant("Decathlon", Category.SHOPPING, ("DECATHLON",), (500, 6_000)),
    Merchant("IKEA", Category.SHOPPING, ("IKEA",), (500, 15_000)),

    # --- Transport (cabs, fuel, rail, air) ---
    Merchant("Uber", Category.TRANSPORT, ("UBER",), (80, 900)),
    Merchant("Ola", Category.TRANSPORT, ("OLA", "OLACABS", "ANI TECHNOLOGIES"), (80, 900)),
    Merchant("Rapido", Category.TRANSPORT, ("RAPIDO", "ROPPEN"), (30, 300)),
    Merchant("IRCTC", Category.TRANSPORT, ("IRCTC",), (200, 5_000)),
    Merchant("Namma Metro", Category.TRANSPORT, ("NAMMA METRO", "BMRCL"), (100, 2_000)),
    Merchant("FASTag", Category.TRANSPORT, ("FASTAG", "NETC"), (200, 2_000)),
    Merchant("Indian Oil", Category.TRANSPORT, ("INDIAN OIL", "IOCL"), (300, 4_000)),
    Merchant("Bharat Petroleum", Category.TRANSPORT, ("BHARAT PETROLEUM", "BPCL"), (300, 4_000)),
    Merchant("HP Petrol", Category.TRANSPORT, ("HPCL", "HINDUSTAN PETROLEUM"), (300, 4_000)),
    Merchant("RedBus", Category.TRANSPORT, ("REDBUS",), (400, 2_500)),
    # Not "MMT": ICICI prefixes IMPS narrations with MMT/IMPS.
    Merchant("MakeMyTrip", Category.TRANSPORT, ("MAKEMYTRIP",), (1_500, 25_000)),
    Merchant("IndiGo", Category.TRANSPORT, ("INDIGO", "INTERGLOBE AVIATION"), (2_500, 15_000)),

    # --- Cash ---
    Merchant("ATM withdrawal", Category.CASH, ("ATM", "ATW", "NWD", "NFS", "CASH WDL", "CASH WITHDRAWAL"), (500, 10_000)),
    Merchant("Cash deposit", Category.CASH, ("CASH DEP", "CASH DEPOSIT", "BY CASH", "CDM"), (1_000, 50_000), "credit"),

    # --- Other (health, entertainment) ---
    Merchant("Apollo Pharmacy", Category.OTHER, ("APOLLO PHARMACY", "APOLLO"), (100, 3_000)),
    Merchant("Tata 1mg", Category.OTHER, ("1MG", "TATA 1MG"), (100, 3_000)),
    Merchant("PharmEasy", Category.OTHER, ("PHARMEASY",), (100, 3_000)),
    Merchant("Practo", Category.OTHER, ("PRACTO",), (300, 1_500)),
    Merchant("BookMyShow", Category.OTHER, ("BOOKMYSHOW", "BIGTREE"), (200, 2_000)),
    Merchant("PVR INOX", Category.OTHER, ("PVR", "INOX"), (200, 2_000)),
]

MERCHANTS_BY_CATEGORY: dict[Category, list[Merchant]] = {
    category: [m for m in MERCHANTS if m.category == category] for category in Category
}


def normalize(text: str) -> str:
    """Uppercase and collapse punctuation to single spaces: 'UPI/DR/SWIGGY@ybl' -> 'UPI DR SWIGGY YBL'."""
    return " ".join("".join(ch if ch.isalnum() else " " for ch in text.upper()).split())


# Longest alias first, so "AMAZON PRIME" wins over "AMAZON" and "JIO HOTSTAR" over "JIO".
_ALIASES: list[tuple[str, Merchant]] = sorted(
    ((normalize(alias), m) for m in MERCHANTS for alias in m.aliases),
    key=lambda pair: len(pair[0]),
    reverse=True,
)


def match_merchant(description: str) -> Merchant | None:
    """Find the merchant whose alias appears as whole words in a transaction description."""
    text = f" {normalize(description)} "
    for alias, merchant in _ALIASES:
        if f" {alias} " in text:
            return merchant
    return None
