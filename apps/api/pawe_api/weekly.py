from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from pawe_api.contracts import DataQuality
from pawe_api.data.calendar import TradingWeekAssessment, TradingWeekSchedule
from pawe_api.data.snapshot import FrozenSnapshot
from pawe_api.rules.engine import RuleRunResult, run_v9_rules
from pawe_api.rules.market_state import MarketStateInput
from pawe_api.rules.models import RuleFeatures


class WeeklyRunGate(StrEnum):
    READY_FOR_APPROVAL = "ready_for_approval"
    CALENDAR_INELIGIBLE = "calendar_ineligible"
    CALENDAR_DATA_DEGRADED = "calendar_data_degraded"
    PREPARATION_WINDOW_NOT_OPEN = "preparation_window_not_open"
    PUBLICATION_DEADLINE_PASSED = "publication_deadline_passed"
    SNAPSHOT_CUTOFF_MISMATCH = "snapshot_cutoff_mismatch"
    SNAPSHOT_LOCKED_TOO_LATE = "snapshot_locked_too_late"
    RULE_BLOCKED = "rule_blocked"


@dataclass(frozen=True, slots=True)
class WeeklyPreparation:
    gate: WeeklyRunGate
    reason: str | None
    rule_result: RuleRunResult | None


def prepare_weekly_rule_baseline(
    *,
    now: datetime,
    assessment: TradingWeekAssessment,
    schedule: TradingWeekSchedule | None,
    snapshot: FrozenSnapshot | None,
    features: list[RuleFeatures],
    market_state_input: MarketStateInput,
    candidate_overheat_ratio: float = 0.0,
    formal_end: datetime | None = None,
) -> WeeklyPreparation:
    if not assessment.qualifies or schedule is None:
        return WeeklyPreparation(
            WeeklyRunGate.CALENDAR_INELIGIBLE,
            assessment.reason or "TRADING_WEEK_INELIGIBLE",
            None,
        )
    if assessment.data_quality is DataQuality.DEGRADED:
        return WeeklyPreparation(
            WeeklyRunGate.CALENDAR_DATA_DEGRADED,
            "BACKUP_ONLY_CALENDAR_REQUIRES_MANUAL_RESOLUTION",
            None,
        )
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("weekly preparation now must be timezone-aware")
    local_now = now.astimezone(schedule.publication_deadline.tzinfo)
    effective_formal_end = formal_end or datetime.combine(
        assessment.week_id + timedelta(days=7),
        datetime.min.time(),
        tzinfo=schedule.publication_deadline.tzinfo,
    )
    if local_now >= effective_formal_end:
        return WeeklyPreparation(
            WeeklyRunGate.PUBLICATION_DEADLINE_PASSED,
            "NEXT_TRADING_WEEK_STARTED",
            None,
        )
    if local_now <= schedule.decision_cutoff:
        return WeeklyPreparation(
            WeeklyRunGate.PREPARATION_WINDOW_NOT_OPEN,
            "WEEKLY_PREPARATION_REQUIRES_COMPLETED_DECISION_CUTOFF",
            None,
        )
    if snapshot is None or snapshot.cutoff != schedule.decision_cutoff:
        return WeeklyPreparation(
            WeeklyRunGate.SNAPSHOT_CUTOFF_MISMATCH,
            "SNAPSHOT_MUST_USE_PREVIOUS_TRADING_DAY_CLOSE",
            None,
        )
    if snapshot.locked_at >= effective_formal_end:
        return WeeklyPreparation(
            WeeklyRunGate.SNAPSHOT_LOCKED_TOO_LATE,
            "SNAPSHOT_LOCKED_AFTER_FORMAL_WEEK_ENDED",
            None,
        )

    rule_result = run_v9_rules(
        snapshot=snapshot,
        features=features,
        market_state_input=market_state_input,
        candidate_overheat_ratio=candidate_overheat_ratio,
    )
    if not rule_result.auto_publish_allowed:
        return WeeklyPreparation(
            WeeklyRunGate.RULE_BLOCKED,
            "RULE_RESULT_REQUIRES_MANUAL_RESOLUTION",
            rule_result,
        )
    return WeeklyPreparation(WeeklyRunGate.READY_FOR_APPROVAL, None, rule_result)
