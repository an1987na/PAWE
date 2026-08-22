import hashlib
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq


class FeatureArtifactError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class FeatureArtifactManifest:
    snapshot_id: str
    partition_key: str
    schema_version: str
    feature_version: str
    code_version: str
    decision_cutoff: datetime
    source_hashes: tuple[str, ...]
    row_count: int
    content_hash: str

    def validate(self) -> None:
        if self.row_count < 0:
            raise FeatureArtifactError("row_count cannot be negative")
        if len(self.content_hash) != 64:
            raise FeatureArtifactError("content_hash must be a SHA-256 digest")
        if any(len(source_hash) != 64 for source_hash in self.source_hashes):
            raise FeatureArtifactError("source_hashes must contain SHA-256 digests")
        if self.decision_cutoff.tzinfo is None:
            raise FeatureArtifactError("decision_cutoff must include timezone information")


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def publish_feature_artifact(
    temporary_path: Path,
    published_path: Path,
    manifest: FeatureArtifactManifest,
) -> Path:
    manifest.validate()
    temporary = temporary_path.resolve(strict=True)
    destination_parent = published_path.parent.resolve(strict=True)
    destination = destination_parent / published_path.name
    if temporary.parent != destination_parent:
        raise FeatureArtifactError("temporary and published artifacts must share a directory")
    if temporary.suffix != ".parquet" or destination.suffix != ".parquet":
        raise FeatureArtifactError("feature artifacts must use the .parquet extension")
    if destination.exists():
        raise FeatureArtifactError("published artifact already exists")
    if sha256_file(temporary) != manifest.content_hash:
        raise FeatureArtifactError("artifact content hash does not match manifest")
    try:
        os.link(temporary, destination)
    except FileExistsError as exc:
        raise FeatureArtifactError("published artifact already exists") from exc
    temporary.unlink()
    return destination


def write_parquet_partition(
    records: Sequence[Mapping[str, Any]],
    temporary_path: Path,
    *,
    max_rows: int = 100_000,
) -> str:
    if not records:
        raise FeatureArtifactError("feature partition cannot be empty")
    if len(records) > max_rows:
        raise FeatureArtifactError("feature partition exceeds the configured row budget")
    if temporary_path.suffix != ".parquet":
        raise FeatureArtifactError("temporary feature partition must use .parquet")
    if temporary_path.exists():
        raise FeatureArtifactError("temporary feature partition already exists")
    temporary_path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist([dict(record) for record in records])
    pq.write_table(table, temporary_path, compression="zstd", write_statistics=True)
    return sha256_file(temporary_path)


def query_parquet_partition(
    path: Path,
    *,
    columns: Sequence[str],
    allowed_columns: frozenset[str],
    limit: int = 1_000,
) -> list[dict[str, object]]:
    resolved = path.resolve(strict=True)
    if resolved.suffix != ".parquet":
        raise FeatureArtifactError("query target must be a .parquet artifact")
    if not 1 <= limit <= 10_000:
        raise FeatureArtifactError("query limit must be between 1 and 10000")
    if not columns:
        raise FeatureArtifactError("at least one query column is required")
    if any(
        column not in allowed_columns
        or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", column)
        for column in columns
    ):
        raise FeatureArtifactError("query contains an unregistered column")
    selected = ", ".join(f'"{column}"' for column in columns)
    connection = duckdb.connect(database=":memory:")
    try:
        cursor = connection.execute(
            f"SELECT {selected} FROM read_parquet(?) LIMIT ?",
            [str(resolved), limit],
        )
        names = [description[0] for description in cursor.description]
        return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]
    finally:
        connection.close()
