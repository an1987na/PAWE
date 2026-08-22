from datetime import UTC, date, datetime

import pytest
from pawe_api.contracts import DataQuality
from pawe_api.data.exchange_calendar import (
    ExchangeCalendarPayloadError,
    build_verified_calendar_week,
    parse_annual_closure_manifest,
)

FETCHED_AT = datetime(2026, 8, 9, 8, tzinfo=UTC)


def test_exchange_manifests_verify_normal_and_holiday_weeks() -> None:
    primary = _manifest("sse")
    backup = _manifest("szse")

    normal = build_verified_calendar_week(date(2026, 8, 10), primary, backup)
    holiday = build_verified_calendar_week(date(2026, 9, 21), primary, backup)

    assert [row.trade_date for row in normal if row.is_open] == [
        date(2026, 8, 10),
        date(2026, 8, 11),
        date(2026, 8, 12),
        date(2026, 8, 13),
        date(2026, 8, 14),
    ]
    assert {row.quality for row in normal} == {DataQuality.VERIFIED}
    assert [row.trade_date for row in holiday if row.is_open] == [
        date(2026, 9, 21),
        date(2026, 9, 22),
        date(2026, 9, 23),
        date(2026, 9, 24),
    ]


def test_exchange_manifest_conflict_is_not_silently_accepted() -> None:
    primary = _manifest("sse")
    conflicting = parse_annual_closure_manifest(
        "<p>2026年休市安排</p><p>8月10日至8月10日休市。</p>",
        source="szse",
        source_url="https://example.invalid/szse",
        year=2026,
        fetched_at=FETCHED_AT,
    )

    rows = build_verified_calendar_week(date(2026, 8, 10), primary, conflicting)

    assert {row.quality for row in rows} == {DataQuality.CONFLICTED}
    assert not any(row.is_open for row in rows)


def test_exchange_manifest_rejects_unidentified_payload() -> None:
    with pytest.raises(ExchangeCalendarPayloadError, match="requested year"):
        parse_annual_closure_manifest(
            "<html>temporarily unavailable</html>",
            source="sse",
            source_url="https://example.invalid/sse",
            year=2026,
            fetched_at=FETCHED_AT,
        )


def _manifest(source: str):
    return parse_annual_closure_manifest(
        """
        <h2>2026年休市安排</h2>
        <p>元旦：1月1日至1月3日休市，1月5日起照常开市。</p>
        <p>春节：2月15日至2月23日休市，2月24日起照常开市。</p>
        <p>清明节：4月4日至4月6日休市。</p>
        <p>劳动节：5月1日至5月5日休市。</p>
        <p>端午节：6月19日至6月21日休市。</p>
        <p>中秋节：9月25日至9月27日休市。</p>
        <p>国庆节：10月1日至10月7日休市。</p>
        """,
        source=source,
        source_url=f"https://example.invalid/{source}",
        year=2026,
        fetched_at=FETCHED_AT,
    )
