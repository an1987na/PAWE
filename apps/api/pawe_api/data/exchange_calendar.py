import hashlib
import html
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from pawe_api.contracts import DataQuality
from pawe_api.data.baseline import canonical_payload_hash
from pawe_api.data.calendar import (
    TradingCalendarDay,
    TradingCalendarObservation,
    reconcile_trading_calendar,
)

_TAG_PATTERN = re.compile(r"<[^>]+>")
_RANGE_PATTERN = re.compile(
    r"(?P<start_month>\d{1,2})月(?P<start_day>\d{1,2})日[^。；]{0,30}?至"
    r"(?P<end_month>\d{1,2})月(?P<end_day>\d{1,2})日[^。；]{0,30}?休市"
)
_SINGLE_PATTERN = re.compile(r"(?<!至)(?P<month>\d{1,2})月(?P<day>\d{1,2})日[^。；]{0,20}?休市")


class ExchangeCalendarPayloadError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class AnnualClosureManifest:
    source: str
    source_url: str
    year: int
    fetched_at: datetime
    closed_dates: tuple[date, ...]
    content_hash: str


@dataclass(frozen=True, slots=True)
class TradingCalendarWrite:
    trade_date: date
    is_open: bool
    previous_open_date: date
    source: str
    quality: DataQuality
    fetched_at: datetime
    content_hash: str


def parse_annual_closure_manifest(
    payload_text: str,
    *,
    source: str,
    source_url: str,
    year: int,
    fetched_at: datetime,
) -> AnnualClosureManifest:
    text = _normalize_html_text(payload_text)
    if str(year) not in text or "休市" not in text:
        raise ExchangeCalendarPayloadError(
            "annual closure page does not identify the requested year"
        )
    closed: set[date] = set()
    for match in _RANGE_PATTERN.finditer(text):
        start = _calendar_date(year, match.group("start_month"), match.group("start_day"))
        end = _calendar_date(year, match.group("end_month"), match.group("end_day"))
        if end < start or (end - start).days > 31:
            raise ExchangeCalendarPayloadError("annual closure page contains an invalid range")
        current = start
        while current <= end:
            closed.add(current)
            current += timedelta(days=1)
    for match in _SINGLE_PATTERN.finditer(text):
        closed.add(_calendar_date(year, match.group("month"), match.group("day")))
    if not closed:
        raise ExchangeCalendarPayloadError("annual closure page contains no closure dates")
    payload: dict[str, object] = {
        "source": source,
        "source_url": source_url,
        "year": year,
        "closed_dates": [item.isoformat() for item in sorted(closed)],
    }
    return AnnualClosureManifest(
        source=source,
        source_url=source_url,
        year=year,
        fetched_at=fetched_at,
        closed_dates=tuple(sorted(closed)),
        content_hash=canonical_payload_hash(payload),
    )


def build_verified_calendar_week(
    week_id: date,
    primary: AnnualClosureManifest | None,
    backup: AnnualClosureManifest | None,
) -> tuple[TradingCalendarWrite, ...]:
    if week_id.weekday() != 0:
        raise ValueError("week_id must be a Monday")
    if primary is not None and primary.year != week_id.year:
        raise ValueError("primary calendar manifest year does not match week")
    if backup is not None and backup.year != week_id.year:
        raise ValueError("backup calendar manifest year does not match week")
    primary_observation = _observation(week_id, primary)
    backup_observation = _observation(week_id, backup)
    days = reconcile_trading_calendar(week_id, primary_observation, backup_observation)
    manifests = tuple(item for item in (primary, backup) if item is not None)
    fetched_at = (
        max(item.fetched_at for item in manifests)
        if manifests
        else datetime.min.replace(tzinfo=UTC)
    )
    source = "+".join(item.source for item in manifests) or "missing"
    return tuple(
        TradingCalendarWrite(
            trade_date=day.calendar_date,
            is_open=day.is_open,
            previous_open_date=_previous_open_date(day.calendar_date, manifests),
            source=source,
            quality=day.quality,
            fetched_at=fetched_at,
            content_hash=_calendar_day_hash(day, manifests),
        )
        for day in days
    )


def _observation(
    week_id: date,
    manifest: AnnualClosureManifest | None,
) -> TradingCalendarObservation | None:
    if manifest is None:
        return None
    closed = set(manifest.closed_dates)
    open_dates = tuple(
        week_id + timedelta(days=offset)
        for offset in range(5)
        if week_id + timedelta(days=offset) not in closed
    )
    return TradingCalendarObservation(
        source=manifest.source,
        week_id=week_id,
        open_dates=open_dates,
        quality=DataQuality.SINGLE_SOURCE,
    )


def _previous_open_date(
    target: date,
    manifests: tuple[AnnualClosureManifest, ...],
) -> date:
    closed_sets = [set(item.closed_dates) for item in manifests]
    candidate = target - timedelta(days=1)
    while candidate.weekday() >= 5 or any(candidate in closed for closed in closed_sets):
        candidate -= timedelta(days=1)
    return candidate


def _calendar_day_hash(
    day: TradingCalendarDay,
    manifests: tuple[AnnualClosureManifest, ...],
) -> str:
    value = "|".join(
        [
            day.calendar_date.isoformat(),
            str(day.is_open),
            day.quality.value,
            *(item.content_hash for item in manifests),
        ]
    )
    return hashlib.sha256(value.encode()).hexdigest()


def _normalize_html_text(payload_text: str) -> str:
    without_tags = _TAG_PATTERN.sub(" ", payload_text)
    return re.sub(r"\s+", "", html.unescape(without_tags))


def _calendar_date(year: int, month: str, day: str) -> date:
    try:
        return date(year, int(month), int(day))
    except ValueError as exc:
        raise ExchangeCalendarPayloadError("annual closure page contains an invalid date") from exc
