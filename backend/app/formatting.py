"""Display formatting shared by the stages that write text for people."""


def plural(n: int, word: str) -> str:
    """plural(1, "row") -> '1 row'; plural(3, "row") -> '3 rows'."""
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def format_inr(amount: float) -> str:
    """12345678.5 -> '₹1,23,45,678.50'; whole rupees drop the paise. Sign is dropped; say it in words."""
    whole, paise = f"{abs(amount):.2f}".split(".")
    head, groups = whole[:-3], [whole[-3:]]
    while head:
        groups.insert(0, head[-2:])
        head = head[:-2]
    return "₹" + ",".join(groups) + ("" if paise == "00" else f".{paise}")
