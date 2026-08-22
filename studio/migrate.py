"""Apply studio's database schema. Run once per deploy - not automatically
at app startup; see db.apply_schema's docstring for why.

Usage:
    cd studio && python migrate.py
"""

from __future__ import annotations

import db


def main() -> None:
    db.apply_schema()
    print("Schema applied.")


if __name__ == "__main__":
    main()
