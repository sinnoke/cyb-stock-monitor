from __future__ import annotations

from datetime import datetime
import random
from typing import Callable

from .config import FutuConfig, RuleConfig
from .models import MinuteBar


BarCallback = Callable[[MinuteBar], None]
BaselineCallback = Callable[[dict[str, int]], None]


class FutuMinuteKlineProvider:
    def __init__(
        self,
        config: FutuConfig,
        rules: RuleConfig,
        on_bar: BarCallback,
        on_baselines: BaselineCallback | None = None,
    ) -> None:
        self.config = config
        self.rules = rules
        self.on_bar = on_bar
        self.on_baselines = on_baselines
        self.quote_ctx = None

    def run(
        self,
        candidate_codes: list[str],
        prefixes: list[str] | None = None,
        prefer_futu_universe: bool = True,
        selection: str = "random",
        random_seed: int = 20260509,
    ) -> None:
        from futu import RET_OK, SubType
        from futu import OpenQuoteContext

        self.quote_ctx = OpenQuoteContext(host=self.config.host, port=self.config.port)
        self.quote_ctx.set_handler(self._build_handler())

        codes = candidate_codes
        if prefer_futu_universe:
            codes = self._load_cyb_codes(prefixes or ["300", "301"]) or candidate_codes
        if len(codes) > self.config.max_subscribe:
            print(
                f"code count {len(codes)} exceeds max_subscribe {self.config.max_subscribe}; "
                f"selection={selection}"
            )
            codes = _select_codes(codes, self.config.max_subscribe, selection, random_seed)
        print(f"prepared {len(codes)} codes for subscription")

        if self.on_baselines is not None:
            baselines = self._load_volume_baselines(codes)
            self.on_baselines(baselines)

        subtype = _resolve_futu_constant(SubType, self.config.kline_type)
        for batch in _chunks(codes, self.config.subscribe_batch_size):
            ret, data = self.quote_ctx.subscribe(batch, [subtype], subscribe_push=True)
            if ret != RET_OK:
                print(f"subscribe failed: {data}; batch size={len(batch)}")
                continue
            print(f"subscribed {len(batch)} codes")

        print("futu provider is running; press Ctrl+C to stop")
        try:
            import time

            while True:
                time.sleep(1)
        finally:
            self.close()

    def close(self) -> None:
        if self.quote_ctx is not None:
            self.quote_ctx.close()
            self.quote_ctx = None

    def _load_cyb_codes(self, prefixes: list[str]) -> list[str]:
        from futu import Market, RET_OK, SecurityType

        if self.quote_ctx is None:
            return []

        market = _resolve_futu_constant(Market, self.config.market_prefix)
        ret, data = self.quote_ctx.get_stock_basicinfo(market, SecurityType.STOCK)
        if ret != RET_OK:
            print(f"load futu stock basic info failed: {data}")
            return []

        codes = []
        for code in data["code"].dropna().astype(str):
            raw_code = code.split(".", 1)[-1]
            if code.startswith(f"{self.config.market_prefix}.") and any(
                raw_code.startswith(prefix) for prefix in prefixes
            ):
                codes.append(code)

        return sorted(set(codes))

    def _load_volume_baselines(self, codes: list[str]) -> dict[str, int]:
        from futu import AuType, KLType, RET_OK

        if self.quote_ctx is None:
            return {}

        ktype = _resolve_futu_constant(KLType, self.config.kline_type)
        autype = _resolve_futu_constant(AuType, self.config.autype)
        baselines: dict[str, int] = {}

        for index, code in enumerate(codes, start=1):
            ret, data, _ = self.quote_ctx.request_history_kline(
                code,
                ktype=ktype,
                autype=autype,
                max_count=2000,
            )
            if ret != RET_OK:
                print(f"history kline failed: {code} {data}")
                continue

            baseline = _max_volume_in_recent_days(data, self.rules.history_days)
            if baseline > 0:
                baselines[code] = baseline
            print(f"baseline {index}/{len(codes)} {code} max_volume={baseline}")

        return baselines

    def _build_handler(self):
        from futu import CurKlineHandlerBase, RET_OK

        provider = self

        class Handler(CurKlineHandlerBase):
            def on_recv_rsp(self, rsp_pb):
                ret, data = super().on_recv_rsp(rsp_pb)
                if ret != RET_OK:
                    print(f"kline push error: {data}")
                    return ret, data

                for _, row in data.iterrows():
                    provider.on_bar(_row_to_bar(row))
                return ret, data

        return Handler()


def _row_to_bar(row) -> MinuteBar:
    return MinuteBar(
        code=str(row.get("code", "")),
        name=str(row.get("name", "")),
        time=datetime.strptime(str(row["time_key"]), "%Y-%m-%d %H:%M:%S"),
        open=float(row["open"]),
        high=float(row["high"]),
        low=float(row["low"]),
        close=float(row["close"]),
        volume=int(row.get("volume", 0)),
        amount=float(row.get("turnover", 0.0)),
        last_close=float(row.get("last_close", 0.0)),
    )


def _chunks(items: list[str], size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _select_codes(codes: list[str], limit: int, selection: str, random_seed: int) -> list[str]:
    codes = sorted(set(codes))
    if selection == "random":
        rng = random.Random(random_seed)
        return sorted(rng.sample(codes, limit))
    if selection == "first":
        return codes[:limit]
    raise ValueError(f"unsupported universe selection: {selection}")


def _max_volume_in_recent_days(data, days: int) -> int:
    if data is None or data.empty or "time_key" not in data or "volume" not in data:
        return 0

    df = data.copy()
    df["trade_date"] = df["time_key"].astype(str).str.slice(0, 10)
    recent_dates = sorted(df["trade_date"].dropna().unique())[-days:]
    if not recent_dates:
        return 0

    recent = df[df["trade_date"].isin(recent_dates)]
    if recent.empty:
        return 0

    return int(recent["volume"].max())


def _resolve_futu_constant(container, name: str):
    try:
        return getattr(container, name)
    except AttributeError as exc:
        raise ValueError(f"unsupported futu constant: {name}") from exc
