"""Bulk ontology enrichment for the universe companies — bring them up to the depth of
the curated core, using the LLM task manager (task=search_bulk → GLM/Kimi subscription so
the ~569-company pass rides a flat plan, not an unbounded token bill).

Per company the LLM returns, STRICTLY whitelist-validated against the existing ontology
(no invented taxonomy):
  - additional theme memberships (theme + a segment within it) — the multi-theme gap,
  - tech-route exposure (from the 25 known routes) — the untagged gap,
  - extra aliases (native / romanized / short / brand) — the entity-resolution gap,
  - a better primary segment if the current one mis-fits,
  - a free-text `suggest_route` (a real tech specialization NOT in the vocab) → feeds the
    ontology-EXTENSION analysis (new tech-routes), applied separately as code-as-truth.

Subcommands:
  enrich [--limit N] [--sample N]   LLM pass → cache .universe_cache/enrich.json
  report                            tallies for the extension decision (new routes / overloaded segments)
  generate                          merge cache → rewrite src/xar/ingestion/universe.py (additive)
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from pydantic import BaseModel, Field

from xar.ingestion import registry as R
from xar.ingestion.universe import UNIVERSE
from xar.models import llm

CACHE = Path(".universe_cache/enrich.json")
UNIVERSE_PY = Path("src/xar/ingestion/universe.py")

# --- whitelist vocab -------------------------------------------------------
SEG_BY_THEME: dict[str, list[str]] = {}
for _sid, _sm in R.SEGMENTS.items():
    SEG_BY_THEME.setdefault(_sm.get("theme"), []).append(_sid)
THEMES = list(R.THEMES.keys())
ROUTES = {r["id"]: r["name"] for r in getattr(R, "TECH_ROUTES", [])}
ROUTE_THEMES = getattr(R, "ROUTE_THEMES", {})
ROUTE_BY_NAME = {v.lower(): k for k, v in ROUTES.items()}
ROUTE_BY_ID = {k.lower(): k for k in ROUTES}

# Map a recurring free-text `suggest_route` to one of the data-driven EXTENSION routes added
# to registry.TECH_ROUTES — so generate() can tag the company without a second LLM pass.
_SUGGEST_MAP = {
    "cyber": "tr_cybersec", "network security": "tr_cybersec", "ai security": "tr_cybersec",
    "pki": "tr_cybersec", "security software": "tr_cybersec",
    "display driver": "tr_ddic", "ddic": "tr_ddic",
    "power semi": "tr_power_semi", "power device": "tr_power_semi",
    "computer vision": "tr_cv", "vision ai": "tr_cv",
    "medical": "tr_med_imaging", "imaging": "tr_med_imaging",
    "pneumatic": "tr_pneumatic",
    "industrial gas": "tr_industrial_gas", "electronic gas": "tr_industrial_gas",
    "ceramic": "tr_ceramic_pkg",
}


# Deterministic corrections from the independent common-sense audit (workflow
# ontology-enrichment-audit): confirmed LLM mis-assignments — supplier-vs-route
# conflations, multi-theme over-reach, one alias collision. Applied in generate().
_CORRECTIONS: dict[str, dict] = {
    "u_us_lin":  {"drop_themes": ["space_exploration"], "drop_routes": ["tr_methalox"], "add_routes": ["tr_industrial_gas"]},  # Linde: gas vendor, not propulsion
    "u_us_apd":  {"drop_themes": ["space_exploration"]},                       # Air Products: gas vendor
    "u_tw_3105": {"drop_routes": ["tr_siph"]},                                 # Win Semi: GaAs/RF, not SiPh
    "u_tw_2455": {"drop_routes": ["tr_eml"]},                                  # VPEC: VCSEL epi, not EML
    "u_tw_2454": {"drop_themes": ["space_exploration", "humanoid_robotics"]},  # MediaTek: ai_chip only
    "u_tw_2327": {"drop_themes": ["space_exploration"]},                       # Yageo: passives, not space
    "u_tw_2332": {"drop_themes": ["retail"]},                                  # D-Link: networking hw, not a retailer
    "u_jp_3665": {"set_themes": ["internet"], "set_seg": {"internet": "net_ecommerce"}, "set_role": "net_ecommerce", "drop_routes": ["tr_cv"]},  # Enigmo/BUYMA marketplace
    "u_us_u":    {"set_seg": {"ai_software": "swe_devinfra"}, "set_role": "swe_devinfra"},  # Unity: dev/AI infra, not productivity
    "u_jp_4686": {"drop_routes": ["tr_rag"]},                                  # JustSystems: no RAG product
    "u_jp_7751": {"drop_routes": ["tr_euv"]},                                  # Canon: nanoimprint, not EUV
    "u_jp_6963": {"drop_routes": ["tr_eml"]},                                  # Rohm: power/analog IDM (laser≠EML)
    "u_us_ter":  {"drop_themes": ["humanoid_robotics"]},                       # Teradyne: ATE test, cobots≠humanoid OEM
    "u_jp_3774": {"drop_themes": ["internet"]},                                # IIJ: B2B ISP/infra, not consumer internet
    "u_jp_3991": {"drop_themes": ["internet"]},                               # Wantedly: recruiting, not net_social
    "u_tw_3545": {"set_seg": {"ai_chip": "chip_cpu"}, "set_role": "chip_cpu", "drop_aliases": ["原相科技股份有限公司"]},  # FocalTech (敦泰): DDIC/touch; 原相 is PixArt
    "u_jp_4188": {"drop_routes": ["tr_euv"]},                                  # Mitsubishi Chemical: materials, not EUV tool
    "u_jp_3132": {"drop_themes": ["humanoid_robotics"]},                       # Macnica: distributor / ai_software
}


def _correct(c: dict) -> dict:
    fix = _CORRECTIONS.get(c["id"])
    if not fix:
        return c
    if "set_themes" in fix:
        c["themes"] = list(fix["set_themes"])
        c["seg"] = {}
    for t in fix.get("drop_themes", []):
        c["themes"] = [x for x in c.get("themes", []) if x != t]
        c["seg"] = {k: v for k, v in (c.get("seg") or {}).items() if k != t}
    for t, s in (fix.get("set_seg") or {}).items():
        c.setdefault("seg", {})[t] = s
        if t not in c["themes"]:
            c["themes"].append(t)
    if fix.get("set_role"):
        c["chain_role"] = fix["set_role"]
    if "drop_routes" in fix or "add_routes" in fix:
        routes = [r for r in (c.get("tech_routes") or []) if r not in fix.get("drop_routes", [])]
        for r in fix.get("add_routes", []):
            if r not in routes:
                routes.append(r)
        c["tech_routes"] = routes
    for a in fix.get("drop_aliases", []):
        c["aliases"] = [x for x in (c.get("aliases") or []) if x != a]
    return c


class ThemeAssign(BaseModel):
    theme: str = ""
    segment: str = ""


class OntoEnrich(BaseModel):
    additional: list[ThemeAssign] = Field(default_factory=list)
    tech_routes: list[str] = Field(default_factory=list)
    extra_aliases: list[str] = Field(default_factory=list)
    better_segment: str = ""
    suggest_route: str = ""


def _prompt(c: dict) -> str:
    theme = (c.get("themes") or [""])[0]
    seg = (c.get("seg") or {}).get(theme, "")
    vocab = "\n".join(f"  {t}: {SEG_BY_THEME.get(t, [])}" for t in THEMES)
    routes = ", ".join(f"{rid}({name})" for rid, name in ROUTES.items())
    return (
        f"Company: {c['name']}  tickers={c.get('tickers')}  region={c.get('region')}\n"
        f"Currently classified: theme={theme}, segment={seg}.\n\n"
        "Enrich its ontology, using ONLY the controlled vocabulary below — never invent themes/segments.\n"
        f"THEMES → SEGMENTS:\n{vocab}\n"
        f"TECH ROUTES (id(name)): {routes}\n\n"
        "Return:\n"
        "- additional: other THEMES this company CLEARLY also belongs to (each with a valid segment "
        "in that theme). Only add a theme if the company has a real, material business in it. Usually 0-2.\n"
        "- tech_routes: route ids (from the list) this company is genuinely exposed to. Empty if none apply.\n"
        "- extra_aliases: additional real names — native-language name, romanization, common short name, "
        "or well-known brand. No duplicates of the existing name. 0-4.\n"
        "- better_segment: if the current segment mis-fits, a better segment id WITHIN the current theme; else ''.\n"
        "- suggest_route: if this company's core tech specialization is NOT covered by any route above, "
        "name it in <=4 words (e.g. 'GLP-1 manufacturing', 'reusable booster'); else ''.\n"
        "Be conservative: precision over recall. Only assert what is well-known about this specific company."
    )


def _valid(c: dict, e: OntoEnrich) -> dict:
    """Drop anything outside the whitelist; return a clean delta dict."""
    cur_themes = set(c.get("themes") or [])
    add = []
    for a in e.additional:
        t, s = (a.theme or "").strip(), (a.segment or "").strip()
        if t in THEMES and t not in cur_themes and s in SEG_BY_THEME.get(t, []):
            add.append({"theme": t, "segment": s})
    # the company's full theme set after this enrichment's additions, for the cross-domain
    # route gate below
    co_themes = cur_themes | {a["theme"] for a in add}
    routes = []
    for r in e.tech_routes:
        rid = ROUTE_BY_ID.get(str(r).lower()) or ROUTE_BY_NAME.get(str(r).lower())
        if not rid or rid in routes:
            continue
        # cross-domain invariant (registry.ROUTE_THEMES, code-as-truth): reject a route whose
        # home themes don't overlap the company's themes — a domain confusion (e.g. a chip
        # company tagged a space-propulsion route). Routes absent from the map are unconstrained.
        home = set(ROUTE_THEMES.get(rid, ()))
        if home and not (home & co_themes):
            continue
        routes.append(rid)
    name_l = c["name"].lower()
    have = {a.lower() for a in (c.get("aliases") or [])} | {name_l}
    aliases = []
    for a in e.extra_aliases:
        a = (a or "").strip()
        if a and a.lower() not in have and len(a) <= 60:
            aliases.append(a)
            have.add(a.lower())
    theme = (c.get("themes") or [""])[0]
    better = e.better_segment.strip()
    better = better if better in SEG_BY_THEME.get(theme, []) and better != (c.get("seg") or {}).get(theme) else ""
    return {"additional": add, "tech_routes": routes, "extra_aliases": aliases[:4],
            "better_segment": better, "suggest_route": (e.suggest_route or "").strip()[:40]}


def enrich(limit: int | None = None, sample: int | None = None) -> None:
    CACHE.parent.mkdir(exist_ok=True)
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    run_id = llm.new_batch_run_id("batch")  # batch budget cap applies if subscription unavailable
    targets = UNIVERSE[:sample] if sample else UNIVERSE
    done = 0
    for c in targets:
        if c["id"] in cache:
            continue
        if limit and done >= limit:
            break
        try:
            e = llm.complete_json(_prompt(c), OntoEnrich, task="search_bulk",
                                  node="onto_enrich", run_id=run_id, max_tokens=900)
            cache[c["id"]] = _valid(c, e)
            done += 1
            if done % 20 == 0:
                CACHE.write_text(json.dumps(cache, ensure_ascii=False))
                print(f"  enriched {done} (cache {len(cache)})", flush=True)
        except llm.BudgetExceeded as be:
            print("budget capped:", be)
            break
        except Exception as ex:  # noqa: BLE001
            print("err", c["id"], str(ex)[:80])
        time.sleep(0.25)  # gentle pace to avoid the provider's RPM burst limits
    CACHE.write_text(json.dumps(cache, ensure_ascii=False))
    print(f"enriched this run: {done}; cache total: {len(cache)}")


def report() -> None:
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    import collections
    add_themes = collections.Counter()
    routes = collections.Counter()
    suggests = collections.Counter()
    n_add = n_route = n_alias = n_better = 0
    for d in cache.values():
        for a in d.get("additional", []):
            add_themes[a["theme"]] += 1
        n_add += len(d.get("additional", []))
        for r in d.get("tech_routes", []):
            routes[r] += 1
        n_route += len(d.get("tech_routes", []))
        n_alias += len(d.get("extra_aliases", []))
        n_better += 1 if d.get("better_segment") else 0
        if d.get("suggest_route"):
            suggests[d["suggest_route"].lower()] += 1
    print(f"cache={len(cache)}  +themes={n_add}  +routes={n_route}  +aliases={n_alias}  better_seg={n_better}")
    print("additional-theme distribution:", dict(add_themes))
    print("top tech-routes tagged:", routes.most_common(12))
    print("top suggested NEW routes (extension candidates):", suggests.most_common(20))


def generate() -> None:
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    merged = 0
    recs = []
    for c in UNIVERSE:
        c = dict(c)
        d = cache.get(c["id"])
        if d:
            for a in d.get("additional", []):
                if a["theme"] not in c["themes"]:
                    c["themes"] = c["themes"] + [a["theme"]]
                    c["seg"] = {**c.get("seg", {}), a["theme"]: a["segment"]}
            if d.get("better_segment"):
                t = c["themes"][0]
                c["seg"] = {**c.get("seg", {}), t: d["better_segment"]}
                c["chain_role"] = d["better_segment"]
            if d.get("extra_aliases"):
                c["aliases"] = (c.get("aliases") or []) + [a for a in d["extra_aliases"] if a not in (c.get("aliases") or [])]
            routes = list(d.get("tech_routes") or [])
            sug = (d.get("suggest_route") or "").lower()
            for kw, rid in _SUGGEST_MAP.items():       # extension: tag the new route
                if kw in sug:
                    routes.append(rid)
                    break
            if routes:
                c["tech_routes"] = list(dict.fromkeys(routes))
            merged += 1
        c = _correct(c)   # apply audit-confirmed common-sense corrections
        recs.append(c)
    # universe.py is a PYTHON module — emit Python literals (None/True, unicode kept), not
    # JSON (which would write `null` and break the import).
    body = "UNIVERSE = [\n" + "\n".join("    " + repr(r) + "," for r in recs) + "\n]\n"
    header = ('"""Auto-generated universe roster (do NOT hand-edit). Built by scripts/universe_build.py\n'
              'and enriched by scripts/ontology_enrich.py. Appended to registry.COMPANIES."""\n')
    UNIVERSE_PY.write_text(header + body)
    print(f"merged {merged} enrichments → wrote {len(recs)} records to {UNIVERSE_PY}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"
    kw = {}
    for a in sys.argv[2:]:
        if a.startswith("--limit"):
            kw["limit"] = int(a.split("=")[1])
        if a.startswith("--sample"):
            kw["sample"] = int(a.split("=")[1])
    {"enrich": lambda: enrich(**kw), "report": report, "generate": generate}[cmd]()
