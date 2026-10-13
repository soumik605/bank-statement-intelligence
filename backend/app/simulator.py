# backend/app/simulator.py
"""Generate synthetic bank statements for a persona.

Output is a PipelinePayload whose transaction categories are ground truth, so the
same file can drive the frontend, demos and evaluation of the categoriser.

Every month also gets deliberately ambiguous rows (rent to a person, a bill paid
through Amazon Pay, a refund, ...). Each is listed in meta["ambiguous_rows"] with
the label chosen and why.
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
_FRIEND_NOTES = ("CAB SHARE", "MOVIE TICKETS", "TRIP SHARE", "BIRTHDAY GIFT", "LUNCH")
_CITY = "BENGALURU"


@dataclass(frozen=True)
class SpendPattern:
    merchants: tuple[str, ...]  # Names from the merchant catalogue
    per_month: tuple[int, int]  # Min and max transactions per month
    weights: tuple[float, ...] | None = None  # Relative frequency of each merchant
    channel: str = "upi"  # "upi", "card" (in store) or "online" (card, e-commerce)


@dataclass(frozen=True)
class Gateway:
    """A purchase that only shows up as the payment gateway's name."""
    name: str
    vpa: str
    purpose: str
    category: Category
    amount_range: tuple[float, float]


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
    bills_via_amazon_pay: tuple[str, ...]  # Bills paid through Amazon Pay instead of the biller
    loan: tuple[str, int, float]  # (lender, day, EMI) collected by NACH
    card_bill: tuple[int, tuple[float, float]]  # (day, amount range) paid through CRED
    gateways: tuple[Gateway, ...]  # One per month
    spending: tuple[SpendPattern, ...]
    friend_transfers: tuple[int, int]
    friend_count: int = 4


SALARIED_PROFESSIONAL = Persona(
    key="salaried_professional",
    account_number="XXXXXXXX4821",
    opening_balance=60_000,
    employer="ACME TECH",
    salary=85_000,
    rent=22_000,
    self_account="SBI XXXX5678",
    self_transfer=5_000,
    bills=(
        ("BESCOM", 10, None),
        ("Jio", 15, 349),
        ("BWSSB", 12, None),
    ),
    bills_per_month=(2, 3),
    bills_via_amazon_pay=("BESCOM",),
    loan=("BAJAJ FINANCE LTD", 7, 2_999),
    card_bill=(18, (2_000, 5_000)),
    gateways=(
        Gateway("RAZORPAY", "razorpay@icici", "cult.fit gym membership", Category.OTHER, (999, 2_499)),
        Gateway("PAYU", "payu@hdfcbank", "Star Health insurance premium", Category.BILLS, (1_200, 2_200)),
        Gateway("RAZORPAY", "razorpay@icici", "Unacademy course subscription", Category.OTHER, (799, 1_999)),
    ),
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


@dataclass(frozen=True)
class Ambiguity:
    type: str
    reason: str
    acceptable: tuple[Category, ...] = ()  # Other labels an evaluator may accept


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
    ambiguity: Ambiguity | None = None
    merchant: Merchant | None = None


def _event(date: dt.date, narration: Narration, amount: float, direction: Direction, category: Category,
           essential: bool = True, priority: int = 1, ambiguity: Ambiguity | None = None) -> _Event:
    return _Event(date, narration.text, amount, direction, category, narration.reference, essential, priority,
                  ambiguity)


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
        event = _event(date, narration, amount, merchant.direction, merchant.category, essential)
        event.merchant = merchant
        return event

    def month(self, year: int, month: int) -> list[_Event]:
        """One month of events: fixed obligations on set days, everything else on random days."""
        p, people, n, rng = self.p, self.people, self.n, self.rng
        mon = dt.date(year, month, 1).strftime("%b").upper()
        month_start = dt.date(year, month, 1)
        month_end = dt.date(year, month, monthrange(year, month)[1])
        events: list[_Event] = []

        events.append(_event(month_start, n.neft(p.employer, "credit", note=f"SALARY {mon} {year}"),
                             p.salary, "credit", Category.INCOME, priority=0))

        events.append(_event(self.day(year, month), n.imps(people.holder, note="SELF", own_account=True),
                             p.self_transfer, "debit", Category.TRANSFER))

        for name, day, fixed in p.bills[:rng.randint(*p.bills_per_month)]:
            merchant = _MERCHANT[name]
            amount = fixed if fixed is not None else self.amount(merchant)
            if name not in p.bills_via_amazon_pay:
                events.append(self.merchant_event(merchant, dt.date(year, month, day), amount, "upi", essential=True))
                continue
            # Ambiguous: a utility bill paid through a shopping brand's wallet.
            events.append(_event(
                dt.date(year, month, day), n.upi("AMAZON PAY", "amazonpay@apl"), amount, "debit", Category.BILLS,
                ambiguity=Ambiguity(
                    "wallet_bill_payment",
                    f"{merchant.name} bill paid through Amazon Pay. Recurs around day {day} with a utility-sized "
                    "amount, so it is labelled bills. Matching on the merchant name 'AMAZON' would wrongly say "
                    "shopping.",
                ),
            ))

        for pattern in p.spending:
            names = rng.choices(pattern.merchants, pattern.weights, k=rng.randint(*pattern.per_month))
            for name in names:
                merchant = _MERCHANT[name]
                events.append(self.merchant_event(merchant, self.day(year, month), self.amount(merchant), pattern.channel))

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

        events += self.ambiguous(year, month, events)
        return events

    def ambiguous(self, year: int, month: int, events: list[_Event]) -> list[_Event]:
        """Rows that are hard to categorise from the narration alone. Never skipped for low balance."""
        p, people, n, rng = self.p, self.people, self.n, self.rng
        month_end = dt.date(year, month, monthrange(year, month)[1])
        out: list[_Event] = []

        landlord, landlord_vpa = people.landlord
        out.append(_event(
            dt.date(year, month, 5), n.upi(landlord, landlord_vpa, merchant=False), p.rent, "debit", Category.BILLS,
            ambiguity=Ambiguity(
                "rent_to_person",
                f"Rent: the same Rs {p.rent:,.0f} paid to the same person on the 5th of every month. The narration "
                "has only a person's name and no 'rent', so it looks like a transfer to a friend. The repeating "
                "amount, date and payee are the only clues. Rent counts as bills under CONVENTIONS.",
            ),
        ))

        instamart = _MERCHANT["Swiggy Instamart"]
        row = self.merchant_event(instamart, self.day(year, month), self.amount(instamart), "upi", essential=True)
        row.ambiguity = Ambiguity(
            "quick_commerce_grocery",
            "Swiggy Instamart delivers groceries; it is not a restaurant order. The food category includes "
            "groceries, so the label is food. 'Swiggy' suggests eating out, and quick-commerce can be mistaken "
            "for shopping.",
        )
        out.append(row)

        shopping = [e for e in events if e.category == Category.SHOPPING and e.date.day <= month_end.day - 3]
        if shopping:
            original = rng.choice(shopping)
            merchant = original.merchant
            amount, refund_date = original.amount, original.date + dt.timedelta(days=rng.randint(3, 7))
        else:
            merchant = _MERCHANT["Flipkart"]
            amount, refund_date = self.amount(merchant), self.day(year, month)
        payee = merchant.aliases[0]
        out.append(_event(
            min(refund_date, month_end), n.upi(payee, f"{payee.lower()}@axl", direction="credit"), amount,
            "credit", Category.INCOME,
            ambiguity=Ambiguity(
                "merchant_refund",
                f"Credit from {merchant.name} for the same amount as an earlier purchase: a refund. Refunds count "
                "as income under CONVENTIONS. The narration names a shopping merchant and does not say 'refund', "
                "so only the credit direction shows it is not a purchase.",
            ),
        ))

        lender, day, emi = p.loan
        out.append(_event(
            dt.date(year, month, day), n.nach(lender), emi, "debit", Category.BILLS,
            ambiguity=Ambiguity(
                "loan_emi_nach",
                f"NACH auto-debit to {lender} for the same EMI every month. Loan EMIs count as bills under "
                "CONVENTIONS. NACH is also used for SIPs and insurance, so the lender's name is what identifies "
                "a loan.",
            ),
        ))

        day, (low, high) = p.card_bill
        out.append(_event(
            dt.date(year, month, day), n.upi("CRED CLUB", "cred.club@axisb"), round(rng.uniform(low, high), 2),
            "debit", Category.TRANSFER,
            ambiguity=Ambiguity(
                "credit_card_payment",
                "Credit card bill paid through CRED. This pays off the card; the actual spending happened on the "
                "card and belongs to the card statement. Counting it as spending would count those purchases "
                "twice, so it is labelled transfer.",
            ),
        ))

        gateway = p.gateways[(month - 1) % len(p.gateways)]
        out.append(_event(
            self.day(year, month), n.upi(gateway.name, gateway.vpa), round(rng.uniform(*gateway.amount_range), 2),
            "debit", gateway.category,
            ambiguity=Ambiguity(
                "payment_gateway",
                f"The narration shows only the payment gateway ({gateway.name}), not the business that was paid. "
                f"It was actually: {gateway.purpose}, labelled {gateway.category.value}. The text alone cannot "
                "show this, so 'other' is also an acceptable prediction.",
                acceptable=(Category.OTHER,),
            ),
        ))

        name, vpa = rng.choice(people.friends)
        out.append(_event(
            self.day(year, month), n.upi(name, vpa, note="DINNER SPLIT", merchant=False),
            float(rng.randrange(400, 2_501, 50)), "debit", Category.TRANSFER,
            ambiguity=Ambiguity(
                "p2p_with_memo",
                "UPI payment to a friend with the memo 'DINNER SPLIT'. The memo suggests food, but the money went "
                "to a person, not a restaurant. Payments to people count as transfer under CONVENTIONS.",
            ),
        ))
        return out


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
    ambiguous_rows: list[dict] = []
    for e in events:
        signed = e.amount if e.direction == "credit" else -e.amount
        if not e.essential and balance + signed < 0:
            continue
        balance = round(balance + signed, 2)
        if e.ambiguity:
            ambiguous_rows.append({
                "index": len(transactions),
                "reference": e.reference,
                "description": e.description,
                "type": e.ambiguity.type,
                "label": e.category.value,
                "acceptable": [c.value for c in e.ambiguity.acceptable],
                "reason": e.ambiguity.reason,
            })
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
        meta={
            "source": "simulated",
            "persona": persona.key,
            "seed": seed,
            "narration_style": bank,
            "ambiguous_rows": ambiguous_rows,
        },
    )
