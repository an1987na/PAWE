import statistics
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from pawe_api.data.classification_repository import StoredPrimaryClassification
from pawe_api.features.market_snapshot import TechnicalSnapshotObservation

CLASSIFIED_MARKET_SCHEMA_VERSION = "classified-market-1"


@dataclass(frozen=True, slots=True)
class SectorMarketFeatures:
    sector_code: str
    member_count: int
    positive_member_count_5d: int
    up_ratio_5d: float
    volume_activity_median: float


@dataclass(frozen=True, slots=True)
class ClassifiedMarketObservation:
    technical: TechnicalSnapshotObservation
    classification: StoredPrimaryClassification
    sector: SectorMarketFeatures
    volatility_percentile: float

    def snapshot_payload(self) -> dict[str, object]:
        return {
            **self.technical.payload,
            "schema_version": CLASSIFIED_MARKET_SCHEMA_VERSION,
            "technical_schema_version": self.technical.payload["schema_version"],
            "classification": {
                "domain": self.classification.domain.value,
                "sector_code": self.classification.sector_code,
                "quality": self.classification.quality.value,
                "valid_from": self.classification.valid_from.isoformat(),
                "valid_to": (
                    self.classification.valid_to.isoformat()
                    if self.classification.valid_to is not None
                    else None
                ),
                "published_at": self.classification.published_at.isoformat(),
                "fetched_at": self.classification.fetched_at.isoformat(),
                "content_hash": self.classification.content_hash,
            },
            "sector_market": {
                "sector_code": self.sector.sector_code,
                "member_count": self.sector.member_count,
                "positive_member_count_5d": self.sector.positive_member_count_5d,
                "up_ratio_5d": self.sector.up_ratio_5d,
                "volume_activity_median": self.sector.volume_activity_median,
            },
            "volatility_percentile": self.volatility_percentile,
        }


def build_classified_market_observations(
    observations: Sequence[TechnicalSnapshotObservation],
    classifications: Mapping[str, StoredPrimaryClassification],
) -> tuple[ClassifiedMarketObservation, ...]:
    codes = [observation.stock_code for observation in observations]
    if len(codes) != len(set(codes)):
        raise ValueError("technical observations must have unique stock codes")
    missing = sorted(set(codes) - set(classifications))
    if missing:
        raise ValueError("primary classification missing: " + ",".join(missing[:10]))

    by_sector: dict[str, list[TechnicalSnapshotObservation]] = defaultdict(list)
    for observation in observations:
        classification = classifications[observation.stock_code]
        if classification.stock_id != observation.stock_id:
            raise ValueError(
                f"classification stock identity mismatch: {observation.stock_code}"
            )
        by_sector[classification.sector_code].append(observation)

    sector_features: dict[str, SectorMarketFeatures] = {}
    for sector_code, members in by_sector.items():
        positive_count = sum(member.features.return_5d > 0 for member in members)
        sector_features[sector_code] = SectorMarketFeatures(
            sector_code=sector_code,
            member_count=len(members),
            positive_member_count_5d=positive_count,
            up_ratio_5d=positive_count / len(members),
            volume_activity_median=statistics.median(
                member.features.volume_activity_5d for member in members
            ),
        )

    volatility_percentiles = _average_rank_percentiles(
        {
            observation.stock_code: observation.features.volatility_20d
            for observation in observations
        }
    )
    return tuple(
        ClassifiedMarketObservation(
            technical=observation,
            classification=classifications[observation.stock_code],
            sector=sector_features[classifications[observation.stock_code].sector_code],
            volatility_percentile=volatility_percentiles[observation.stock_code],
        )
        for observation in observations
    )


def _average_rank_percentiles(values: Mapping[str, float]) -> dict[str, float]:
    if not values:
        return {}
    sorted_items = sorted(values.items(), key=lambda item: (item[1], item[0]))
    result: dict[str, float] = {}
    index = 0
    count = len(sorted_items)
    while index < count:
        end = index + 1
        while end < count and sorted_items[end][1] == sorted_items[index][1]:
            end += 1
        average_rank = ((index + 1) + end) / 2
        percentile = average_rank / count
        for code, _ in sorted_items[index:end]:
            result[code] = percentile
        index = end
    return result
