import argparse
import asyncio
import json
from collections import Counter
from dataclasses import asdict

from pawe_api.db.session import SessionFactory
from pawe_api.experiments.replay_inventory import build_legacy_outcome_inventory


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Report atomic legacy outcome sets.")
    parser.add_argument("--details", action="store_true")
    return parser.parse_args()


async def report(*, details: bool) -> dict[str, object]:
    async with SessionFactory() as session:
        inventory = await build_legacy_outcome_inventory(session)
    counts = Counter((item.arm, item.status) for item in inventory)
    summary = [
        {"arm": arm, "status": status, "week_count": count}
        for (arm, status), count in sorted(counts.items())
    ]
    payload: dict[str, object] = {
        "outcome_set_count": len(inventory),
        "summary": summary,
        "formal_replay_ready_count": 0,
        "formal_blocker": "all_ready_outcomes_are_single_source",
    }
    if details:
        payload["outcome_sets"] = [asdict(item) for item in inventory]
    return payload


def main() -> None:
    args = parse_args()
    print(
        json.dumps(
            asyncio.run(report(details=args.details)),
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
