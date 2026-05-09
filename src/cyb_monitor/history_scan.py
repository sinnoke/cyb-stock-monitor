from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class VolumeSpikeResult:
    code: str
    name: str
    target_max_time: datetime
    target_max_volume: int
    baseline_max_time: datetime
    baseline_max_volume: int
    ratio: float


def build_sh_code_range(start: int = 600001, end: int = 600999) -> list[str]:
    if end < start:
        raise ValueError("end must be greater than or equal to start")
    return [f"SH.{code:06d}" for code in range(start, end + 1)]


def scan_history_volume_spikes(
    quote_ctx,
    codes: Iterable[str],
    autype_name: str = "QFQ",
    threshold: float = 3.0,
    trade_days: int = 20,
    target_date: date | None = None,
    lookback_calendar_days: int = 80,
    page_size: int = 1000,
    max_pages: int = 20,
) -> list[VolumeSpikeResult]:
    if trade_days < 2:
        raise ValueError("trade_days must be at least 2")

    code_list = list(codes)
    total = len(code_list)
    results: list[VolumeSpikeResult] = []
    for index, code in enumerate(code_list, start=1):
        print(f"checking {index}/{total} {code}", flush=True)
        data = request_recent_1m_klines(
            quote_ctx,
            code,
            autype_name=autype_name,
            end_date=target_date,
            lookback_calendar_days=lookback_calendar_days,
            page_size=page_size,
            max_pages=max_pages,
        )
        result = select_volume_spike(
            data,
            threshold=threshold,
            trade_days=trade_days,
            target_date=target_date,
        )
        if result is not None:
            results.append(result)

        print(f"checked {index}/{total} {code}; matched={len(results)}", flush=True)

    return results


def request_recent_1m_klines(
    quote_ctx,
    code: str,
    autype_name: str = "QFQ",
    end_date: date | None = None,
    lookback_calendar_days: int = 80,
    page_size: int = 1000,
    max_pages: int = 20,
):
    from futu import AuType, KLType, RET_OK

    end = end_date or date.today()
    start = end - timedelta(days=lookback_calendar_days)
    autype = getattr(AuType, autype_name)
    page_req_key = None
    frames = []

    for _ in range(max_pages):
        ret, data, page_req_key = quote_ctx.request_history_kline(
            code,
            start=start.isoformat(),
            end=end.isoformat(),
            ktype=KLType.K_1M,
            autype=autype,
            max_count=page_size,
            page_req_key=page_req_key,
        )
        if ret != RET_OK:
            print(f"history kline failed: {code} {data}")
            break
        if data is not None and not data.empty:
            frames.append(data)
        if page_req_key is None:
            break

    if not frames:
        return None

    import pandas as pd

    return pd.concat(frames, ignore_index=True)


def select_volume_spike(
    data,
    threshold: float = 3.0,
    trade_days: int = 20,
    target_date: date | None = None,
) -> VolumeSpikeResult | None:
    if data is None or data.empty:
        return None
    if "time_key" not in data or "volume" not in data or "code" not in data:
        return None

    df = data.copy()
    df["volume"] = df["volume"].astype(int)
    df["time_key"] = df["time_key"].astype(str)
    df["trade_date"] = df["time_key"].str.slice(0, 10)
    df = df[df["volume"] > 0]
    if df.empty:
        return None

    trade_dates = sorted(df["trade_date"].dropna().unique())
    target_date_text = target_date.isoformat() if target_date is not None else trade_dates[-1]
    if target_date_text not in trade_dates:
        return None

    baseline_dates = [trade_date for trade_date in trade_dates if trade_date < target_date_text]
    baseline_dates = baseline_dates[-(trade_days - 1) :]
    if len(baseline_dates) < trade_days - 1:
        return None

    baseline = df[df["trade_date"].isin(baseline_dates)]
    target = df[df["trade_date"] == target_date_text]
    if baseline.empty or target.empty:
        return None

    baseline_row = baseline.sort_values(["volume", "time_key"], ascending=[False, True]).iloc[0]
    target_row = target.sort_values(["volume", "time_key"], ascending=[False, True]).iloc[0]
    baseline_volume = int(baseline_row["volume"])
    target_volume = int(target_row["volume"])
    if baseline_volume <= 0:
        return None

    ratio = target_volume / baseline_volume
    if ratio < threshold:
        return None

    return VolumeSpikeResult(
        code=str(target_row["code"]),
        name=_pick_name(target_row),
        target_max_time=datetime.strptime(str(target_row["time_key"]), "%Y-%m-%d %H:%M:%S"),
        target_max_volume=target_volume,
        baseline_max_time=datetime.strptime(str(baseline_row["time_key"]), "%Y-%m-%d %H:%M:%S"),
        baseline_max_volume=baseline_volume,
        ratio=ratio,
    )


def print_volume_spike_results(results: list[VolumeSpikeResult]) -> None:
    rows = volume_spike_result_rows(results)
    _print_table(
        [
            "code",
            "name",
            "target_max_time",
            "target_max_volume",
            "baseline_max_time",
            "baseline_max_volume",
            "ratio",
        ],
        rows,
    )


def save_volume_spike_results_csv(results: list[VolumeSpikeResult], path: Path) -> None:
    import csv

    headers = [
        "code",
        "name",
        "target_max_time",
        "target_max_volume",
        "baseline_max_time",
        "baseline_max_volume",
        "ratio",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows(volume_spike_result_rows(results))


def volume_spike_result_rows(results: list[VolumeSpikeResult]) -> list[list[str]]:
    return [
        [
            result.code,
            result.name,
            f"{result.target_max_time:%Y-%m-%d %H:%M:%S}",
            str(result.target_max_volume),
            f"{result.baseline_max_time:%Y-%m-%d %H:%M:%S}",
            str(result.baseline_max_volume),
            f"{result.ratio:.2f}",
        ]
        for result in sorted(results, key=lambda item: item.ratio, reverse=True)
    ]


def _pick_name(row) -> str:
    for field in ("name", "code_name", "stock_name"):
        value = row.get(field)
        if value is not None:
            return str(value)
    return ""


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
    print(f"+{separator}+")


def _format_table_row(row: list[str], widths: list[int]) -> str:
    cells = []
    for value, width in zip(row, widths):
        cells.append(f" {value}{' ' * (width - _display_width(value))} ")
    return f"|{'|'.join(cells)}|"


def _display_width(value: str) -> int:
    return sum(2 if ord(char) > 127 else 1 for char in value)
