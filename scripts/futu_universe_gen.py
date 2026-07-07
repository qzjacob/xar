"""生成 ingestion/futu_universe.py —— 从富途主题相关板块扩容 HK/A股 universe。

确定性(零 LLM):板块名经 cn_routing 映射到本体 8 主题 → get_plate_stock 取成员 →
按市值排序、每主题取 top-N、剔除已在册 → 写候选清单(与 universe.py 同形)到 docs/futu/ 供人工复核 —— **不自动并入
COMPANIES**:概念板块是大杂烩(如"人工智能"含中国移动/顺丰),直接注入会污染本体。
复核后可手动 promote 干净子集。

用法:XAR_ENABLE_FUTU=true python3 scripts/futu_universe_gen.py [--per-theme 20] [--min-cap-bn 5]
"""
from __future__ import annotations

import argparse
import sys

from xar.ingestion.registry import COMPANIES
from xar.ontology import cn_routing
from xar.providers import futu


def _existing_futu_codes() -> set[str]:
    out = set()
    for c in COMPANIES:
        code = futu.code_from_tickers(c.get("tickers", []))
        if code:
            out.add(code)
    return out


def _registry_ticker(code: str) -> str | None:
    """Futu code → registry ticker(round-trips via code_from_tickers)。"""
    mkt, _, num = code.partition(".")
    if mkt == "HK":
        return f"{int(num):04d}.HK"
    if mkt == "SH":
        return f"{num}.SS"
    if mkt == "SZ":
        return f"{num}.SZ"
    return None


def _slug(code: str) -> str:
    return "ft_" + code.replace(".", "_").lower()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-theme", type=int, default=20)
    ap.add_argument("--min-cap-bn", type=float, default=5.0, help="min market cap (bn local ccy)")
    ap.add_argument("--out", default="docs/futu/futu_universe_candidates.py")
    args = ap.parse_args()

    if not futu.available():
        print("Futu OpenD unavailable — set XAR_ENABLE_FUTU=true and run OpenD.")
        return 1
    from futu import Market, Plate, RET_OK

    ctx = futu._quote_ctx()
    existing = _existing_futu_codes()
    print(f"existing futu-addressable companies: {len(existing)}")

    # 1) theme → relevant plates (concept+industry across HK/SH/SZ)
    theme_plates: dict[str, list[str]] = {}
    for mkt in (Market.HK, Market.SH, Market.SZ):
        for pl in (Plate.CONCEPT, Plate.INDUSTRY):
            r, df = ctx.get_plate_list(mkt, pl)
            if r != RET_OK or df is None:
                continue
            for _, row in df.iterrows():
                name = str(row.get("plate_name") or "")
                pcode = str(row.get("code") or "")
                for th in cn_routing.theme_hits(name):
                    theme_plates.setdefault(th, []).append(pcode)
    print("theme→plate coverage:", {k: len(v) for k, v in theme_plates.items()})

    # 2) plate → stocks; collect candidate code → themes
    cand_themes: dict[str, set[str]] = {}
    for th, plates in theme_plates.items():
        for pcode in set(plates):
            r, df = ctx.get_plate_stock(pcode)
            if r != RET_OK or df is None:
                continue
            for _, row in df.iterrows():
                code = str(row.get("code") or "")
                if not code or code in existing:
                    continue
                if not code.startswith(("HK.", "SH.", "SZ.")):  # HK/A only
                    continue
                cand_themes.setdefault(code, set()).add(th)
    print(f"raw candidates (excl. existing): {len(cand_themes)}")

    # 3) snapshot for market cap + name (batched)
    codes = list(cand_themes)
    snap: dict[str, dict] = {}
    for i in range(0, len(codes), 200):
        batch = codes[i:i + 200]
        r, df = ctx.get_market_snapshot(batch)
        if r != RET_OK or df is None:
            continue
        for _, row in df.iterrows():
            snap[str(row.get("code"))] = {
                "name": str(row.get("name") or "").strip(),
                "cap": futu._num(row.get("total_market_val")) or 0.0,
                "sec_type": str(row.get("sec_status") or "")}

    # 4) rank per theme by cap, take top-N above min cap
    min_cap = args.min_cap_bn * 1e9
    picked: dict[str, dict] = {}
    for th in theme_plates:
        pool = [(c, snap.get(c, {})) for c, ths in cand_themes.items() if th in ths]
        pool = [(c, s) for c, s in pool if s.get("cap", 0) >= min_cap and s.get("name")]
        pool.sort(key=lambda x: x[1]["cap"], reverse=True)
        for code, s in pool[: args.per_theme]:
            e = picked.setdefault(code, {"code": code, "name": s["name"],
                                         "cap": s["cap"], "themes": set()})
            e["themes"].add(th)
    print(f"picked (top {args.per_theme}/theme, ≥{args.min_cap_bn}bn): {len(picked)}")

    # 5) emit module
    entries = []
    for code, e in sorted(picked.items(), key=lambda x: -x[1]["cap"]):
        tk = _registry_ticker(code)
        if not tk:
            continue
        region = "HK" if code.startswith("HK.") else "CN"
        entries.append({
            "id": _slug(code), "name": e["name"], "tickers": [tk],
            "aliases": [e["name"]], "region": region, "chain_role": None,
            "cn_code": tk if region == "CN" else None,
            "themes": sorted(e["themes"]), "seg": {},
            "meta": {"source": "futu_plate", "futu_code": code,
                     "market_cap": round(e["cap"])},
        })
    _write_module(args.out, entries)
    print(f"wrote {len(entries)} companies → {args.out}")
    futu.close()
    return 0


def _write_module(path: str, entries: list[dict]) -> None:
    # repr() → valid Python literals (None/True/False) with Chinese preserved (py3).
    body = "\n".join(f"    {e!r}," for e in entries)
    src = ('"""Generated by scripts/futu_universe_gen.py — HK/A-share names from Futu\n'
           '主题相关板块(市值排序、每主题 top-N)。加性合并进 COMPANIES;可删可再生。\n'
           'DO NOT EDIT BY HAND."""\n'
           "from __future__ import annotations\n\n"
           "FUTU_UNIVERSE: list[dict] = [\n" + body + "\n]\n")
    with open(path, "w", encoding="utf-8") as f:
        f.write(src)


if __name__ == "__main__":
    sys.exit(main())
