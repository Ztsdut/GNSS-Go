from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum


class PlanStage(StrEnum):
    PREPARING = "preparing"
    RESOLVING_STATIONS = "resolving_stations"
    RESOLVING_SOURCES = "resolving_sources"
    CHECKING_AVAILABILITY = "checking_availability"
    BUILDING_TASKS = "building_tasks"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ERROR = "error"


@dataclass(frozen=True)
class PlanEstimate:
    station_count: int | None
    day_count: int | None
    candidate_file_count: int | None
    large: bool = False


@dataclass(frozen=True)
class PlanProgress:
    stage: PlanStage
    message: str
    elapsed_seconds: float
    completed: int | None = None
    total: int | None = None
    cancelable: bool = True
    estimate: PlanEstimate | None = None


class PlanCancelledError(Exception):
    """Raised when a user cancels an in-progress planning operation."""


PlanProgressCallback = Callable[[PlanProgress], None]
