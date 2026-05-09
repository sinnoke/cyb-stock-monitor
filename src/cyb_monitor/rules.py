from __future__ import annotations

from datetime import datetime, timedelta

from .config import RuleConfig
from .models import Alert, MinuteBar


class AlertEngine:
    def __init__(self, config: RuleConfig, volume_baselines: dict[str, int] | None = None) -> None:
        self.config = config
        self._volume_baselines = volume_baselines or {}
        self._last_alert_at: dict[tuple[str, str], datetime] = {}

    def set_volume_baselines(self, volume_baselines: dict[str, int]) -> None:
        self._volume_baselines = volume_baselines

    def on_bar(self, bar: MinuteBar) -> list[Alert]:
        baseline = self._volume_baselines.get(bar.code, 0)
        if baseline <= 0:
            return []

        multiple = bar.volume / baseline
        if multiple < self.config.volume_multiple:
            return []

        alert = self._build_alert(
            bar,
            "volume_gt_5d_max",
            (
                f"[创业板成交量异动] {bar.code} {bar.name} "
                f"时间={bar.time:%Y-%m-%d %H:%M} "
                f"当前1分钟成交量={bar.volume} "
                f"近5日最大1分钟成交量={baseline} "
                f"倍数={multiple:.2f}"
            ),
        )
        return [alert] if self._allow(alert) else []

    def _allow(self, alert: Alert) -> bool:
        key = (alert.code, alert.rule_name)
        cooldown = timedelta(minutes=self.config.alert_cooldown_minutes)
        last = self._last_alert_at.get(key)
        if last and alert.triggered_at - last < cooldown:
            return False

        self._last_alert_at[key] = alert.triggered_at
        return True

    @staticmethod
    def _build_alert(bar: MinuteBar, rule_name: str, reason: str) -> Alert:
        return Alert(
            code=bar.code,
            name=bar.name,
            triggered_at=bar.time,
            level="warning",
            rule_name=rule_name,
            message=reason,
            bar=bar,
        )
