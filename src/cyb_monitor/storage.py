from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from queue import Empty, Queue
import sqlite3
import threading

from .models import MinuteBar
from .status import write_status


class SQLiteMinuteBarStore:
    def __init__(
        self,
        path: Path,
        retention_days: int = 92,
        batch_size: int = 500,
        flush_interval_seconds: float = 1.0,
        status_path: Path | None = None,
    ) -> None:
        self.path = path
        self.retention_days = retention_days
        self.batch_size = batch_size
        self.flush_interval_seconds = flush_interval_seconds
        self.status_path = status_path
        self._queue: Queue[MinuteBar | None] = Queue()
        self._thread = threading.Thread(target=self._run, name="sqlite-minute-bar-store", daemon=True)
        self._last_prune_at: datetime | None = None
        self._written_rows = 0

    def start(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self._write_status("starting")
        self._thread.start()

    def stop(self) -> None:
        self._queue.put(None)
        self._thread.join(timeout=10)
        self._write_status("stopped")

    def enqueue(self, bar: MinuteBar) -> None:
        self._queue.put(bar)

    def _init_db(self) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS minute_bars (
                    code TEXT NOT NULL,
                    name TEXT NOT NULL,
                    time TEXT NOT NULL,
                    open REAL NOT NULL,
                    high REAL NOT NULL,
                    low REAL NOT NULL,
                    close REAL NOT NULL,
                    volume INTEGER NOT NULL,
                    amount REAL NOT NULL,
                    last_close REAL NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (code, time)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_minute_bars_time ON minute_bars(time)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_minute_bars_code_time ON minute_bars(code, time)"
            )
            conn.commit()
        self._write_status("initialized")

    def _run(self) -> None:
        conn = sqlite3.connect(self.path)
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            self._prune(conn)
            batch: list[MinuteBar] = []
            while True:
                item = self._queue.get()
                if item is None:
                    self._flush(conn, batch)
                    break

                batch.append(item)
                deadline = datetime.now() + timedelta(seconds=self.flush_interval_seconds)
                while len(batch) < self.batch_size:
                    timeout = max(0.0, (deadline - datetime.now()).total_seconds())
                    if timeout <= 0:
                        break
                    try:
                        next_item = self._queue.get(timeout=timeout)
                    except Empty:
                        break
                    if next_item is None:
                        self._flush(conn, batch)
                        return
                    batch.append(next_item)

                self._flush(conn, batch)
                self._prune_if_due(conn)
        finally:
            conn.close()

    def _flush(self, conn: sqlite3.Connection, batch: list[MinuteBar]) -> None:
        if not batch:
            return

        conn.executemany(
            """
            INSERT INTO minute_bars (
                code, name, time, open, high, low, close, volume, amount, last_close, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(code, time) DO UPDATE SET
                name=excluded.name,
                open=excluded.open,
                high=excluded.high,
                low=excluded.low,
                close=excluded.close,
                volume=excluded.volume,
                amount=excluded.amount,
                last_close=excluded.last_close,
                updated_at=CURRENT_TIMESTAMP
            """,
            [
                (
                    bar.code,
                    bar.name,
                    bar.time.strftime("%Y-%m-%d %H:%M:%S"),
                    bar.open,
                    bar.high,
                    bar.low,
                    bar.close,
                    bar.volume,
                    bar.amount,
                    bar.last_close,
                )
                for bar in batch
            ],
        )
        conn.commit()
        self._written_rows += len(batch)
        self._write_status("running", last_flush_rows=len(batch))
        batch.clear()

    def _prune_if_due(self, conn: sqlite3.Connection) -> None:
        if self._last_prune_at is None:
            self._prune(conn)
            return
        if datetime.now() - self._last_prune_at >= timedelta(hours=1):
            self._prune(conn)

    def _prune(self, conn: sqlite3.Connection) -> None:
        cutoff = datetime.now() - timedelta(days=self.retention_days)
        conn.execute("DELETE FROM minute_bars WHERE time < ?", (cutoff.strftime("%Y-%m-%d %H:%M:%S"),))
        conn.commit()
        self._last_prune_at = datetime.now()
        self._write_status("running", last_prune_at=self._last_prune_at.strftime("%Y-%m-%d %H:%M:%S"))

    def _write_status(self, state: str, **extra) -> None:
        if self.status_path is None:
            return
        write_status(
            self.status_path,
            {
                "state": state,
                "db_path": str(self.path),
                "retention_days": self.retention_days,
                "queue_size": self._queue.qsize(),
                "written_rows_since_start": self._written_rows,
                **extra,
            },
        )
