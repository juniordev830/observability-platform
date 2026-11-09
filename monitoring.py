"""Health, SLO, alert state machines and incident lifecycle management.

All state is in-memory and intended for local observability demos, not
distributed alert coordination or durable incident storage.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from math import isfinite
from threading import RLock
from time import monotonic, time
from typing import Callable, Mapping


@dataclass(frozen=True)
class CheckResult:
    name: str
    healthy: bool
    required: bool
    duration_ms: float
    detail: str

    def to_dict(self) -> dict:
        return {"name": self.name, "healthy": self.healthy, "required": self.required,
                "duration_ms": round(self.duration_ms, 3), "detail": self.detail}


@dataclass(frozen=True)
class HealthReport:
    healthy: bool
    checks: tuple[CheckResult, ...]

    def to_dict(self) -> dict:
        required = sum(check.required for check in self.checks)
        failed_required = sum(check.required and not check.healthy for check in self.checks)
        failed_optional = sum(not check.required and not check.healthy for check in self.checks)
        return {
            "status": "healthy" if self.healthy else "unhealthy",
            "checks": [check.to_dict() for check in self.checks],
            "summary": {
                "total": len(self.checks),
                "required": required,
                "optional": len(self.checks) - required,
                "failed_required": failed_required,
                "failed_optional": failed_optional,
            },
        }


class HealthRegistry:
    """Runs registered dependency checks and reports required failures."""

    def __init__(self, clock: Callable[[], float] = monotonic):
        self.clock = clock
        self._checks: dict[str, tuple[Callable[[], bool | tuple[bool, str]], bool]] = {}
        self._lock = RLock()

    def register(self, name: str, check: Callable, *, required: bool = True) -> None:
        if not isinstance(name, str) or not name or len(name) > 80:
            raise ValueError("invalid health check name")
        if not callable(check):
            raise TypeError("health check must be callable")
        if not isinstance(required, bool):
            raise ValueError("required must be boolean")
        with self._lock:
            if name in self._checks:
                raise ValueError("health check already registered")
            self._checks[name] = (check, required)

    def unregister(self, name: str) -> bool:
        with self._lock:
            return self._checks.pop(name, None) is not None

    def names(self) -> list[str]:
        with self._lock:
            return sorted(self._checks)

    def evaluate(self) -> HealthReport:
        with self._lock:
            checks = sorted(self._checks.items())
        results = []
        for name, (check, required) in checks:
            started = self.clock()
            try:
                raw = check()
                if isinstance(raw, bool):
                    healthy, detail = raw, "ok" if raw else "failed"
                elif (isinstance(raw, tuple) and len(raw) == 2
                      and isinstance(raw[0], bool) and isinstance(raw[1], str)):
                    healthy, detail = raw
                else:
                    healthy, detail = False, "invalid check response"
            except Exception as error:
                healthy, detail = False, type(error).__name__
            duration = max(0, (self.clock() - started) * 1000)
            results.append(CheckResult(name, healthy, required, duration, detail[:200]))
        return HealthReport(all(item.healthy or not item.required for item in results),
                            tuple(results))


@dataclass(frozen=True)
class SLOSnapshot:
    target: float
    window_seconds: float
    total: int
    successful: int
    observed_ratio: float | None
    error_budget: float
    errors: int
    budget_remaining: float
    burn_rate: float
    compliant: bool | None

    def to_dict(self) -> dict:
        return vars(self).copy()


class SLOTracker:
    """Sliding-window availability SLO with an explicit finite sample cap."""

    def __init__(self, target: float = 0.999, window_seconds: float = 3600,
                 capacity: int = 100000, clock: Callable[[], float] = monotonic):
        if isinstance(target, bool) or not isinstance(target, (int, float)) or not 0 < target < 1:
            raise ValueError("target must be strictly between 0 and 1")
        if not isfinite(target):
            raise ValueError("target must be finite")
        if isinstance(window_seconds, bool) or not isinstance(window_seconds, (int, float)):
            raise ValueError("window_seconds must be numeric")
        if not isfinite(window_seconds) or window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        if not isinstance(capacity, int) or isinstance(capacity, bool) or capacity < 1:
            raise ValueError("capacity must be positive")
        self.target = float(target)
        self.window_seconds = float(window_seconds)
        self.capacity = capacity
        self.clock = clock
        self._observations: deque[tuple[float, bool]] = deque(maxlen=capacity)
        self._lock = RLock()

    def record(self, successful: bool) -> None:
        if not isinstance(successful, bool):
            raise TypeError("successful must be boolean")
        with self._lock:
            now = self.clock()
            self._trim(now)
            self._observations.append((now, successful))

    def _trim(self, now: float) -> None:
        cutoff = now - self.window_seconds
        while self._observations and self._observations[0][0] < cutoff:
            self._observations.popleft()

    def snapshot(self) -> SLOSnapshot:
        with self._lock:
            self._trim(self.clock())
            total = len(self._observations)
            successful = sum(ok for _, ok in self._observations)
        errors = total - successful
        budget = total * (1 - self.target)
        remaining = budget - errors
        ratio = successful / total if total else None
        burn = errors / budget if budget else 0.0
        return SLOSnapshot(self.target, self.window_seconds, total, successful,
                           ratio, budget, errors, remaining, burn,
                           ratio >= self.target if total else None)

    def reset(self) -> None:
        with self._lock:
            self._observations.clear()


@dataclass(frozen=True)
class AlertRule:
    name: str
    threshold: float
    operator: str = ">"
    for_seconds: float = 0.0
    severity: str = "warning"

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name or len(self.name) > 100:
            raise ValueError("invalid alert name")
        if self.operator not in {">", ">=", "<", "<=", "==", "!="}:
            raise ValueError("invalid alert operator")
        if isinstance(self.threshold, bool) or not isinstance(self.threshold, (int, float)):
            raise ValueError("threshold must be numeric")
        if not isfinite(self.threshold):
            raise ValueError("threshold must be finite")
        if not isinstance(self.for_seconds, (int, float)) or not isfinite(self.for_seconds):
            raise ValueError("for_seconds must be finite")
        if self.for_seconds < 0:
            raise ValueError("for_seconds cannot be negative")
        if self.severity not in {"info", "warning", "critical"}:
            raise ValueError("invalid severity")

    def matches(self, value: float) -> bool:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
            raise ValueError("alert observation must be finite")
        if self.operator == ">":
            return value > self.threshold
        if self.operator == ">=":
            return value >= self.threshold
        if self.operator == "<":
            return value < self.threshold
        if self.operator == "<=":
            return value <= self.threshold
        if self.operator == "==":
            return value == self.threshold
        return value != self.threshold


@dataclass(frozen=True)
class AlertTransition:
    sequence: int
    name: str
    previous: str
    current: str
    value: float
    timestamp: float
    severity: str

    def to_dict(self) -> dict:
        return vars(self).copy()


@dataclass
class _AlertRuntime:
    state: str = "inactive"
    pending_since: float | None = None
    silenced_until: float = 0.0
    last_value: float | None = None


class AlertManager:
    """Deterministic inactive/pending/firing transitions and timed silences."""

    def __init__(self, clock: Callable[[], float] = monotonic,
                 history_limit: int = 500):
        if not isinstance(history_limit, int) or history_limit < 1:
            raise ValueError("history_limit must be positive")
        self.clock = clock
        self._rules: dict[str, AlertRule] = {}
        self._states: dict[str, _AlertRuntime] = {}
        self._history: deque[AlertTransition] = deque(maxlen=history_limit)
        self._sequence = 0
        self._lock = RLock()

    def register(self, rule: AlertRule) -> None:
        if not isinstance(rule, AlertRule):
            raise TypeError("expected AlertRule")
        with self._lock:
            if rule.name in self._rules:
                raise ValueError("alert rule already registered")
            self._rules[rule.name] = rule
            self._states[rule.name] = _AlertRuntime()

    def remove(self, name: str) -> bool:
        with self._lock:
            if name not in self._rules:
                return False
            del self._rules[name]
            del self._states[name]
            return True

    def silence(self, name: str, duration_seconds: float) -> None:
        if isinstance(duration_seconds, bool) or not isinstance(duration_seconds, (int, float)):
            raise ValueError("invalid silence duration")
        if not isfinite(duration_seconds) or duration_seconds <= 0:
            raise ValueError("silence duration must be positive")
        with self._lock:
            state = self._states[name]
            state.silenced_until = max(state.silenced_until, self.clock() + duration_seconds)

    def evaluate(self, name: str, value: float) -> str:
        with self._lock:
            rule = self._rules[name]
            state = self._states[name]
            now = self.clock()
            matches = rule.matches(value)
            previous = state.state
            state.last_value = float(value)
            if not matches:
                state.state = "inactive"
                state.pending_since = None
            elif state.pending_since is None:
                state.pending_since = now
                state.state = "firing" if rule.for_seconds == 0 else "pending"
            elif now - state.pending_since >= rule.for_seconds:
                state.state = "firing"
            else:
                state.state = "pending"
            if previous != state.state:
                self._sequence += 1
                self._history.append(AlertTransition(
                    self._sequence, name, previous, state.state,
                    float(value), now, rule.severity))
            return state.state

    def evaluate_all(self, observations: Mapping[str, float]) -> dict[str, str]:
        if not isinstance(observations, Mapping):
            raise TypeError("observations must be a mapping")
        with self._lock:
            unknown = set(observations) - set(self._rules)
            if unknown:
                raise KeyError(f"unknown alert rules: {sorted(unknown)}")
            return {name: self.evaluate(name, value) for name, value in observations.items()}

    def status(self) -> dict[str, dict]:
        with self._lock:
            now = self.clock()
            return {
                name: {"state": state.state, "severity": self._rules[name].severity,
                       "silenced": now < state.silenced_until,
                       "silenced_until": state.silenced_until,
                       "last_value": state.last_value,
                       "threshold": self._rules[name].threshold,
                       "operator": self._rules[name].operator}
                for name, state in sorted(self._states.items())
            }

    def active(self, *, include_silenced: bool = False) -> dict[str, dict]:
        return {name: details for name, details in self.status().items()
                if details["state"] == "firing" and
                (include_silenced or not details["silenced"])}

    def history(self, after: int = 0, limit: int = 100) -> list[dict]:
        if not isinstance(after, int) or after < 0:
            raise ValueError("after must be nonnegative")
        if not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be positive")
        with self._lock:
            return [item.to_dict() for item in self._history if item.sequence > after][-limit:]


@dataclass
class Incident:
    id: int
    title: str
    severity: str
    created_at: float
    state: str = "open"
    acknowledged_at: float | None = None
    resolved_at: float | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"id": self.id, "title": self.title, "severity": self.severity,
                "created_at": self.created_at, "state": self.state,
                "acknowledged_at": self.acknowledged_at, "resolved_at": self.resolved_at,
                "notes": list(self.notes)}


class IncidentStore:
    """In-memory incident tracker with validated lifecycle transitions."""

    def __init__(self, clock: Callable[[], float] = time, max_incidents: int = 1000):
        if not isinstance(max_incidents, int) or max_incidents < 1:
            raise ValueError("max_incidents must be positive")
        self.clock = clock
        self.max_incidents = max_incidents
        self._incidents: dict[int, Incident] = {}
        self._next_id = 1
        self._lock = RLock()

    def create(self, title: str, severity: str = "warning") -> dict:
        if not isinstance(title, str) or not title.strip() or len(title) > 200:
            raise ValueError("invalid incident title")
        if severity not in {"info", "warning", "critical"}:
            raise ValueError("invalid incident severity")
        with self._lock:
            if len(self._incidents) >= self.max_incidents:
                raise RuntimeError("incident capacity reached")
            incident = Incident(self._next_id, title.strip(), severity, float(self.clock()))
            self._incidents[incident.id] = incident
            self._next_id += 1
            return incident.to_dict()

    def get(self, incident_id: int) -> dict:
        with self._lock:
            return self._incidents[incident_id].to_dict()

    def acknowledge(self, incident_id: int) -> dict:
        with self._lock:
            incident = self._incidents[incident_id]
            if incident.state == "resolved":
                raise ValueError("cannot acknowledge resolved incident")
            if incident.state == "open":
                incident.state = "acknowledged"
                incident.acknowledged_at = float(self.clock())
            return incident.to_dict()

    def resolve(self, incident_id: int, note: str | None = None) -> dict:
        with self._lock:
            incident = self._incidents[incident_id]
            if incident.state == "resolved":
                raise ValueError("incident already resolved")
            if note is not None:
                self._append_note(incident, note)
            incident.state = "resolved"
            incident.resolved_at = float(self.clock())
            return incident.to_dict()

    def add_note(self, incident_id: int, note: str) -> dict:
        with self._lock:
            incident = self._incidents[incident_id]
            self._append_note(incident, note)
            return incident.to_dict()

    @staticmethod
    def _append_note(incident: Incident, note: str) -> None:
        if not isinstance(note, str) or not note.strip() or len(note) > 500:
            raise ValueError("invalid incident note")
        if len(incident.notes) >= 100:
            raise RuntimeError("incident note limit reached")
        incident.notes.append(note.strip())

    def list(self, state: str | None = None, severity: str | None = None) -> list[dict]:
        if state is not None and state not in {"open", "acknowledged", "resolved"}:
            raise ValueError("invalid state filter")
        if severity is not None and severity not in {"info", "warning", "critical"}:
            raise ValueError("invalid severity filter")
        with self._lock:
            return [incident.to_dict() for incident in self._incidents.values()
                    if (state is None or incident.state == state) and
                    (severity is None or incident.severity == severity)]

    def summary(self) -> dict:
        with self._lock:
            return {"total": len(self._incidents),
                    "open": sum(item.state == "open" for item in self._incidents.values()),
                    "acknowledged": sum(item.state == "acknowledged" for item in self._incidents.values()),
                    "resolved": sum(item.state == "resolved" for item in self._incidents.values())}
