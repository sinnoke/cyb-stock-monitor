from __future__ import annotations

from .config import UniverseConfig


def build_candidate_codes(config: UniverseConfig, market_prefix: str = "SZ") -> list[str]:
    codes: list[str] = []
    for prefix in config.prefixes:
        for n in range(config.start, config.end + 1):
            codes.append(f"{market_prefix}.{prefix}{n:03d}")

    codes.extend(normalize_code(code, market_prefix) for code in config.include_codes)

    excluded = {normalize_code(code, market_prefix) for code in config.exclude_codes}
    return sorted(code for code in set(codes) if code not in excluded)


def normalize_code(code: str, market_prefix: str = "SZ") -> str:
    code = code.strip().upper()
    if "." in code:
        return code
    return f"{market_prefix}.{code}"

