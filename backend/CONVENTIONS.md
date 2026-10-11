# Data conventions

These rules apply to every model in [`app/schema.py`](app/schema.py) and to the JSON the API returns. Where possible the schema enforces them, so data that breaks a rule fails validation instead of passing through silently.

## Amounts and direction

- `Transaction.amount` is always **positive** (`> 0`). Whether money came in or went out is stored separately in `direction`: `"credit"` means money in, `"debit"` means money out.
- Parsers skip zero-amount rows instead of storing them.
- Never put a sign on `amount`. When you need signed arithmetic, use `Transaction.signed_amount`, a Python-only helper that never appears in the JSON.
- Totals of transaction amounts are also positive: `total_credits`, `total_debits`, `largest_*`, `MonthlyTotal.credits` and `MonthlyTotal.debits`, `spending_by_category`, and `Anomaly.amount`.
- Balances and net values **are signed**, because they can really be negative: `balance`, `opening_balance`, `closing_balance`, `net_flow`, `MonthlyTotal.net`, `difference`, `average_balance`. Net values are credits minus debits.

## Money

- Every money value is rounded to **2 decimal places**, using round-half-up (`2.675 → 2.68`).
- The rounding is done by the `Money` and `PositiveMoney` types, so nobody needs to call `round()` by hand.
- Values are stored as floats so they work with pandas and come out as JSON numbers. Because floats aren't exact, compare balances with a tolerance of `BALANCE_TOLERANCE` (0.01), never with `==`.
- `currency` is an ISO 4217 code such as `INR`. Amounts never include a currency symbol or thousands separators.

## Dates

- All dates follow ISO 8601. A date is written `YYYY-MM-DD`, and a month key (`MonthlyTotal.month`) is `YYYY-MM`.
- Parsers convert bank formats such as `01/09/26` or `01-Sep-2026` into `datetime.date` before building a model. Raw date strings never reach the schema.
- Timestamps in `meta` are ISO 8601 datetimes in UTC, for example `2026-10-11T09:30:00Z`.

## Categories

`Transaction.category` must be one of these 8 values. Anything that doesn't match a rule defaults to `other`.

| Value | Covers |
|---|---|
| `income` | Salary, interest, refunds, dividends |
| `transfer` | Moving money between your own accounts, and payments to or from people (UPI, NEFT, IMPS) |
| `bills` | Rent, utilities, phone and internet, insurance, subscriptions, loan EMIs |
| `food` | Groceries, restaurants, food delivery |
| `shopping` | Shops and online stores |
| `transport` | Fuel, cabs, public transport, travel |
| `cash` | ATM withdrawals and cash deposits |
| `other` | Anything that doesn't fit a category above |

Adding or renaming a category is a schema change. The frontend needs to be updated in the same change.
