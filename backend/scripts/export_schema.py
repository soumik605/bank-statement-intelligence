# backend/scripts/export_schema.py
"""Write schema/transaction.schema.json from the Transaction model.

Usage (from backend/):
    uv run python scripts/export_schema.py          # write the file
    uv run python scripts/export_schema.py --check  # exit 1 if the file is out of date
"""
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.schema import Transaction  # noqa: E402

OUTPUT = BACKEND_DIR.parent / "schema" / "transaction.schema.json"


def render() -> str:
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        **Transaction.model_json_schema(),
    }
    return json.dumps(schema, indent=2) + "\n"


def main() -> int:
    content = render()
    if "--check" in sys.argv:
        if not OUTPUT.exists() or OUTPUT.read_text() != content:
            print(f"{OUTPUT} is out of date; run scripts/export_schema.py", file=sys.stderr)
            return 1
        print(f"{OUTPUT} is up to date")
        return 0
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(content)
    print(f"Wrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
