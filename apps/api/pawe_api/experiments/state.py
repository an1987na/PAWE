from dataclasses import dataclass


class ExperimentStateError(ValueError):
    pass


TRANSITIONS: dict[str, frozenset[str]] = {
    "schema_validated": frozenset({"replay_queued"}),
    "replay_queued": frozenset({"replay_running", "replay_failed"}),
    "replay_running": frozenset({"replay_passed", "replay_rejected", "replay_failed"}),
    "replay_passed": frozenset({"shadow_ready"}),
    "shadow_ready": frozenset({"shadow_running"}),
    "shadow_running": frozenset({"awaiting_approval", "shadow_failed"}),
    "awaiting_approval": frozenset({"approved", "replay_rejected"}),
    "approved": frozenset({"activated"}),
    "activated": frozenset({"superseded", "rolled_back"}),
}
TERMINAL_STATES = frozenset(
    {"replay_rejected", "replay_failed", "shadow_failed", "superseded", "rolled_back"}
)


@dataclass(frozen=True, slots=True)
class ExperimentTransition:
    current: str
    target: str

    def validate(self) -> None:
        if self.current in TERMINAL_STATES:
            raise ExperimentStateError(
                f"terminal experiment state cannot transition: {self.current}"
            )
        if self.target not in TRANSITIONS.get(self.current, frozenset()):
            raise ExperimentStateError(
                f"invalid experiment transition: {self.current} -> {self.target}"
            )


def require_transition(current: str, target: str) -> None:
    ExperimentTransition(current=current, target=target).validate()
