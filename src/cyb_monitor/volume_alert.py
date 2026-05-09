from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
import sqlite3
import time

from .models import MinuteBar
from .status import write_status


@dataclass(frozen=True)
class BaselineBar:
    volume: int
    time: datetime


@dataclass(frozen=True)
class VolumeAlert:
    code: str
    name: str
    m_volume: int
    m_time: datetime
    n_volume: int
    n_time: datetime
    ratio: float


class SQLiteVolumeAlertRunner:
    def __init__(
        self,
        db_path: Path,
        interval_seconds: int = 300,
        workers: int = 20,
        latest_bars: int = 5,
        baseline_days: int = 20,
        threshold: float = 2.0,
        status_path: Path | None = None,
    ) -> None:
        self.db_path = db_path
        self.interval_seconds = interval_seconds
        self.workers = workers
        self.latest_bars = latest_bars
        self.baseline_days = baseline_days
        self.threshold = threshold
        self.status_path = status_path
        self._baseline_date: date | None = None
        self._baselines: dict[str, BaselineBar] = {}
        self._alerted: set[tuple[str, datetime]] = set()

    def run_forever(self) -> None:
        print(
            f"volume alert runner started db={self.db_path} interval={self.interval_seconds}s "
            f"workers={self.workers} latest_bars={self.latest_bars} baseline_days={self.baseline_days} "
            f"threshold={self.threshold}",
            flush=True,
        )
        self._write_status("starting")
        while True:
            self.run_once()
            time.sleep(self.interval_seconds)

    def run_once(self) -> list[VolumeAlert]:
        started_at = datetime.now()
        today = date.today()
        if self._baseline_date != today:
            self._baselines = load_baselines(self.db_path, today, self.baseline_days)
            self._baseline_date = today
            print(f"loaded baselines: {len(self._baselines)} codes for {today}", flush=True)

        codes = load_codes(self.db_path)
        if not codes:
            print("no codes in minute_bars", flush=True)
            self._write_status("running", scanned_codes=0, baseline_codes=len(self._baselines), alerts=0)
            return []

        with ThreadPoolExecutor(max_workers=self.workers) as executor:
            alerts = list(
                filter(
                    None,
                    executor.map(self._check_code, codes),
                )
            )

        new_alerts = []
        for alert in sorted(alerts, key=lambda item: item.ratio, reverse=True):
            key = (alert.code, alert.m_time)
            if key in self._alerted:
                continue
            self._alerted.add(key)
            new_alerts.append(alert)

        if new_alerts:
            print_volume_alerts(new_alerts)
        else:
            print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] no volume alerts", flush=True)
        elapsed_ms = int((datetime.now() - started_at).total_seconds() * 1000)
        self._write_status(
            "running",
            scanned_codes=len(codes),
            baseline_codes=len(self._baselines),
            raw_alerts=len(alerts),
            new_alerts=len(new_alerts),
            last_run_elapsed_ms=elapsed_ms,
            last_run_at=started_at.strftime("%Y-%m-%d %H:%M:%S"),
        )
        return new_alerts

    def _check_code(self, code: str) -> VolumeAlert | None:
        baseline = self._baselines.get(code)
        if baseline is None or baseline.volume <= 0:
            return None

        bars = load_latest_bars(self.db_path, code, self.latest_bars)
        if len(bars) < self.latest_bars:
            return None

        m_bar = max(bars, key=lambda bar: (bar.volume, -bar.time.timestamp()))
        if m_bar.close <= m_bar.open:
            return None

        ratio = m_bar.volume / baseline.volume
        if ratio < self.threshold:
            return None

        return VolumeAlert(
            code=m_bar.code,
            name=m_bar.name,
            m_volume=m_bar.volume,
            m_time=m_bar.time,
            n_volume=baseline.volume,
            n_time=baseline.time,
            ratio=ratio,
        )

    def _write_status(self, state: str, **extra) -> None:
        if self.status_path is None:
            return
        write_status(
            self.status_path,
            {
                "state": state,
                "db_path": str(self.db_path),
                "interval_seconds": self.interval_seconds,
                "workers": self.workers,
                "latest_bars": self.latest_bars,
                "baseline_days": self.baseline_days,
                "threshold": self.threshold,
                "alerted_count": len(self._alerted),
                **extra,
            },
        )


def load_codes(db_path: Path) -> list[str]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT DISTINCT code FROM minute_bars ORDER BY code").fetchall()
    return [str(row[0]) for row in rows]


def load_latest_bars(db_path: Path, code: str, limit: int) -> list[MinuteBar]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT code, name, time, open, high, low, close, volume, amount, last_close
            FROM minute_bars
            WHERE code = ?
            ORDER BY time DESC
            LIMIT ?
            """,
            (code, limit),
        ).fetchall()

    return [
        _row_to_bar(row)
        for row in reversed(rows)
    ]


def load_baselines(db_path: Path, target_date: date, baseline_days: int) -> dict[str, BaselineBar]:
    with sqlite3.connect(db_path) as conn:
        date_rows = conn.execute(
            """
            SELECT DISTINCT substr(time, 1, 10) AS trade_date
            FROM minute_bars
            WHERE substr(time, 1, 10) < ?
            ORDER BY trade_date DESC
            LIMIT ?
            """,
            (target_date.isoformat(), baseline_days),
        ).fetchall()
        baseline_dates = [str(row[0]) for row in date_rows]
        if not baseline_dates:
            return {}

        placeholders = ",".join("?" for _ in baseline_dates)
        rows = conn.execute(
            f"""
            SELECT code, time, volume
            FROM minute_bars
            WHERE substr(time, 1, 10) IN ({placeholders})
            ORDER BY code, volume DESC, time ASC
            """,
            baseline_dates,
        ).fetchall()

    baselines: dict[str, BaselineBar] = {}
    for code, time_text, volume in rows:
        code = str(code)
        if code in baselines:
            continue
        baselines[code] = BaselineBar(
            volume=int(volume),
            time=datetime.strptime(str(time_text), "%Y-%m-%d %H:%M:%S"),
        )
    return baselines


def print_volume_alerts(alerts: list[VolumeAlert]) -> None:
    headers = ["code", "name", "m_volume", "m_time", "n_volume", "n_time", "ratio"]
    rows = [
        [
            alert.code,
            alert.name,
            str(alert.m_volume),
            f"{alert.m_time:%Y-%m-%d %H:%M:%S}",
            str(alert.n_volume),
            f"{alert.n_time:%Y-%m-%d %H:%M:%S}",
            f"{alert.ratio:.2f}",
        ]
        for alert in alerts
    ]
    _print_table(headers, rows)


def _row_to_bar(row) -> MinuteBar:
    return MinuteBar(
        code=str(row[0]),
        name=str(row[1]),
        time=datetime.strptime(str(row[2]), "%Y-%m-%d %H:%M:%S"),
        open=float(row[3]),
        high=float(row[4]),
        low=float(row[5]),
        close=float(row[6]),
        volume=int(row[7]),
        amount=float(row[8]),
        last_close=float(row[9]),
    )


def _print_table(headers: list[str], rows: list[list[str]]) -> None:
    widths = [
        max(_display_width(value) for value in column)
        for column in zip(headers, *rows)
    ]
    separator = "+".join("-" * (width + 2) for width in widths)
    print(f"+{separator}+")
    print(_format_table_row(headers, widths))
    print(f"+{separator}+")
    for row in rows:
        print(_format_table_row(row, widths))
    print(f"+{separator}+", flush=True)


def _format_table_row(row: list[str], widths: list[int]) -> str:
    cells = []
    for value, width in zip(row, widths):
        cells.append(f" {value}{' ' * (width - _display_width(value))} ")
    return f"|{'|'.join(cells)}|"


def _display_width(value: str) -> int:
    return sum(2 if ord(char) > 127 else 1 for char in value)
