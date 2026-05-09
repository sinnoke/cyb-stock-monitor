from __future__ import annotations

from .models import Alert


class ConsoleNotifier:
    def send(self, alert: Alert) -> None:
        print(alert.message)
