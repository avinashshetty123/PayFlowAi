"""Seed demo data:  python -m app.seed"""

import asyncio
import sys

from app.core.database import engine
from app.core.logging import configure_logging
from app.services.demo_service import seed_demo_data


async def main() -> int:
    configure_logging()
    try:
        counts = await seed_demo_data()
    finally:
        await engine.dispose()
    print(
        f"Seeded {counts['payments']} payments, {counts['incidents']} incidents, "
        f"{counts['historical_incidents']} historical incidents in {counts['seconds']}s"
    )
    return 0


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    sys.exit(asyncio.run(main()))
