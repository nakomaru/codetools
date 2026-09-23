from dataclasses import dataclass, field

from .commands import CommandResult
from .diffs import FileChange
from .ops import Op, OpResult
from .protocol import ParseError
from .stage import Stage

MALFORMED = "malformed"
DONE = "done"
PENDING = "pending"
APPLIED = "applied"
REJECTED = "rejected"
UNDONE = "undone"


@dataclass
class Batch:
    id: int
    source: str
    ops: list[Op]
    errors: list[ParseError]
    status: str = PENDING
    results: dict[int, OpResult] = field(default_factory=dict)
    stage: Stage | None = None
    staged_changes: list[FileChange] = field(default_factory=list)
    applied_changes: list[FileChange] = field(default_factory=list)
    runs: dict[int, CommandResult] = field(default_factory=dict)
    partial: bool = False
    report: str = ""

    def of_kind(self, kind: str) -> list[Op]:
        return [op for op in self.ops if op.kind == kind]

    def gated(self) -> list[Op]:
        """Ops that wait for the operator: changes and commands."""
        return [op for op in self.ops if op.kind != "query"]

    def failed(self) -> list[Op]:
        return [op for op in self.gated() if not self.results[op.index].ok]

    def passed(self) -> list[Op]:
        return [op for op in self.gated() if self.results[op.index].ok]
