from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class FutuConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 11111
    market_prefix: str = "SZ"
    kline_type: str = "K_1M"
    autype: str = "QFQ"
    subscribe_batch_size: int = 80
    max_subscribe: int = 10


class UniverseConfig(BaseModel):
    source: str = "futu"
    selection: str = "random"
    random_seed: int = 20260509
    prefixes: list[str] = Field(default_factory=lambda: ["300", "301"])
    start: int = 0
    end: int = 999
    include_codes: list[str] = Field(default_factory=list)
    exclude_codes: list[str] = Field(default_factory=list)


class RuleConfig(BaseModel):
    history_days: int = 5
    volume_multiple: float = 2.0
    alert_cooldown_minutes: int = 3


class NotifyConfig(BaseModel):
    console: bool = True


class AppConfig(BaseModel):
    futu: FutuConfig = Field(default_factory=FutuConfig)
    universe: UniverseConfig = Field(default_factory=UniverseConfig)
    rules: RuleConfig = Field(default_factory=RuleConfig)
    notify: NotifyConfig = Field(default_factory=NotifyConfig)


def load_config(path: Path) -> AppConfig:
    if not path.exists():
        raise FileNotFoundError(f"config file not found: {path}")

    with path.open("r", encoding="utf-8") as f:
        raw: dict[str, Any] = yaml.safe_load(f) or {}

    return AppConfig.model_validate(raw)
