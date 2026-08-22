import argparse
import asyncio
import json
from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
from pawe_api.data.classification import (
    PrimaryClassificationStatus,
    parse_capco_pdf,
    parse_official_theme_manifest,
    resolve_primary_classification,
    theme_manifest_records,
)
from pawe_api.data.classification_repository import SqlClassificationRepository
from pawe_api.data.csi_classification import CSI_THEME_DEFINITIONS, CsiThemeProvider
from pawe_api.db import models
from pawe_api.db.session import SessionFactory
from sqlalchemy import select


async def ingest_capco(args: argparse.Namespace) -> None:
    fetched_at = datetime.now(UTC)
    payload = await _read_bytes(args.pdf)
    records = parse_capco_pdf(
        payload,
        valid_from=args.valid_from,
        published_at=args.published_at,
        evidence_url=args.evidence_url,
        fetched_at=fetched_at,
    )
    print(
        f"source=capco parsed={len(records)} valid_from={args.valid_from.isoformat()} "
        f"published_at={args.published_at.isoformat()} persist={args.persist}"
    )
    if not args.persist:
        return
    async with SessionFactory() as session, session.begin():
        result = await SqlClassificationRepository(session).upsert_evidence_batch(
            records,
            complete_source_snapshot=True,
        )
    print(
        f"created={result.created} updated={result.updated} closed={result.closed} "
        f"unknown={len(result.unknown_codes)}"
    )
    if result.unknown_codes:
        print(f"unknown_codes={','.join(result.unknown_codes)}")


async def ingest_theme(args: argparse.Namespace) -> None:
    fetched_at = datetime.now(UTC)
    payload = _load_json(Path(args.manifest))
    manifest = parse_official_theme_manifest(payload)
    records = theme_manifest_records(manifest, fetched_at=fetched_at)
    print(
        f"source={manifest.source}:{manifest.index_code} sector={manifest.sector.value} "
        f"parsed={len(records)} valid_from={manifest.valid_from.isoformat()} "
        f"persist={args.persist}"
    )
    if not args.persist:
        return
    async with SessionFactory() as session, session.begin():
        result = await SqlClassificationRepository(session).upsert_evidence_batch(
            records,
            complete_source_snapshot=True,
        )
    print(
        f"created={result.created} updated={result.updated} closed={result.closed} "
        f"unknown={len(result.unknown_codes)}"
    )
    if result.unknown_codes:
        print(f"unknown_codes={','.join(result.unknown_codes)}")


async def ingest_csi(args: argparse.Namespace) -> None:
    fetched_at = datetime.now(UTC)
    index_codes = tuple(CSI_THEME_DEFINITIONS) if args.all else tuple(args.index_code)
    if not index_codes:
        raise ValueError("select --all or at least one --index-code")
    async with httpx.AsyncClient(follow_redirects=True, trust_env=False) as client:
        provider = CsiThemeProvider(client)
        manifests = tuple(
            [
                await provider.fetch(
                    CSI_THEME_DEFINITIONS[index_code], fetched_at=fetched_at
                )
                for index_code in index_codes
            ]
        )
    batches = tuple(
        theme_manifest_records(manifest, fetched_at=fetched_at) for manifest in manifests
    )
    for manifest, records in zip(manifests, batches, strict=True):
        print(
            f"source=csi:{manifest.index_code} sector={manifest.sector.value} "
            f"parsed={len(records)} published_at={manifest.published_at.isoformat()} "
            f"valid_from={manifest.valid_from.isoformat()} persist={args.persist}"
        )
    if not args.persist:
        return
    async with SessionFactory() as session, session.begin():
        repository = SqlClassificationRepository(session)
        results = tuple(
            [
                await repository.upsert_evidence_batch(
                    records,
                    complete_source_snapshot=True,
                )
                for records in batches
            ]
        )
    print(
        f"created={sum(result.created for result in results)} "
        f"updated={sum(result.updated for result in results)} "
        f"closed={sum(result.closed for result in results)} "
        f"unknown={sum(len(result.unknown_codes) for result in results)}"
    )
    for manifest, result in zip(manifests, results, strict=True):
        if result.unknown_codes:
            print(
                f"unknown_codes[{manifest.index_code}]="
                f"{','.join(result.unknown_codes)}"
            )


async def resolve_primary(args: argparse.Namespace) -> None:
    async with SessionFactory() as session:
        repository = SqlClassificationRepository(session)
        evidence_by_code = await repository.load_evidence_as_of(as_of=args.as_of)
        codes = tuple(
            (
                await session.execute(
                    select(models.Stock.code)
                    .where(
                        models.Stock.exchange.in_(("SSE", "SZSE")),
                        models.Stock.status != "delisted",
                    )
                    .order_by(models.Stock.code)
                )
            ).scalars()
        )
        results = tuple(
            resolve_primary_classification(
                code,
                evidence_by_code.get(code, ()),
                as_of=args.as_of,
            )
            for code in codes
        )
        status_counts = Counter(result.status.value for result in results)
        sector_counts = Counter(
            result.primary.sector_code
            for result in results
            if result.primary is not None
        )
        print(
            f"as_of={args.as_of.isoformat()} stocks={len(codes)} "
            f"statuses={dict(sorted(status_counts.items()))} "
            f"sectors={dict(sorted(sector_counts.items()))} persist={args.persist}"
        )
        conflicted = [
            result
            for result in results
            if result.status is PrimaryClassificationStatus.CONFLICTED
        ]
        if conflicted:
            print(
                "conflicts="
                + ";".join(
                    f"{result.stock_code}:{','.join(result.reasons)}"
                    for result in conflicted
                )
            )
        if not args.persist:
            return
        write_result = await repository.replace_primary_classifications(
            results,
            as_of=args.as_of,
        )
        await session.commit()
        print(
            f"created={write_result.created} unchanged={write_result.unchanged} "
            f"closed={write_result.closed} missing={write_result.missing} "
            f"conflicted={write_result.conflicted}"
        )


async def _read_bytes(location: str) -> bytes:
    if location.startswith(("https://", "http://")):
        async with httpx.AsyncClient(follow_redirects=True, trust_env=False) as client:
            response = await client.get(location, timeout=30)
            response.raise_for_status()
            return response.content
    return Path(location).read_bytes()


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("theme manifest root must be an object")
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Ingest effective-dated official classification evidence."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    capco = subparsers.add_parser("capco", help="Import a CAPCO classification PDF.")
    capco.add_argument("--pdf", required=True, help="Official PDF URL or local path.")
    capco.add_argument("--evidence-url", required=True)
    capco.add_argument("--published-at", type=date.fromisoformat, required=True)
    capco.add_argument(
        "--valid-from",
        type=date.fromisoformat,
        required=True,
        help="First date this published file may be used; never earlier than publication.",
    )
    capco.add_argument("--persist", action="store_true")
    capco.set_defaults(handler=ingest_capco)

    theme = subparsers.add_parser("theme", help="Import a verified official index manifest.")
    theme.add_argument("--manifest", required=True)
    theme.add_argument("--persist", action="store_true")
    theme.set_defaults(handler=ingest_theme)

    csi = subparsers.add_parser("csi", help="Fetch official CSI theme constituents.")
    csi.add_argument(
        "--index-code",
        action="append",
        choices=tuple(CSI_THEME_DEFINITIONS),
        default=[],
    )
    csi.add_argument("--all", action="store_true")
    csi.add_argument("--persist", action="store_true")
    csi.set_defaults(handler=ingest_csi)

    resolve = subparsers.add_parser("resolve", help="Resolve unique PAWE primary domains.")
    resolve.add_argument("--as-of", type=date.fromisoformat, required=True)
    resolve.add_argument("--persist", action="store_true")
    resolve.set_defaults(handler=resolve_primary)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    asyncio.run(args.handler(args))


if __name__ == "__main__":
    main()
