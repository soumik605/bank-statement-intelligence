# backend/app/simulator.py
"""Generate synthetic bank statements for a persona.

Output is a PipelinePayload whose transaction categories are ground truth, so the
same file can drive the frontend, demos and evaluation of the categoriser.
"""
import datetime as dt
import random
from calendar import monthrange
from dataclasses import dataclass

from faker import Faker

from app.merchants import MERCHANTS, Merchant
from app.narrations import Bank, Narration, Narrator
from app.schema import Category, Direction, PipelinePayload, StatementInfo, Transaction

_MERCHANT = {m.name: m for m in MERCHANTS}
_UPI_HANDLES = ("ybl", "axl", "icici", "hdfcbank", "paytm")
_PERSON_HANDLES = ("okaxis", "oksbi", "okicici", "okhdfcbank", "ybl")
_FRIEND_NOTES = ("DINNER SPLIT", "CAB SHARE", "MOVIE TICKETS", "TRIP SHARE", "BIRTHDAY GIFT", "LUNCH")
_CITY = "BENGALURU"


@dataclass(frozen=True)
class SpendPattern:
    merchants: tuple[str, ...]  # Names from the merchant catalogue
    per_month: tuple[int, int]  # Min and max transactions per month
    weights: tuple[float, ...] | None = None  # Relative frequency of each merchant
    channel: str = "upi"  # "upi", "card" (in store) or "online" (card, e-commerce)


@dataclass(frozen=True)
class Persona:
    key: str
    account_number: str
    opening_balance: float
    employer: str
    salary: float  # Credited on the 1st
    rent: float  # Paid by UPI to the landlord on the 5th
    self_account: str  # Own account at another bank
    self_transfer: float
    bills: tuple[tuple[str, int, float | None], ...]  # (merchant, day, fixed amount or None = variable)
    bills_per_month: tuple[int, int]  # Takes the first n bills each month
    spending: tuple[SpendPattern, ...]
    friend_transfers: tuple[int, int]
    friend_count: int = 4


SALARIED_PROFESSIONAL = Persona(
    key="salaried_professional",
    account_number="XXXXXXXX4821",
    opening_balance=40_000,
    employer="ACME TECH",
    salary=85_000,
    rent=22_000,
    self_account="SBI XXXX5678",
    self_transfer=10_000,
    bills=(
        ("BESCOM", 10, None),
        ("Jio", 15, 349),
        ("BWSSB", 12, None),
    ),
    bills_per_month=(2, 3),
    spending=(
        SpendPattern(  # Food orders
            ("Swiggy", "Zomato", "Swiggy Instamart", "Blinkit", "Zepto", "BigBasket", "Domino's", "Starbucks"),
            (15, 25),
            (30, 30, 8, 10, 10, 5, 4, 3),
        ),
        SpendPattern(("Amazon", "Flipkart", "Myntra", "Ajio", "Meesho", "Nykaa"), (4, 8),
                     (30, 25, 15, 10, 10, 10), channel="online"),
        SpendPattern(  # Travel
            ("Uber", "Ola", "Rapido", "Namma Metro", "IRCTC", "RedBus", "IndiGo"),
            (3, 6),
            (30, 20, 15, 15, 8, 7, 5),
        ),
    ),
    friend_transfers=(2, 4),
)


@dataclass(frozen=True)
class People:
    holder: str
    landlord: tuple[str, str]  # (name, UPI id)
    friends: tuple[tuple[str, str], ...]


def _make_people(fake: Faker, rng: random.Random, friend_count: int) -> People:
    def with_vpa(name: str) -> tuple[str, str]:
        first, *rest = name.lower().split()
        handle = f"{first}.{rest[-1]}" if rest else first
        return name.upper(), f"{handle}@{rng.choice(_PERSON_HANDLES)}"

    return People(
        holder=fake.name().upper(),
        landlord=with_vpa(fake.name()),
        friends=tuple(with_vpa(fake.name()) for _ in range(friend_count)),
    )


@dataclass
class _Event:
    date: dt.date
    description: str
    amount: float
    direction: Direction
    category: Category
    reference: str
    essential: bool = True  # Discretionary debits are skipped if they would overdraw the account
    priority: int = 1  # Lower runs first on the same day


def _event(date: dt.date, narration: Narration, amount: float, direction: Direction, category: Category,
           essential: bool = True, priority: int = 1) -> _Event:
    return _Event(date, narration.text, amount, direction, category, narration.reference, essential, priority)


class _Generator:
    def __init__(self, persona: Persona, people: People, narrator: Narrator, rng: random.Random):
        self.p = persona
        self.people = people
        self.n = narrator
        self.rng = rng

    def day(self, year: int, month: int) -> dt.date:
        return dt.date(year, month, self.rng.randint(1, monthrange(year, month)[1]))

    def amount(self, merchant: Merchant) -> float:
        # Skewed towards the low end: most orders are small, a few are large.
        low, high = merchant.amount_range
        return round(self.rng.triangular(low, high, low + (high - low) * 0.25), 2)

    def merchant_event(self, merchant: Merchant, date: dt.date, amount: float, channel: str,
                       essential: bool = False) -> _Event:
        payee = merchant.aliases[0]
        if channel in ("card", "online"):
            narration = self.n.card(payee, _CITY, online=channel == "online")
        else:
            vpa = f"{payee.lower().replace(' ', '')}@{self.rng.choice(_UPI_HANDLES)}"
            narration = self.n.upi(payee, vpa)
        return _event(date, narration, amount, merchant.direction, merchant.category, essential)

    def month(self, year: int, month: int) -> list[_Event]:
        """One month of events: fixed obligations on set days, everything else on random days."""
        p, people, n, rng = self.p, self.people, self.n, self.rng
        mon = dt.date(year, month, 1).strftime("%b").upper()
        month_start = dt.date(year, month, 1)
        month_end = dt.date(year, month, monthrange(year, month)[1])
        events: list[_Event] = []

        events.append(_event(month_start, n.neft(p.employer, "credit", note=f"SALARY {mon} {year}"),
                             p.salary, "credit", Category.INCOME, priority=0))

        landlord, landlord_vpa = people.landlord
        events.append(_event(dt.date(year, month, 5),
                             n.upi(landlord, landlord_vpa, note=f"HOUSE RENT {mon}", merchant=False),
                             p.rent, "debit", Category.BILLS))

        events.append(_event(self.day(year, month), n.imps(people.holder, note="SELF", own_account=True),
                             p.self_transfer, "debit", Category.TRANSFER))

        for name, day, fixed in p.bills[:rng.randint(*p.bills_per_month)]:
            merchant = _MERCHANT[name]
            amount = fixed if fixed is not None else self.amount(merchant)
            events.append(self.merchant_event(merchant, dt.date(year, month, day), amount, "upi", essential=True))

        for pattern in p.spending:
            names = rng.choices(pattern.merchants, pattern.weights, k=rng.randint(*pattern.per_month))
            for name in names:
                merchant = _MERCHANT[name]
                events.append(self.merchant_event(merchant, self.day(year, month), self.amount(merchant), pattern.channel))

        # Friends: mostly UPI, sometimes IMPS.
        for _ in range(rng.randint(*p.friend_transfers)):
            name, vpa = rng.choice(people.friends)
            note = rng.choice(_FRIEND_NOTES)
            narration = n.upi(name, vpa, note=note, merchant=False) if rng.random() < 0.75 else n.imps(name, note=note)
            events.append(_event(self.day(year, month), narration, float(rng.randrange(200, 3_001, 50)),
                                 "debit", Category.TRANSFER, essential=False))

        events.append(_event(self.day(year, month), n.atm(_CITY), float(rng.randrange(500, 5_001, 500)),
                             "debit", Category.CASH, essential=False))

        events.append(_event(month_end, n.interest(month_start, month_end), round(rng.uniform(80, 250), 2),
                             "credit", Category.INCOME, priority=2))
        return events


def simulate(persona: Persona = SALARIED_PROFESSIONAL, start: dt.date = dt.date(2026, 7, 1),
             months: int = 3, seed: int = 42, bank: Bank = "sbi") -> PipelinePayload:
    """Simulate `months` full calendar months starting from `start`'s month, in `bank`'s narration style."""
    rng = random.Random(seed)
    fake = Faker("en_IN")
    fake.seed_instance(seed)
    people = _make_people(fake, rng, persona.friend_count)
    gen = _Generator(persona, people, Narrator(bank, rng), rng)

    events: list[_Event] = []
    year, month = start.year, start.month
    for _ in range(months):
        events += gen.month(year, month)
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)

    # Stable sort keeps generation order within a day, after priority.
    events.sort(key=lambda e: (e.date, e.priority))

    balance = persona.opening_balance
    transactions: list[Transaction] = []
    for e in events:
        signed = e.amount if e.direction == "credit" else -e.amount
        if not e.essential and balance + signed < 0:
            continue
        balance = round(balance + signed, 2)
        transactions.append(Transaction(date=e.date, description=e.description, amount=e.amount,
                                        direction=e.direction, balance=balance, category=e.category,
                                        reference=e.reference))

    period_start = dt.date(start.year, start.month, 1)
    last_year, last_month = (year - 1, 12) if month == 1 else (year, month - 1)
    period_end = dt.date(last_year, last_month, monthrange(last_year, last_month)[1])

    return PipelinePayload(
        statement=StatementInfo(
            bank_name="Sample Bank",
            account_holder=people.holder,
            account_number=persona.account_number,
            currency="INR",
            period_start=period_start,
            period_end=period_end,
            opening_balance=persona.opening_balance,
            closing_balance=balance,
        ),
        transactions=transactions,
        meta={"source": "simulated", "persona": persona.key, "seed": seed, "narration_style": bank},
    )
