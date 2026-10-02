"""Load a collected snapshot into Postgres.

    DATABASE_URL=postgresql://... python scripts/load_seed.py
    DATABASE_URL=postgresql://... python scripts/load_seed.py --path ../data/seed_glendale.json

The API bootstraps an *empty* database from the committed snapshot on its own,
so this is not needed for a first deploy. It exists for the case that does need
a human decision: pushing a freshly collected market into a database that
already has data. The load is an upsert keyed on company id, so re-running it
updates rows in place rather than duplicating them, and companies a user has
since refreshed are overwritten only if the snapshot names them.

Run `alembic upgrade head` first; this loads data, it does not create tables.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from app.db.session import connection_spec, database_configured, dispose_engine, get_sessionmaker
from app.schemas import Company
from app.store import load_dataset, load_snapshot


def read_snapshot(path: Path | None) -> tuple[dict[str, Any], list[Company]]:
    """The committed seed by default, or the file given."""
    if path is None:
        return load_dataset()
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload, [Company(**row) for row in payload.get("companies", [])]


async def run(payload: dict[str, Any], companies: list[Company]) -> int:
    try:
        async with get_sessionmaker()() as session, session.begin():
            loaded = await load_snapshot(session, payload, companies)
    finally:
        await dispose_engine()

    market = (payload.get("market") or {}).get("label", "unnamed market")
    print(f"loaded {loaded} companies ({market}) into {connection_spec().safe_description}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Load a collected snapshot into Postgres.")
    parser.add_argument(
        "--path", type=Path, default=None, help="snapshot JSON (default: the committed seed)"
    )
    args = parser.parse_args()

    if not database_configured():
        print("DATABASE_URL is not set; nothing to load into.", file=sys.stderr)
        raise SystemExit(2)
    payload, companies = read_snapshot(args.path)
    if not companies:
        print("The snapshot contains no companies.", file=sys.stderr)
        raise SystemExit(1)
    raise SystemExit(asyncio.run(run(payload, companies)))


if __name__ == "__main__":
    main()
