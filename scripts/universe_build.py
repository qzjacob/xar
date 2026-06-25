"""Universe expansion tooling for XAR — turn LLM-enumerated theme candidates into
a verified, registry-ready company universe.

Pipeline (each step idempotent; artifacts under .universe_cache/):
  cache     -> fetch authoritative listed-symbol sets per exchange (Finnhub)
  verify    -> gate LLM candidates: ticker must exist in its exchange set; US must
               be >= $2B USD market cap (JP/KR/TW: existence only, per the goal)
  generate  -> emit src/xar/ingestion/universe.py (appended to registry.COMPANIES)

Run: python3 scripts/universe_build.py <cache|verify|generate>
Authoritative existence gate (Finnhub symbol lists) kills hallucinated tickers;
the LLM only supplies *which* real names belong to *which* theme/segment.
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.request
from pathlib import Path

CACHE = Path(__file__).resolve().parent.parent / ".universe_cache"
CACHE.mkdir(exist_ok=True)
SYMBOLS_JSON = CACHE / "symbols.json"
CANDIDATES_JSON = CACHE / "candidates.json"   # written by the enumeration workflow
VERIFIED_JSON = CACHE / "verified.json"

# region -> the Finnhub exchange code(s) whose symbol sets define "listed there"
REGION_EXCHANGES = {
    "US": ["US"],
    "JP": ["T"],            # Tokyo
    "KR": ["KS", "KQ"],     # KOSPI + KOSDAQ
    "TW": ["TW", "TWO"],    # Taiwan main board + Taipei Exchange (OTC)
}
ALL_EXCHANGES = ["US", "T", "KS", "KQ", "TW", "TWO"]
US_MIN_MCAP_USD = 2_000_000_000  # $2B floor — US only
# Consumer cycle themes are organized by the *US* consumer/economic cycle; a
# US-listed name driven by a non-US cycle (e.g. Chinese/SE-Asian ADRs: BABA/PDD/
# SE/MELI…) must NOT be included (standing rule). Gate these themes on country==US.
CONSUMER_THEMES = {"internet", "retail", "restaurants"}
# Known US-listed-but-non-US-cycle consumer ADRs (China/SE-Asia/LatAm). Some are
# Delaware/Cayman-incorporated so the country gate alone misses them — block explicitly.
CONSUMER_NON_US_TICKERS = {
    "PDD", "BABA", "JD", "YUMC", "SE", "CPNG", "MELI", "NU", "BIDU", "TCOM", "BEKE", "TME",
    "VIPS", "IQ", "BILI", "GRAB", "TCEHY", "DADA", "WB", "MOMO", "YY", "DOYU", "HUYA",
    "TAL", "EDU", "GOTU", "ZTO", "YMM", "KC", "DAO", "TUYA", "DDL", "GDS", "NTES", "WIT",
}

# --- corrections from the independent agent audit (CODE_REVIEW Appendix C) ------
# Off-theme false positives the reviewers flagged AND I independently confirmed are
# not material to the theme. (I declined several reviewer flags I judged WRONG, e.g.
# ai_chip Nippon Pillar/NGK Insulators/Linde/Air Products = genuine fab suppliers;
# NGK Insulators ≠ NGK Spark Plug — the reviewer conflated them.)
THEME_DROP: dict[str, set[str]] = {
    "ai_chip": {"5332.T", "6498.T"},                       # TOTO (sanitaryware), Kitz (industrial valves)
    "humanoid_robotics": {"DD", "APH", "TEL", "JBL", "CLS", "6817.T", "1717.TW",
                          "3211.TWO", "6121.TWO", "3008.TW", "7740.T", "3019.TW"},  # broad/off-theme suppliers
    "internet": {"CMCSA", "FOXA"},                          # traditional cable/broadcast, not internet platforms
    "retail": {"PFGC", "SYY", "USFD", "VVV"},               # B2B foodservice distributors + oil-change services
    "restaurants": {"CASY"},                                # convenience/fuel retailer, not a restaurant
    "space_exploration": {"003490.KS"},                     # Korean Air — passenger airline
}
# Segment fixes (theme, resolved_symbol) -> correct segment id.
RETAG: dict[tuple, str] = {
    ("humanoid_robotics", "ON"): "hum_power",               # onsemi: power/sensors, not motors
}


def _tok() -> str:
    import os
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    from xar.config import get_settings
    tok = get_settings().finnhub_api_key
    return tok or os.getenv("FINNHUB_API_KEY", "")


def _get(url: str, timeout: int = 30):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.load(r)


# --- step: cache authoritative symbol sets ---------------------------------
def cache_symbols() -> None:
    tok = _tok()
    out: dict[str, dict[str, str]] = {}
    for exch in ALL_EXCHANGES:
        for attempt in range(3):
            try:
                data = _get(f"https://finnhub.io/api/v1/stock/symbol?exchange={exch}&token={tok}")
                out[exch] = {d["symbol"]: (d.get("description") or "") for d in data if d.get("symbol")}
                print(f"  {exch}: {len(out[exch])} symbols")
                break
            except Exception as e:
                print(f"  {exch} attempt {attempt+1} failed: {type(e).__name__} {str(e)[:60]}")
                time.sleep(2)
        time.sleep(1.2)  # be polite to free-tier
    SYMBOLS_JSON.write_text(json.dumps(out, ensure_ascii=False))
    total = sum(len(v) for v in out.values())
    print(f"cached {total} symbols across {len(out)} exchanges -> {SYMBOLS_JSON}")


# --- shared helpers --------------------------------------------------------
def _load_symbols() -> dict[str, dict[str, str]]:
    return json.loads(SYMBOLS_JSON.read_text())


def resolve_symbol(code: str, region: str, symbols: dict) -> str | None:
    """Map an LLM (code, region) to an authoritative Finnhub symbol, or None if it
    does not exist in the right exchange set (kills hallucinated tickers)."""
    code = (code or "").strip().upper()
    if not code:
        return None
    if region == "US":
        for cand in (code, code.replace(".", "-"), code.replace(".", "")):
            if cand in symbols.get("US", {}):
                return cand
        return None
    base = code.split(".")[0]  # strip any suffix the LLM added
    for exch in REGION_EXCHANGES.get(region, []):
        sym = f"{base}.{exch}"
        if sym in symbols.get(exch, {}):
            return sym
    return None


_SUFFIXES = ("incorporated", "inc", "corporation", "corp", "company", "co", "ltd", "limited",
             "plc", "holdings", "holding", "group", "technologies", "technology", "the",
             "sa", "ag", "nv", "kk", "llc", "lp", "se", "spa", "ab", "oyj", "asa")


_NAME_STOP = {"corp", "corporation", "inc", "co", "ltd", "limited", "company", "holdings",
              "holding", "group", "plc", "kk", "llc", "sa", "ag", "nv", "the", "and", "of",
              "intl", "international", "new", "de"}


def _name_parts(s: str):
    """(token list, concat string, meaningful-token set) — keeps parenthetical
    content (the real name often lives there, e.g. 'Chatwork (kubell)')."""
    s = re.sub(r"[?…/—]", " ", s.lower())
    s = s.replace("...", " ")
    toks = [t for t in re.findall(r"[a-z0-9]+", s) if t not in _NAME_STOP]
    cat = "".join(toks)
    mean = {t for t in toks if len(t) >= 3}
    return toks, cat, mean


def _lev(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a or not b:
        return max(len(a), len(b))
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[-1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def same_entity(llm_name: str, desc: str) -> bool:
    """True if the LLM-supplied name and the authoritative Finnhub `description`
    plausibly denote the SAME company — via shared meaningful token, substring,
    close edit-distance (romanization/spacing) or acronym. False ⇒ the LLM attached
    the WRONG ticker (different company) ⇒ its segment classification is untrustworthy
    ⇒ drop. (CODE_REVIEW D.1)"""
    tn, cn, mn = _name_parts(llm_name)
    td, cd, md = _name_parts(desc)
    if not cd:
        return True  # no authoritative name to check against — keep
    if mn & md:
        return True
    if cn and (cn in cd or cd in cn) and min(len(cn), len(cd)) >= 4:
        return True
    if cn and cd and _lev(cn, cd) / max(len(cn), len(cd)) <= 0.2:
        return True
    if td and cn == "".join(t[0] for t in td):          # LLM acronym of the real name
        return True
    if tn and cd == "".join(t[0] for t in tn):          # real name is acronym of LLM name
        return True
    return False


_GARBLE = re.compile(r"[?…—]|\.\.\.|\bn/a\b| / .+ / | -- ")
# Dedup key strips ONLY legal-entity suffixes (so "Sercomm" == "Sercomm Corporation"
# but "PSK Holdings" != "PSK Inc" — distinct real companies).
_LEGAL = {"inc", "corp", "corporation", "ltd", "limited", "co", "llc", "plc", "kk", "ag", "sa", "nv"}


def dedup_key(name: str) -> str:
    toks = [t for t in re.findall(r"[a-z0-9]+|[一-鿿]+", (name or "").lower()) if t not in _LEGAL]
    return "".join(toks)


def clean_name(desc: str) -> str:
    """Authoritative display name from a Finnhub ALL-CAPS description."""
    d = re.sub(r"\s+", " ", (desc or "").strip())
    return d.title() if d.isupper() or d.islower() else d


def norm_name(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"[（(].*?[)）]", " ", s)            # drop parentheticals
    s = re.sub(r"[^a-z0-9぀-ヿ一-鿿]+", " ", s)  # keep alnum + JP kana + CJK
    toks = [t for t in s.split() if t and t not in _SUFFIXES]
    return "".join(toks)


def existing_index():
    """Tickers + normalized names/aliases already in the CURATED registry — excludes
    the generated universe (`u_*` ids) so re-runs dedupe against the curated core,
    not against the universe being rebuilt (which would drop everything)."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    from xar.ingestion.registry import COMPANIES
    tickers, names = set(), set()
    for c in COMPANIES:
        if str(c.get("id", "")).startswith("u_"):
            continue
        for t in c.get("tickers", []):
            tickers.add(t.upper())
            tickers.add(t.split(".")[0].upper())
        for nm in [c.get("name", "")] + list(c.get("aliases", [])):
            n = norm_name(nm)
            if len(n) >= 4:
                names.add(n)
    return tickers, names


def us_profile(symbol: str, cache: dict) -> dict:
    """USD market cap + country for a US symbol via Finnhub profile2 (cached),
    yfinance market-cap fallback. Returns {mcap: float|None, country: str|None}."""
    if symbol in cache:
        return cache[symbol]
    mc, country = None, None
    try:
        js = _get(f"https://finnhub.io/api/v1/stock/profile2?symbol={symbol}&token={_tok()}", timeout=20)
        country = js.get("country")
        if js.get("marketCapitalization"):
            mc = float(js["marketCapitalization"]) * 1_000_000  # Finnhub reports in $M
    except Exception:
        pass
    if mc is None:
        try:
            import yfinance as yf
            info = yf.Ticker(symbol).info or {}
            if info.get("marketCap"):
                mc = float(info["marketCap"])
            country = country or info.get("country")
        except Exception:
            pass
    cache[symbol] = {"mcap": mc, "country": country}
    time.sleep(1.1)  # Finnhub free-tier politeness (~55/min)
    return cache[symbol]


# --- step: verify candidates -> verified universe --------------------------
def verify() -> None:
    symbols = _load_symbols()
    descmap = {s: d for ex in symbols.values() for s, d in ex.items()}  # authoritative ticker->name
    cands = json.loads(CANDIDATES_JSON.read_text())
    if isinstance(cands, dict):
        cands = cands.get("candidates", [])
    ex_tickers, ex_names = existing_index()
    prof_cache_path = CACHE / "profile.json"
    prof_cache = json.loads(prof_cache_path.read_text()) if prof_cache_path.exists() else {}

    merged: dict[str, dict] = {}   # resolved symbol -> company record
    stats = {"raw": len(cands), "no_exist": 0, "dup_registry": 0, "us_below_2b": 0,
             "us_non_us_cycle": 0, "audit_dropped": 0, "name_mismatch": 0, "dup_name": 0,
             "us_mcap_unverified": 0, "kept": 0}
    us_to_check = []

    for c in cands:
        region = c.get("region", "US")
        sym = resolve_symbol(c.get("code", ""), region, symbols)
        if not sym:
            stats["no_exist"] += 1
            continue
        if sym.upper() in ex_tickers or sym.split(".")[0].upper() in ex_tickers:
            stats["dup_registry"] += 1
            continue
        nn = norm_name(c.get("name", ""))
        if len(nn) >= 4 and nn in ex_names:
            stats["dup_registry"] += 1
            continue
        seg, theme = c.get("segment"), c.get("theme")
        if theme in CONSUMER_THEMES and region == "US" and sym.split(".")[0].upper() in CONSUMER_NON_US_TICKERS:
            stats["us_non_us_cycle"] += 1
            continue
        if sym in THEME_DROP.get(theme, set()) or sym.upper() in THEME_DROP.get(theme, set()):
            stats["audit_dropped"] += 1
            continue
        # name↔ticker truth gate (D.1): the LLM name must denote the ticker's real
        # company; otherwise the LLM confused the ticker and its segment is untrusted.
        desc = descmap.get(sym, "")
        llm_name = c.get("name", "")
        if desc and not same_entity(llm_name, desc):
            stats["name_mismatch"] += 1
            continue
        seg = RETAG.get((theme, sym), RETAG.get((theme, sym.upper()), seg))
        # authoritative display name; keep the (clean) LLM name only as an alias
        auth_name = clean_name(desc) if desc else llm_name
        clean_aliases = [a for a in ([llm_name] + (c.get("aliases") or []))
                         if a and not _GARBLE.search(a) and a != auth_name]
        if sym in merged:
            m = merged[sym]
            if theme and theme not in m["themes"]:
                m["themes"].append(theme)
            if theme and seg:
                m["seg"].setdefault(theme, seg)
            for a in clean_aliases:
                if a not in m["aliases"]:
                    m["aliases"].append(a)
        else:
            merged[sym] = {
                "id": f"u_{region.lower()}_{sym.split('.')[0].lower().replace('-', '_').replace('.', '_')}",
                "name": auth_name, "tickers": [sym], "region": region,
                "themes": [theme] if theme else [], "seg": ({theme: seg} if theme and seg else {}),
                "aliases": list(dict.fromkeys(clean_aliases)),
                "note": c.get("note", ""),
            }
            if region == "US":
                us_to_check.append(sym)

    # US gates (batched, cached): $2B floor for ALL US; country==US for consumer themes.
    print(f"  profiling {len(us_to_check)} US names (≈{round(len(us_to_check)*1.1/60,1)} min)…")
    for i, sym in enumerate(us_to_check):
        prof = us_profile(sym, prof_cache)
        mc, country = prof.get("mcap"), prof.get("country")
        rec = merged[sym]
        # non-US-cycle gate: strip consumer themes from a non-US-domiciled name
        if country and country != "US":
            keep_themes = [t for t in rec["themes"] if t not in CONSUMER_THEMES]
            if keep_themes != rec["themes"]:
                stats["us_non_us_cycle"] += 1
                rec["themes"] = keep_themes
                rec["seg"] = {t: s for t, s in rec["seg"].items() if t in keep_themes}
                if not rec["themes"]:
                    rec["_drop"] = True
                    continue
        if mc is None:                      # D.3: hard gate — unverifiable mcap fails the floor
            rec["_drop"] = True
            stats["us_mcap_unverified"] += 1
        elif mc < US_MIN_MCAP_USD:
            rec["_drop"] = True
            stats["us_below_2b"] += 1
        else:
            rec["marketCapUsd"] = round(mc)
        if (i + 1) % 25 == 0:
            prof_cache_path.write_text(json.dumps(prof_cache))
            print(f"    …{i+1}/{len(us_to_check)}")
    prof_cache_path.write_text(json.dumps(prof_cache))

    # D.2: intra-universe entity dedup by authoritative name (keep highest-mcap / first).
    out, seen_name = [], {}
    for r in sorted(merged.values(), key=lambda x: -(x.get("marketCapUsd") or 0)):
        if r.get("_drop"):
            continue
        key = dedup_key(r["name"])
        if len(key) >= 4 and key in seen_name:
            prev = seen_name[key]
            for t in r["themes"]:               # fold themes into the kept twin
                if t not in prev["themes"]:
                    prev["themes"].append(t)
                    prev["seg"].update({k: v for k, v in r["seg"].items() if k == t})
            stats["dup_name"] += 1
            continue
        seen_name[key] = r
        out.append(r)
    stats["kept"] = len(out)
    VERIFIED_JSON.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print("verify stats:", json.dumps(stats))
    by_theme = {}
    for r in out:
        for t in r["themes"]:
            by_theme[t] = by_theme.get(t, 0) + 1
    print("kept by theme:", json.dumps(by_theme, ensure_ascii=False))
    by_region = {}
    for r in out:
        by_region[r["region"]] = by_region.get(r["region"], 0) + 1
    print("kept by region:", json.dumps(by_region))


# --- step: generate the registry universe module ---------------------------
def generate() -> None:
    recs = json.loads(VERIFIED_JSON.read_text())
    recs.sort(key=lambda r: (r["region"], r["themes"][0] if r["themes"] else "", r["id"]))
    lines = [
        '"""GENERATED — do not hand-edit. Bulk theme universe (US >=$2B + JP/KR/TW)',
        "built by scripts/universe_build.py from LLM enumeration gated against the",
        "authoritative Finnhub exchange symbol sets. Appended to registry.COMPANIES.",
        '"""',
        "from __future__ import annotations",
        "",
        "UNIVERSE: list[dict] = [",
    ]
    for r in recs:
        themes = r["themes"] or ["ai_chip"]
        seg = r["seg"] or {themes[0]: ""}
        primary_seg = seg.get(themes[0], "") or next(iter(seg.values()), "")
        aliases = [a for a in dict.fromkeys(r["aliases"]) if a]
        rec = {
            "id": r["id"], "name": r["name"], "tickers": r["tickers"], "aliases": aliases,
            "region": r["region"], "chain_role": primary_seg, "cn_code": None,
            "themes": themes, "seg": seg,
        }
        lines.append(f"    {rec!r},")
    lines.append("]")
    out_path = Path(__file__).resolve().parent.parent / "src" / "xar" / "ingestion" / "universe.py"
    out_path.write_text("\n".join(lines) + "\n")
    print(f"wrote {len(recs)} companies -> {out_path}")


# --- step: pull market data for the generated universe (background-friendly) ---
def pull() -> None:
    """Pull prices (keyless yahoo) + fundamentals (finnhub) for the generated
    universe ids. Idempotent; resumable. Prices are prioritized (dashboard momentum)."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    from xar.providers import yahoo
    from xar.providers import pull_company
    recs = json.loads(VERIFIED_JSON.read_text())
    ids = [r["id"] for r in recs]
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else None
    if limit:
        ids = ids[:limit]
    px_ok = fund_ok = 0
    for i, cid in enumerate(ids):
        try:
            if yahoo.pull_prices(cid):   # keyless; the important one for momentum/sparklines
                px_ok += 1
        except Exception as e:
            print(f"  px fail {cid}: {str(e)[:50]}")
        try:
            pull_company(cid, with_social=False)  # finnhub fundamentals/estimates + signals
            fund_ok += 1
        except Exception as e:
            print(f"  fund fail {cid}: {str(e)[:50]}")
        if (i + 1) % 25 == 0:
            print(f"  …{i+1}/{len(ids)} (prices {px_ok}, fundamentals {fund_ok})", flush=True)
    print(f"pull done: prices {px_ok}/{len(ids)}, fundamentals {fund_ok}/{len(ids)}")


# --- step: deterministic integrity audit (factual backbone for the agent review) ---
def audit() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    from xar.ingestion.registry import COMPANIES, SEGMENTS, THEMES
    from xar.ontology import cycle
    symbols = _load_symbols()
    prof_cache_path = CACHE / "profile.json"
    prof = json.loads(prof_cache_path.read_text()) if prof_cache_path.exists() else {}
    uni = [c for c in COMPANIES if str(c["id"]).startswith("u_")]
    issues = {"dup_ids": [], "dup_tickers": [], "dup_names": [], "ticker_not_in_exchange": [],
              "name_mismatch": [], "bad_seg": [], "bad_theme": [], "us_below_2b": [],
              "consumer_non_us": [], "consumer_no_cycle": []}

    ids = [c["id"] for c in COMPANIES]
    ticks = [t for c in COMPANIES for t in c["tickers"]]
    issues["dup_ids"] = sorted({i for i in ids if ids.count(i) > 1})
    issues["dup_tickers"] = sorted({t for t in ticks if ticks.count(t) > 1})
    # D.2: duplicate company NAMES across the whole registry (legal-suffix-insensitive)
    from collections import Counter
    nm_counts = Counter(dedup_key(c["name"]) for c in COMPANIES if dedup_key(c["name"]))
    dup_nm = {n for n, k in nm_counts.items() if k > 1}
    issues["dup_names"] = sorted({c["name"] for c in COMPANIES if dedup_key(c["name"]) in dup_nm})[:20]
    # D.1: any universe name that disagrees with its authoritative exchange description
    desc = {s: d for ex in symbols.values() for s, d in ex.items()}
    for c in uni:
        d = desc.get(c["tickers"][0], "")
        if d and not same_entity(c["name"], d):
            issues["name_mismatch"].append((c["tickers"][0], c["name"][:24], d))

    exch_all = {s for ex in symbols.values() for s in ex}
    for c in uni:
        for t in c["tickers"]:
            if t not in exch_all:
                issues["ticker_not_in_exchange"].append((c["id"], t))
        for th in c["themes"]:
            if th not in THEMES:
                issues["bad_theme"].append((c["id"], th))
        for th, sg in (c.get("seg") or {}).items():
            if sg not in SEGMENTS:
                issues["bad_seg"].append((c["id"], sg))
        if c["region"] == "US":
            p = prof.get(c["tickers"][0], {})
            mc, country = p.get("mcap"), p.get("country")
            if mc is not None and mc < US_MIN_MCAP_USD:
                issues["us_below_2b"].append((c["id"], round(mc / 1e9, 2)))
            if any(th in CONSUMER_THEMES for th in c["themes"]):
                if country and country != "US":
                    issues["consumer_non_us"].append((c["id"], country))
                if not cycle.cycle_of_company(c):
                    issues["consumer_no_cycle"].append(c["id"])

    by_tr = Counter()
    for c in uni:
        for th in c["themes"]:
            by_tr[f"{th}:{c['region']}"] += 1
    print(f"AUDIT: {len(uni)} generated universe companies; {len(COMPANIES)} total")
    print("coverage (universe only) theme:region ->", json.dumps(dict(sorted(by_tr.items())), ensure_ascii=False))
    clean = True
    for k, v in issues.items():
        if v:
            clean = False
            print(f"  ISSUE {k}: {len(v)} -> {v[:12]}")
    print("INTEGRITY:", "CLEAN ✓" if clean else "ISSUES FOUND ✗")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "cache"
    {"cache": cache_symbols, "verify": verify, "generate": generate, "pull": pull,
     "audit": audit}.get(cmd, lambda: print(f"unknown command: {cmd}"))()
