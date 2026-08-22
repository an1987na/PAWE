import asyncio
import json
from dataclasses import asdict

from pawe_api.db.session import SessionFactory
from pawe_api.experiments.legacy_attribution import classify_legacy_outcomes


async def classify() -> dict[str, int]:
    async with SessionFactory() as session:
        result = await classify_legacy_outcomes(session)
    return asdict(result)


def main() -> None:
    print(json.dumps(asyncio.run(classify()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
