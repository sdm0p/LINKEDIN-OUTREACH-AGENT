import time
import uuid
from dataclasses import dataclass, field


@dataclass
class TraceEntry:
    stage: str
    detail: str
    ts: float = field(default_factory=time.time)


@dataclass
class RunTrace:
    """Collects stage-level messages as a run executes. The frontend polls
    the run resource and renders entries in the vertical stepper."""

    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    entries: list[TraceEntry] = field(default_factory=list)

    def log(self, stage: str, detail: str) -> None:
        self.entries.append(TraceEntry(stage=stage, detail=detail))

    def as_list(self) -> list[dict]:
        return [
            {"stage": e.stage, "detail": e.detail, "ts": e.ts} for e in self.entries
        ]
