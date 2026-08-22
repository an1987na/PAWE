import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pawe_api.features.artifacts import (
    FeatureArtifactError,
    FeatureArtifactManifest,
    publish_feature_artifact,
    query_parquet_partition,
    write_parquet_partition,
)


def _manifest(content: bytes) -> FeatureArtifactManifest:
    return FeatureArtifactManifest(
        snapshot_id="00000000-0000-0000-0000-000000000001",
        partition_key="week=2026-08-10",
        schema_version="1",
        feature_version="v9-feature-1",
        code_version="test",
        decision_cutoff=datetime(2026, 8, 7, 15, tzinfo=UTC),
        source_hashes=("a" * 64,),
        row_count=2,
        content_hash=hashlib.sha256(content).hexdigest(),
    )


def test_feature_artifact_is_hash_checked_and_published_without_overwrite(
    tmp_path: Path,
) -> None:
    content = b"PAR1-test-parquet-placeholder"
    temporary = tmp_path / "features.tmp.parquet"
    destination = tmp_path / "features.parquet"
    temporary.write_bytes(content)

    result = publish_feature_artifact(temporary, destination, _manifest(content))

    assert result == destination
    assert destination.read_bytes() == content
    assert not temporary.exists()

    another = tmp_path / "another.parquet"
    another.write_bytes(content)
    with pytest.raises(FeatureArtifactError, match="already exists"):
        publish_feature_artifact(another, destination, _manifest(content))


def test_feature_artifact_rejects_hash_mismatch(tmp_path: Path) -> None:
    temporary = tmp_path / "features.tmp.parquet"
    temporary.write_bytes(b"unexpected")
    with pytest.raises(FeatureArtifactError, match="hash"):
        publish_feature_artifact(temporary, tmp_path / "features.parquet", _manifest(b"expected"))


def test_feature_partition_round_trips_through_parquet_and_duckdb(tmp_path: Path) -> None:
    temporary = tmp_path / "week.tmp.parquet"
    digest = write_parquet_partition(
        [
            {"stock_code": "000001", "return_5d": 0.02},
            {"stock_code": "000002", "return_5d": 0.03},
        ],
        temporary,
        max_rows=2,
    )
    manifest = _manifest(temporary.read_bytes())
    assert digest == manifest.content_hash
    destination = publish_feature_artifact(temporary, tmp_path / "week.parquet", manifest)

    rows = query_parquet_partition(
        destination,
        columns=("stock_code", "return_5d"),
        allowed_columns=frozenset({"stock_code", "return_5d"}),
        limit=5,
    )
    assert rows == [
        {"stock_code": "000001", "return_5d": 0.02},
        {"stock_code": "000002", "return_5d": 0.03},
    ]


def test_feature_partition_enforces_budget_and_column_registry(tmp_path: Path) -> None:
    temporary = tmp_path / "week.tmp.parquet"
    with pytest.raises(FeatureArtifactError, match="row budget"):
        write_parquet_partition([{"a": 1}, {"a": 2}], temporary, max_rows=1)
    write_parquet_partition([{"stock_code": "000001"}], temporary)
    with pytest.raises(FeatureArtifactError, match="unregistered column"):
        query_parquet_partition(
            temporary,
            columns=("stock_code; DROP TABLE stocks",),
            allowed_columns=frozenset({"stock_code"}),
        )
