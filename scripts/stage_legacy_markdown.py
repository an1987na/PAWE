import argparse
import asyncio
import json
from pathlib import Path

from pawe_api.db.session import SessionFactory
from pawe_api.experiments.legacy import stage_legacy_markdown
from pawe_api.experiments.legacy_repository import persist_legacy_batch


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stage legacy PAWE Markdown metadata.")
    parser.add_argument("root", type=Path, help="Legacy pick_a_weekly project root")
    return parser.parse_args()


async def stage(root: Path) -> dict[str, object]:
    batch = stage_legacy_markdown(root)
    async with SessionFactory() as session:
        result = await persist_legacy_batch(session, batch)
    return {
        "batch_id": str(result.batch_id),
        "created": result.created,
        "manifest_hash": batch.manifest_hash,
        "document_count": result.document_count,
        "item_count": result.item_count,
        "verification_status": "legacy_unverified",
    }


def main() -> None:
    args = parse_args()
    print(json.dumps(asyncio.run(stage(args.root)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
