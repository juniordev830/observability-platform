"""Thread-safe, dependency-free metrics and event primitives for local services.

The registry intentionally bounds label cardinality: unlimited metric labels can
exhaust memory even on otherwise healthy services.
"""
from __future__ import annotations

from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass
from math import isfinite
from re import fullmatch
from threading import RLock
from time import monotonic, time
from typing import Callable, Iterator, Mapping


class MetricError(ValueError):
    """Invalid metric configuration, label set, or observation."""


def validate_name(name: str, *, label: bool = False) -> str:
    if not isinstance(name, str):
        raise MetricError("metric and label names must be strings")
    pattern = r"[a-zA-Z_][a-zA-Z0-9_]*" if label else r"[a-zA-Z_:][a-zA-Z0-9_:]*"
    if not fullmatch(pattern, name):
        raise MetricError(f"invalid {'label' if label else 'metric'} name: {name!r}")
    if label and name.startswith("__"):
        raise MetricError("reserved label prefix")
    return name


def escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _number(value: float | int, *, nonnegative: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MetricError("observations must be numeric")
    result = float(value)
    if not isfinite(result):
        raise MetricError("observations must be finite")
    if nonnegative and result < 0:
        raise MetricError("value cannot be negative")
    return result


def _format_number(value: float) -> str:
    return str(int(value)) if value.is_integer() else format(value, ".12g")


@dataclass(frozen=True)
class MetricDefinition:
    name: str
    kind: str
    help: str
    labels: tuple[str, ...] = ()
    buckets: tuple[float, ...] = ()

    def __post_init__(self) -> None:
        validate_name(self.name)
        if self.kind not in {"counter", "gauge", "histogram"}:
            raise MetricError("unknown metric kind")
        if not isinstance(self.help, str) or not self.help.strip():
            raise MetricError("metric help must be nonempty")
        if len(set(self.labels)) != len(self.labels):
            raise MetricError("duplicate label names")
        for label in self.labels:
            validate_name(label, label=True)
        if self.kind == "histogram":
            if "le" in self.labels:
                raise MetricError("histograms reserve the le label")
            if not self.buckets:
                raise MetricError("histograms require finite buckets")
            values = tuple(_number(bucket) for bucket in self.buckets)
            if values != tuple(sorted(set(values))):
                raise MetricError("buckets must be strictly increasing")
        elif self.buckets:
            raise MetricError("only histograms accept buckets")


class MetricRegistry:
    """Stores a bounded set of labelled time-series, exposing Prometheus 0.0.4."""

    def __init__(self, max_series: int = 1000):
        if not isinstance(max_series, int) or isinstance(max_series, bool) or max_series < 1:
            raise MetricError("max_series must be a positive integer")
        self.max_series = max_series
        self._definitions: dict[str, MetricDefinition] = {}
        self._values: dict[tuple[str, tuple[str, ...]], object] = {}
        self._lock = RLock()

    def register_counter(self, name: str, help: str, labels: tuple[str, ...] = ()) -> None:
        self._register(MetricDefinition(name, "counter", help, tuple(labels)))

    def register_gauge(self, name: str, help: str, labels: tuple[str, ...] = ()) -> None:
        self._register(MetricDefinition(name, "gauge", help, tuple(labels)))

    def register_histogram(
        self, name: str, help: str, labels: tuple[str, ...] = (),
        buckets: tuple[float, ...] = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
    ) -> None:
        self._register(MetricDefinition(name, "histogram", help, tuple(labels), tuple(buckets)))

    def _register(self, definition: MetricDefinition) -> None:
        with self._lock:
            if definition.name in self._definitions:
                raise MetricError("metric already registered")
            names = set(self._definitions)
            generated = {definition.name, definition.name + "_bucket",
                         definition.name + "_count", definition.name + "_sum"}
            if names.intersection(generated):
                raise MetricError("metric collides with another series name")
            for existing in self._definitions.values():
                if existing.kind == "histogram" and existing.name in generated:
                    raise MetricError("histogram series name collision")
                if existing.kind == "histogram" and definition.name in {
                    existing.name + "_bucket", existing.name + "_count", existing.name + "_sum"
                }:
                    raise MetricError("reserved histogram series name")
            self._definitions[definition.name] = definition

    def _key(self, name: str, labels: Mapping[str, str] | None) -> tuple[MetricDefinition, tuple[str, tuple[str, ...]]]:
        definition = self._definitions.get(name)
        if definition is None:
            raise MetricError(f"unregistered metric: {name}")
        labels = {} if labels is None else labels
        if not isinstance(labels, Mapping) or set(labels) != set(definition.labels):
            raise MetricError("label names do not match metric definition")
        values = tuple(labels[label] for label in definition.labels)
        if any(not isinstance(value, str) for value in values):
            raise MetricError("label values must be strings")
        if any(len(value) > 200 for value in values):
            raise MetricError("label value too long")
        return definition, (name, values)

    def _series(self, key: tuple[str, tuple[str, ...]], definition: MetricDefinition):
        if key not in self._values:
            if len(self._values) >= self.max_series:
                raise MetricError("metric series cardinality limit exceeded")
            if definition.kind == "histogram":
                self._values[key] = {"count": 0, "sum": 0.0,
                                     "buckets": [0] * len(definition.buckets)}
            else:
                self._values[key] = 0.0
        return self._values[key]

    def increment(self, name: str, amount: float = 1, labels: Mapping[str, str] | None = None) -> None:
        amount = _number(amount, nonnegative=True)
        with self._lock:
            definition, key = self._key(name, labels)
            if definition.kind != "counter":
                raise MetricError("increment requires a counter")
            self._values[key] = self._series(key, definition) + amount

    def set_gauge(self, name: str, value: float, labels: Mapping[str, str] | None = None) -> None:
        value = _number(value)
        with self._lock:
            definition, key = self._key(name, labels)
            if definition.kind != "gauge":
                raise MetricError("set_gauge requires a gauge")
            self._series(key, definition)
            self._values[key] = value

    def adjust_gauge(self, name: str, delta: float, labels: Mapping[str, str] | None = None) -> None:
        delta = _number(delta)
        with self._lock:
            definition, key = self._key(name, labels)
            if definition.kind != "gauge":
                raise MetricError("adjust_gauge requires a gauge")
            self._values[key] = self._series(key, definition) + delta

    def observe(self, name: str, value: float, labels: Mapping[str, str] | None = None) -> None:
        value = _number(value)
        with self._lock:
            definition, key = self._key(name, labels)
            if definition.kind != "histogram":
                raise MetricError("observe requires a histogram")
            data = self._series(key, definition)
            data["count"] += 1
            data["sum"] += value
            for index, bound in enumerate(definition.buckets):
                if value <= bound:
                    data["buckets"][index] += 1

    @contextmanager
    def time(self, name: str, labels: Mapping[str, str] | None = None,
             clock: Callable[[], float] = monotonic) -> Iterator[None]:
        started = clock()
        try:
            yield
        finally:
            self.observe(name, max(0.0, clock() - started), labels)

    def reset(self, name: str | None = None) -> None:
        with self._lock:
            if name is not None and name not in self._definitions:
                raise MetricError("unregistered metric")
            if name is None:
                self._values.clear()
            else:
                for key in list(self._values):
                    if key[0] == name:
                        del self._values[key]

    def snapshot(self) -> dict:
        with self._lock:
            output = {}
            for name, definition in sorted(self._definitions.items()):
                series = []
                for (series_name, values), data in sorted(self._values.items()):
                    if series_name != name:
                        continue
                    record = {"labels": dict(zip(definition.labels, values))}
                    if definition.kind == "histogram":
                        record.update(count=data["count"], sum=data["sum"],
                                      buckets=dict(zip(map(str, definition.buckets), data["buckets"])))
                    else:
                        record["value"] = data
                    series.append(record)
                output[name] = {"type": definition.kind, "help": definition.help, "series": series}
            return output

    def render(self) -> str:
        with self._lock:
            lines: list[str] = []
            for name, definition in sorted(self._definitions.items()):
                description = definition.help.replace("\\", "\\\\").replace("\n", "\\n")
                lines.extend((f"# HELP {name} {description}", f"# TYPE {name} {definition.kind}"))
                for (series_name, values), data in sorted(self._values.items()):
                    if series_name != name:
                        continue
                    labels = dict(zip(definition.labels, values))
                    if definition.kind != "histogram":
                        lines.append(f"{name}{_render_labels(labels)} {_format_number(data)}")
                        continue
                    for index, bound in enumerate(definition.buckets):
                        bucket_labels = {**labels, "le": _format_number(bound)}
                        lines.append(f"{name}_bucket{_render_labels(bucket_labels)} {data['buckets'][index]}")
                    lines.append(f"{name}_bucket{_render_labels({**labels, 'le': '+Inf'})} {data['count']}")
                    lines.append(f"{name}_sum{_render_labels(labels)} {_format_number(data['sum'])}")
                    lines.append(f"{name}_count{_render_labels(labels)} {data['count']}")
            return "\n".join(lines) + ("\n" if lines else "")


def _render_labels(labels: Mapping[str, str]) -> str:
    if not labels:
        return ""
    return "{" + ",".join(f'{key}="{escape_label(value)}"' for key, value in sorted(labels.items())) + "}"


class RollingWindow:
    """Bounded event samples in monotonic time for rates and percentiles."""

    def __init__(self, seconds: float, capacity: int = 10000,
                 clock: Callable[[], float] = monotonic):
        self.seconds = _number(seconds)
        if self.seconds <= 0:
            raise ValueError("seconds must be positive")
        if not isinstance(capacity, int) or isinstance(capacity, bool) or capacity < 1:
            raise ValueError("capacity must be positive")
        self.capacity = capacity
        self.clock = clock
        self._samples: deque[tuple[float, float]] = deque()
        self._lock = RLock()

    def _trim(self, now: float) -> None:
        cutoff = now - self.seconds
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()

    def add(self, value: float = 1) -> None:
        value = _number(value)
        with self._lock:
            now = self.clock()
            self._trim(now)
            self._samples.append((now, value))
            while len(self._samples) > self.capacity:
                self._samples.popleft()

    def values(self) -> list[float]:
        with self._lock:
            self._trim(self.clock())
            return [value for _, value in self._samples]

    def count(self) -> int:
        return len(self.values())

    def total(self) -> float:
        return sum(self.values())

    def average(self) -> float | None:
        values = self.values()
        return sum(values) / len(values) if values else None

    def rate(self) -> float:
        return self.count() / self.seconds

    def percentile(self, percent: float) -> float | None:
        percent = _number(percent)
        if not 0 <= percent <= 100:
            raise ValueError("percent must be between 0 and 100")
        values = sorted(self.values())
        if not values:
            return None
        position = (len(values) - 1) * percent / 100
        lower = int(position)
        upper = min(lower + 1, len(values) - 1)
        return values[lower] + (values[upper] - values[lower]) * (position - lower)

    def summary(self) -> dict:
        values = self.values()
        return {"count": len(values), "total": sum(values),
                "average": sum(values) / len(values) if values else None,
                "p50": self.percentile(50), "p95": self.percentile(95),
                "rate_per_second": len(values) / self.seconds}


@dataclass(frozen=True)
class Event:
    sequence: int
    timestamp: float
    category: str
    message: str
    attributes: dict[str, str]

    def to_dict(self) -> dict:
        return {"sequence": self.sequence, "timestamp": self.timestamp,
                "category": self.category, "message": self.message,
                "attributes": dict(self.attributes)}


class EventLog:
    """In-memory audit events with bounded retention and defensive copies."""

    def __init__(self, capacity: int = 500, clock: Callable[[], float] = time):
        if not isinstance(capacity, int) or isinstance(capacity, bool) or capacity < 1:
            raise ValueError("capacity must be positive")
        self.capacity = capacity
        self.clock = clock
        self._events: deque[Event] = deque(maxlen=capacity)
        self._sequence = 0
        self._lock = RLock()

    def append(self, category: str, message: str,
               attributes: Mapping[str, str] | None = None) -> Event:
        if not isinstance(category, str) or not fullmatch(r"[a-z][a-z0-9_.-]{0,63}", category):
            raise ValueError("invalid event category")
        if not isinstance(message, str) or not message.strip() or len(message) > 500:
            raise ValueError("invalid event message")
        attributes = {} if attributes is None else attributes
        if not isinstance(attributes, Mapping) or len(attributes) > 16:
            raise ValueError("invalid event attributes")
        if any(not isinstance(k, str) or not isinstance(v, str) or len(k) > 64 or len(v) > 200
               for k, v in attributes.items()):
            raise ValueError("event attributes must be bounded strings")
        with self._lock:
            self._sequence += 1
            event = Event(self._sequence, float(self.clock()), category,
                          message.strip(), dict(attributes))
            self._events.append(event)
            return event

    def list(self, *, category: str | None = None, after: int = 0,
             limit: int = 100) -> list[dict]:
        if not isinstance(after, int) or after < 0:
            raise ValueError("after must be nonnegative")
        if not isinstance(limit, int) or not 1 <= limit <= self.capacity:
            raise ValueError("invalid event limit")
        with self._lock:
            result = [event.to_dict() for event in self._events
                      if event.sequence > after and (category is None or event.category == category)]
            return result[-limit:]

    def count(self) -> int:
        with self._lock:
            return len(self._events)

    def clear(self) -> None:
        with self._lock:
            self._events.clear()
