// Indian digit grouping: 245000 -> "2,45,000", never "245,000".
export function inr(amount: number, { paise = "auto" }: { paise?: "auto" | "always" } = {}): string {
  const hasPaise = Math.round(Math.abs(amount) * 100) % 100 !== 0;
  const digits = paise === "always" || hasPaise ? 2 : 0;
  return (
    "₹" +
    Math.abs(amount).toLocaleString("en-IN", { minimumFractionDigits: digits, maximumFractionDigits: digits })
  );
}

// "+₹1,38,000.00" / "−₹485.18"; a real minus sign, so it reads as one.
export function signedInr(amount: number, opts?: Parameters<typeof inr>[1]): string {
  return (amount < 0 ? "−" : "+") + inr(amount, opts);
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

// "2026-03-24" -> "24 Mar 2026". Parsed by hand: new Date("2026-03-24") is UTC
// midnight, which shows as the previous day anywhere west of Greenwich.
export function formatDate(iso: string): string {
  const [y, m, d] = iso.split("-");
  return `${d} ${MONTHS[Number(m) - 1]} ${y}`;
}

// "2026-10-14T04:05:00Z" -> "14 Oct 2026, 9:35 am" in the viewer's own time zone.
export function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString("en-IN", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}
