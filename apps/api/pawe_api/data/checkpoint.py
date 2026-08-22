import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path


class CheckpointError(ValueError):
    pass


@dataclass(slots=True)
class DailyIngestionCheckpoint:
    start: date
    end: date
    last_processed_code: str | None = None
    attempted_count: int = 0
    failures: dict[str, str] = field(default_factory=dict)
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    version: int = 1

    def mark(self, code: str, *, error: str | None, updated_at: datetime) -> None:
        if len(code) != 6 or not code.isdigit():
            raise CheckpointError("checkpoint stock code must contain six digits")
        if updated_at.tzinfo is None or updated_at.utcoffset() is None:
            raise CheckpointError("checkpoint update time must be timezone-aware")
        if self.last_processed_code is None or code > self.last_processed_code:
            self.last_processed_code = code
        self.attempted_count += 1
        if error is None:
            self.failures.pop(code, None)
        else:
            self.failures[code] = error
        self.updated_at = updated_at


def load_daily_checkpoint(
    path: Path,
    *,
    start: date,
    end: date,
) -> DailyIngestionCheckpoint:
    if not path.exists():
        return DailyIngestionCheckpoint(start=start, end=end)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        checkpoint = DailyIngestionCheckpoint(
            version=int(payload["version"]),
            start=date.fromisoformat(str(payload["start"])),
            end=date.fromisoformat(str(payload["end"])),
            last_processed_code=payload.get("last_processed_code"),
            attempted_count=int(payload["attempted_count"]),
            failures={str(key): str(value) for key, value in payload["failures"].items()},
            updated_at=datetime.fromisoformat(str(payload["updated_at"])),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise CheckpointError("daily ingestion checkpoint is invalid") from exc
    if checkpoint.version != 1:
        raise CheckpointError("daily ingestion checkpoint version is unsupported")
    if (checkpoint.start, checkpoint.end) != (start, end):
        raise CheckpointError("daily ingestion checkpoint window does not match the run")
    return checkpoint


def save_daily_checkpoint(path: Path, checkpoint: DailyIngestionCheckpoint) -> None:
    payload = asdict(checkpoint)
    payload["start"] = checkpoint.start.isoformat()
    payload["end"] = checkpoint.end.isoformat()
    payload["updated_at"] = checkpoint.updated_at.isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
