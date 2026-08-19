from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any


class ProviderHealthStatus(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    AUTH_FAILED = "auth_failed"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ProviderHealthKey:
    provider: str
    host: str | None = None
    service: str | None = None


@dataclass
class ProviderHealthState:
    status: ProviderHealthStatus = ProviderHealthStatus.UNKNOWN
    failure_count: int = 0
    last_failure: datetime | None = None
    cooldown_until: datetime | None = None
    last_success: datetime | None = None


class ProviderHealthCache:
    def __init__(
        self,
        *,
        failure_threshold: int = 2,
        cooldown: timedelta = timedelta(minutes=5),
    ) -> None:
        self.failure_threshold = failure_threshold
        self.cooldown = cooldown
        self._states: dict[ProviderHealthKey, ProviderHealthState] = {}

    def state(
        self,
        provider: str | ProviderHealthKey,
        *,
        host: str | None = None,
        service: str | None = None,
    ) -> ProviderHealthState:
        key = _health_key(provider, host=host, service=service)
        return self._states.setdefault(key, ProviderHealthState())

    def can_attempt(
        self,
        provider: str | ProviderHealthKey,
        *,
        host: str | None = None,
        service: str | None = None,
    ) -> bool:
        state = self.state(provider, host=host, service=service)
        return state.cooldown_until is None or datetime.utcnow() >= state.cooldown_until

    def record_success(
        self,
        provider: str | ProviderHealthKey,
        *,
        host: str | None = None,
        service: str | None = None,
    ) -> None:
        key = _health_key(provider, host=host, service=service)
        self._states[key] = ProviderHealthState(
            status=ProviderHealthStatus.HEALTHY,
            last_success=datetime.utcnow(),
        )

    def record_failure(
        self,
        provider: str | ProviderHealthKey,
        *,
        status_code: int | None = None,
        host: str | None = None,
        service: str | None = None,
    ) -> None:
        if status_code == 404:
            return
        state = self.state(provider, host=host, service=service)
        state.failure_count += 1
        state.last_failure = datetime.utcnow()
        if status_code == 401 or status_code == 403:
            state.status = ProviderHealthStatus.AUTH_FAILED
            state.cooldown_until = state.last_failure + self.cooldown
        elif status_code == 429:
            state.status = ProviderHealthStatus.DEGRADED
            state.cooldown_until = state.last_failure + self.cooldown
        elif state.failure_count >= self.failure_threshold:
            state.status = ProviderHealthStatus.UNHEALTHY
            state.cooldown_until = state.last_failure + self.cooldown
        else:
            state.status = ProviderHealthStatus.DEGRADED


def _health_key(
    provider: str | ProviderHealthKey,
    *,
    host: str | None,
    service: str | None,
) -> ProviderHealthKey:
    if isinstance(provider, ProviderHealthKey):
        return provider
    return ProviderHealthKey(provider=provider, host=host, service=service)


@dataclass
class RemoteDiscoveryCache:
    ttl: timedelta = timedelta(minutes=15)
    _entries: dict[tuple[str, str], tuple[datetime, Any]] = field(default_factory=dict)

    def get(self, provider: str, key: str) -> Any | None:
        entry = self._entries.get((provider, key))
        if not entry:
            return None
        timestamp, value = entry
        if datetime.utcnow() - timestamp > self.ttl:
            self._entries.pop((provider, key), None)
            return None
        return value

    def set(self, provider: str, key: str, value: Any) -> Any:
        self._entries[(provider, key)] = (datetime.utcnow(), value)
        return value
