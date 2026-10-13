"""Display formatting shared by the stages that write text for people."""


def format_inr(amount: float) -> str:
    """12345678.5 -> '₹1,23,45,678.50'; whole rupees drop the paise. Sign is dropped; say it in words."""
    whole, paise = f"{abs(amount):.2f}".split(".")
    head, groups = whole[:-3], [whole[-3:]]
    while head:
        groups.insert(0, head[-2:])
        head = head[:-2]
    return "₹" + ",".join(groups) + ("" if paise == "00" else f".{paise}")
