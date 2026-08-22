from datetime import UTC, date, datetime

import pytest
from pawe_api.contracts import DataQuality
from pawe_api.data.classification_repository import StoredPrimaryClassification
from pawe_api.features.market_snapshot import TechnicalSnapshotObservation
from pawe_api.features.sector_market import build_classified_market_observations
from pawe_api.features.technical import TechnicalFeatures
from pawe_api.rules.models import Domain


def test_builds_sector_breadth_and_tie_aware_volatility_percentiles() -> None:
    observations = (
        _observation(1, "000001", return_5d=0.02, volume_activity=1.2, volatility=0.1),
        _observation(2, "000002", return_5d=-0.01, volume_activity=0.8, volatility=0.2),
        _observation(3, "000003", return_5d=0.03, volume_activity=1.0, volatility=0.2),
    )
    classifications = {
        "000001": _classification(1, "000001", "ai"),
        "000002": _classification(2, "000002", "ai"),
        "000003": _classification(3, "000003", "robotics"),
    }

    result = build_classified_market_observations(observations, classifications)

    assert result[0].sector.member_count == 2
    assert result[0].sector.up_ratio_5d == 0.5
    assert result[0].sector.volume_activity_median == 1.0
    assert result[0].volatility_percentile == pytest.approx(1 / 3)
    assert result[1].volatility_percentile == result[2].volatility_percentile
    assert result[0].snapshot_payload()["schema_version"] == "classified-market-1"


def test_rejects_unclassified_observation_instead_of_filling_a_default() -> None:
    with pytest.raises(ValueError, match="primary classification missing"):
        build_classified_market_observations(
            (_observation(1, "000001", 0.01, 1.0, 0.1),),
            {},
        )


def _observation(
    stock_id: int,
    code: str,
    return_5d: float,
    volume_activity: float,
    volatility: float,
) -> TechnicalSnapshotObservation:
    features = TechnicalFeatures(
        as_of=date(2026, 8, 7),
        return_5d=return_5d,
        return_20d=0.1,
        return_60d=0.2,
        distance_high_20d=-0.1,
        volume_activity_5d=volume_activity,
        avg_amount_20d=200_000_000,
        volatility_20d=volatility,
        above_ma20=True,
        amount_anomaly_days=0,
    )
    return TechnicalSnapshotObservation(
        stock_id=stock_id,
        stock_code=code,
        as_of=features.as_of,
        fetched_at=datetime(2026, 8, 9, tzinfo=UTC),
        quality=DataQuality.VERIFIED,
        features=features,
        payload={"schema_version": "technical-market-2"},
    )


def _classification(
    stock_id: int,
    code: str,
    sector: str,
) -> StoredPrimaryClassification:
    return StoredPrimaryClassification(
        stock_id=stock_id,
        stock_code=code,
        domain=Domain.MAIN,
        sector_code=sector,
        quality=DataQuality.VERIFIED,
        valid_from=date(2026, 8, 9),
        valid_to=None,
        published_at=date(2026, 8, 7),
        fetched_at=datetime(2026, 8, 9, tzinfo=UTC),
        content_hash="a" * 64,
    )
