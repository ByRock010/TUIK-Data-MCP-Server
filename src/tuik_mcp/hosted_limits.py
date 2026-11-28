"""Single-worker hosted admission counters with bounded memory."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import monotonic


@dataclass
class Counter:
    window: object = None
    used: int = 0

    def available(self, window, cap: int) -> bool:
        if self.window != window:
            self.window = window
            self.used = 0
        return self.used < cap


@dataclass
class RequestBudget:
    minute: Counter = field(default_factory=Counter)
    day: Counter = field(default_factory=Counter)
    month: Counter = field(default_factory=Counter)


class Admission:
    def __init__(self):
        self.connections = 0
        self.introspections = 0
        self.in_flight: set[str] = set()
        self.global_budget = RequestBudget()
        self.callers: dict[str, RequestBudget] = {}
        self.parse_day = Counter()
        self.parse_callers: dict[str, Counter] = {}
        self.ips: dict[str, int] = {}
        self.ip_minute = None

    def failed_auth(self, ip: str) -> bool:
        minute = int(monotonic()) // 60
        if minute != self.ip_minute:
            self.ips.clear()
            self.ip_minute = minute
        if len(self.ips) >= 256 and ip not in self.ips:
            return False
        used = self.ips.get(ip, 0)
        if used >= 30:
            return False
        self.ips[ip] = used + 1
        return True

    def request(self, caller: str) -> bool:
        # No await between the check and updates. One event loop owns these counters.
        local = self.callers.setdefault(caller, RequestBudget())
        now = datetime.now(UTC)
        minute, day, month = int(now.timestamp()) // 60, now.date(), (now.year, now.month)
        rules = (
            (local.minute, minute, 10),
            (local.day, day, 60),
            (self.global_budget.minute, minute, 60),
            (self.global_budget.day, day, 500),
            (self.global_budget.month, month, 10_000),
        )
        if not all(counter.available(window, cap) for counter, window, cap in rules):
            return False
        for counter, _, _ in rules:
            counter.used += 1
        return True

    def reserve_parse(self, caller: str) -> bool:
        # Charge the expanded 32 MiB bound when cache/expansion is unknown.
        daily = self.parse_callers.setdefault(caller, Counter())
        today = datetime.now(UTC).date()
        if not daily.available(today, 2) or not self.parse_day.available(today, 8):
            return False
        daily.used += 1
        self.parse_day.used += 1
        return True

    def start_call(self, caller: str) -> bool:
        if len(self.in_flight) >= 4 or caller in self.in_flight:
            return False
        self.in_flight.add(caller)
        return True
