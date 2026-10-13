# backend/scripts/simulate_persona.py
"""Write a simulated statement to sample_statements/.

Usage (from backend/):
    uv run python scripts/simulate_persona.py
    uv run python scripts/simulate_persona.py --start 2026-01 --months 6 --seed 7 --bank hdfc
"""
import argparse
import datetime as dt
import sys
from collections import Counter
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.narrations import BANKS  # noqa: E402
from app.simulator import SALARIED_PROFESSIONAL, simulate  # noqa: E402

OUTPUT_DIR = BACKEND_DIR.parent / "sample_statements"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start", default="2026-07", help="First month, YYYY-MM (default: 2026-07)")
    parser.add_argument("--months", type=int, default=3)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--bank", choices=BANKS, default="sbi", help="Narration style (default: sbi)")
    parser.add_argument("--out", type=Path, help="Output file (default: sample_statements/<persona>_<bank>_<start>_<n>m.json)")
    args = parser.parse_args()

    start = dt.datetime.strptime(args.start, "%Y-%m").date()
    persona = SALARIED_PROFESSIONAL
    payload = simulate(persona, start, args.months, args.seed, args.bank)

    out = args.out or OUTPUT_DIR / f"{persona.key}_{args.bank}_{args.start}_{args.months}m.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(payload.model_dump_json(indent=2) + "\n")

    s = payload.statement
    print(f"Wrote {out}")
    print(f"{len(payload.transactions)} transactions, {s.period_start} to {s.period_end}")
    print(f"Balance {s.opening_balance:,.2f} -> {s.closing_balance:,.2f}")
    for category, count in Counter(t.category.value for t in payload.transactions).most_common():
        print(f"  {category:<10} {count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
