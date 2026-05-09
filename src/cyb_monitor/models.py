from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class MinuteBar:
    code: str
    name: str
    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    amount: float
    last_close: float


@dataclass(frozen=True)
class Alert:
    code: str
    name: str
    triggered_at: datetime
    level: str
    rule_name: str
    message: str
    bar: MinuteBar

