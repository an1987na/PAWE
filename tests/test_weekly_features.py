from datetime import date

from pawe_api.contracts import DataQuality, MarketState
from pawe_api.features.weekly import build_degraded_market_state_input, build_rule_features
from pawe_api.rules.market_state import determine_market_state


def test_build_rule_features_uses_only_proven_snapshot_evidence() -> None:
    rows = [
        (
            index,
            code,
            f"stock-{index}",
            "gem",
            "active",
            date(2020, 1, 1),
            _payload(code, return_5d=return_5d),
            DataQuality.VERIFIED,
        )
        for index, (code, return_5d) in enumerate(
            (("300001", 0.03), ("300002", 0.02), ("300003", -0.01)),
            start=1,
        )
    ]

    features = [feature for _, feature in build_rule_features(rows)]

    assert all(feature.listing_trading_days == 61 for feature in features)
    assert all(feature.sector_positive_peer_count == 2 for feature in features)
    assert all(not feature.single_anchor_crowded for feature in features)
    assert all(not feature.has_direct_catalyst for feature in features)
    assert all(not feature.financial_not_deteriorating for feature in features)
    assert all(feature.independent_evidence_sources == 0 for feature in features)


def test_source_warning_is_kept_as_a_risk_penalty() -> None:
    payload = _payload("300001", return_5d=0.03)
    payload["verification"]["warnings"] = ["daily_source_conflict"]

    feature = build_rule_features(
        [
            (
                1,
                "300001",
                "stock",
                "gem",
                "active",
                date(2020, 1, 1),
                payload,
                DataQuality.SINGLE_SOURCE,
            )
        ]
    )[0][1]

    assert feature.trading_anomaly
    assert feature.data_quality is DataQuality.SINGLE_SOURCE


def test_bootstrap_market_state_is_explicitly_degraded() -> None:
    decision = determine_market_state(build_degraded_market_state_input(MarketState.NORMAL))

    assert decision.state is MarketState.NORMAL
    assert decision.flags == ("STATE_DATA_DEGRADED",)


def _payload(code: str, *, return_5d: float) -> dict[str, object]:
    bars = [
        {
            "trade_date": f"2026-05-{(index % 28) + 1:02d}",
            "volume": "1000",
            "adjustment": "qfq",
        }
        for index in range(61)
    ]
    return {
        "technical": {
            "as_of": "2026-08-07",
            "avg_amount_20d": 500_000_000.0,
            "return_5d": return_5d,
            "return_20d": 0.10,
            "return_60d": 0.15,
            "distance_high_20d": -0.10,
            "volume_activity_5d": 1.1,
            "above_ma20": True,
            "amount_anomaly_days": 0,
        },
        "classification": {"domain": "main", "sector_code": "ai"},
        "sector_market": {"up_ratio_5d": 0.6, "volume_activity_median": 1.0},
        "verification": {"warnings": []},
        "source_bars": {"tencent": bars},
        "volatility_percentile": 0.5,
        "stock": {"code": code},
    }
