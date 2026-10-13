# backend/app/narrations.py
"""Indian bank narration formats for UPI, NEFT, IMPS, NACH, ATM, card and interest lines.

Each bank lays out the same payment differently, e.g. the same Swiggy order:
    sbi   UPI/DR/412345678901/SWIGGY/YESB/swiggy@ybl
    hdfc  UPI-SWIGGY-swiggy@ybl-YESB0283714-412345678901-PAYMENT
    icici UPI/412345678901/PAYMENT/swiggy@ybl/YES BANK
    axis  UPI/P2M/412345678901/SWIGGY/PAYMENT/YES BANK
A statement uses one bank's style throughout.

Run `uv run python -m app.narrations` to print a sample.
"""
import datetime as dt
import random
from typing import Literal, NamedTuple

Bank = Literal["hdfc", "icici", "sbi", "axis"]
BANKS: tuple[Bank, ...] = ("hdfc", "icici", "sbi", "axis")
Direction = Literal["debit", "credit"]

# UPI handle -> (IFSC prefix, bank name as printed)
_HANDLE_BANK = {
    "ybl": ("YESB", "YES BANK"),
    "axl": ("UTIB", "AXIS BANK"),
    "okaxis": ("UTIB", "AXIS BANK"),
    "icici": ("ICIC", "ICICI BANK"),
    "okicici": ("ICIC", "ICICI BANK"),
    "hdfcbank": ("HDFC", "HDFC BANK"),
    "okhdfcbank": ("HDFC", "HDFC BANK"),
    "oksbi": ("SBIN", "STATE BANK OF INDIA"),
    "sbi": ("SBIN", "STATE BANK OF INDIA"),
    "paytm": ("PYTM", "PAYTM PAYMENTS BANK"),
    "apl": ("UTIB", "AXIS BANK"),  # Amazon Pay
    "axisb": ("UTIB", "AXIS BANK"),  # CRED
}
_ONLINE_CITIES = ("MUMBAI", "BANGALORE", "GURGAON", "NEW DELHI")


class Narration(NamedTuple):
    text: str
    reference: str


class Narrator:
    """Builds narrations in one bank's style."""

    def __init__(self, bank: Bank, rng: random.Random, card_number: str = "4XXXXXXXXXXX4821"):
        self.bank = bank
        self.rng = rng
        self.card_number = card_number

    # --- helpers ---

    def ref(self) -> str:
        return str(self.rng.randint(10**11, 10**12 - 1))

    def ifsc(self, prefix: str) -> str:
        return f"{prefix}0{self.rng.randint(100000, 999999)}"

    def terminal(self) -> str:
        return f"S1A{self.rng.choice('BCNW')}{self.rng.randint(1000, 9999)}"

    @staticmethod
    def _bank_of(vpa: str) -> tuple[str, str]:
        return _HANDLE_BANK.get(vpa.rsplit("@", 1)[-1], ("UPIB", "UPI BANK"))

    # --- channels ---

    def upi(self, name: str, vpa: str, direction: Direction = "debit", note: str | None = None,
            merchant: bool = True) -> Narration:
        ref = self.ref()
        code, bank_name = self._bank_of(vpa)
        tag = "CR" if direction == "credit" else "DR"
        match self.bank:
            case "sbi":
                text = f"UPI/{tag}/{ref}/{name}/{code}/{vpa}" + (f"/{note}" if note else "")
            case "hdfc":
                text = f"UPI-{name}-{vpa}-{self.ifsc(code)}-{ref}-{note or 'PAYMENT'}"
            case "icici":
                text = f"UPI/{ref}/{note or 'PAYMENT'}/{vpa}/{bank_name}"
            case "axis":
                text = f"UPI/{'P2M' if merchant else 'P2A'}/{ref}/{name}/{note or 'UPI'}/{bank_name}"
        return Narration(text, ref)

    def neft(self, name: str, direction: Direction, note: str = "", bank_prefix: str = "ICIC") -> Narration:
        ifsc = self.ifsc(bank_prefix)
        utr = f"{bank_prefix}N{self.ref()}"
        tag = "CR" if direction == "credit" else "DR"
        match self.bank:
            case "sbi":
                text = f"{'BY' if direction == 'credit' else 'TO'} TRANSFER-NEFT*{ifsc}*{utr}*{name}"
            case "hdfc":
                text = f"NEFT {tag}-{ifsc}-{name}-{note}-{utr}"
            case "icici":
                text = f"NEFT-{utr}-{name}-{note}"
            case "axis":
                text = f"NEFT/{utr}/{name}/{note}"
        return Narration(text, utr)

    def imps(self, name: str, note: str = "", bank_prefix: str = "SBIN", own_account: bool = False) -> Narration:
        ref = self.ref()
        match self.bank:
            case "sbi":
                text = f"IMPS/P2P/{ref}/{name}"
            case "hdfc":
                text = f"IMPS-{ref}-{name}-{bank_prefix}-XXXXXXXX{self.rng.randint(1000, 9999)}-{note}"
            case "icici":
                text = f"MMT/IMPS/{ref}/{note}/{name}/{bank_prefix}"
            case "axis":
                text = f"IMPS/{'P2A' if own_account else 'P2P'}/{ref}/{name}/{note}"
        return Narration(text, ref)

    def nach(self, lender: str, purpose: str = "LOAN EMI") -> Narration:
        umrn = f"{self.rng.choice(('HDFC', 'ICIC', 'UTIB', 'SBIN'))}7{self.rng.randint(10**14, 10**15 - 1)}"
        match self.bank:
            case "sbi":
                text = f"NACH/DR/{lender} {purpose}"
            case "hdfc":
                text = f"ACH D- {lender}-{umrn[-9:]}"
            case "icici":
                text = f"ACH/{lender}/{umrn}"
            case "axis":
                text = f"NACH-DR-{lender}-{purpose}-{umrn}"
        return Narration(text, umrn)

    def atm(self, city: str) -> Narration:
        terminal = self.terminal()
        match self.bank:
            case "sbi":
                text = f"ATM WDL/{city}/{terminal}"
            case "hdfc":
                text = f"NWD-{self.card_number}-{terminal}-{city}"
            case "icici":
                text = f"ATM/CASH WDL/{terminal}/{city}"
            case "axis":
                text = f"ATM-CASH/{terminal}/{city}"
        return Narration(text, terminal)

    def card(self, merchant: str, city: str, online: bool = False) -> Narration:
        ref = self.ref()
        if online:
            city = self.rng.choice(_ONLINE_CITIES)
        last4 = self.card_number[-4:]
        match self.bank:
            case "sbi":
                text = f"{'ECOM PUR' if online else 'POS PRCH'}/{last4}/{merchant}/{city}"
            case "hdfc":
                text = f"POS {self.card_number} {merchant}"
            case "icici":
                text = f"{'IPS' if online else 'VPS'}/{merchant}/{ref[-6:]}/{city}"
            case "axis":
                text = f"{'ECOM PUR' if online else 'POS'}/{merchant}/{city}"
        return Narration(text, ref)

    def interest(self, start: dt.date, end: dt.date) -> Narration:
        ref = self.ref()
        match self.bank:
            case "sbi":
                text = "CREDIT INTEREST"
            case "hdfc":
                text = f"INTEREST PAID TILL {end:%d-%b-%Y}".upper()
            case "icici":
                text = f"INT.PD:{start:%d-%m-%Y} TO {end:%d-%m-%Y}"
            case "axis":
                text = f"SB:INT CREDIT:{start:%d-%m-%Y} TO {end:%d-%m-%Y}"
        return Narration(text, ref)


if __name__ == "__main__":
    rng = random.Random(7)
    # (weight, builder): weighted roughly like a real month, mostly UPI.
    samples = [
        (4, lambda n: n.upi("SWIGGY", "swiggy@ybl")),
        (4, lambda n: n.upi("ZOMATO", "zomato@paytm")),
        (3, lambda n: n.upi("UBER", "uber@axl")),
        (1, lambda n: n.upi("BESCOM", "bescom@sbi", note="ELECTRICITY BILL")),
        (2, lambda n: n.upi("ROHAN GUPTA", "rohan.gupta@okaxis", note="DINNER SPLIT", merchant=False)),
        (1, lambda n: n.upi("SNEHA IYER", "sneha.iyer@oksbi", direction="credit", note="CAB SHARE", merchant=False)),
        (1, lambda n: n.neft("ACME TECH", "credit", note="SALARY JAN 2026")),
        (1, lambda n: n.imps("KARTHIK R", note="TRIP SHARE")),
        (1, lambda n: n.imps("ARYAN MAHARAJ", note="SELF", own_account=True)),
        (1, lambda n: n.nach("BAJAJ FINANCE LTD")),
        (1, lambda n: n.atm("BENGALURU")),
        (2, lambda n: n.card("AMAZON", "BENGALURU", online=True)),
        (1, lambda n: n.card("STARBUCKS", "BENGALURU")),
        (1, lambda n: n.interest(dt.date(2026, 1, 1), dt.date(2026, 1, 31))),
    ]
    weights, builders = zip(*samples)
    for _ in range(20):
        bank = rng.choice(BANKS)
        build = rng.choices(builders, weights)[0]
        print(f"{bank:<6} {build(Narrator(bank, rng)).text}")
