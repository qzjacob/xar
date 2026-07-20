#!/usr/bin/env python3
"""Phase 4 赛马评测:候选本地模型 vs 现役 glm4-local vs 云参照,走生产同源抽取路径打分。

在 minis 宿主、repo 根目录运行(main-db-1 暴露 localhost:5432;ollama 在 172.17.0.1:11434,
宿主 /etc/hosts 有 host.docker.internal 同名映射):

    # 1) 冻结黄金集(一次)
    python3 scripts/bench_local_llm.py --pick
    # 2) 云参照 + 基线 + 候选(可分窗拆跑,--models 子集,结果按模型分文件可合并)
    python3 scripts/bench_local_llm.py --models glm-5.2-sub,glm4-local,qwen35-local
    # 3) 汇总 + 判定门走查(参照默认 glm-5.2-sub,基线默认 glm4-local)
    python3 scripts/bench_local_llm.py --summarize

设计要点(与 tests/test_glm_worker.py::test_json_instruction_matches_complete_json 互为守卫):
- prompt 字节级同产:kg.extract.build_extraction_prompt + llm.json_instruction。
- 不走 complete_json(它把坏 JSON 吞成空默认,评测必须自见原始输出);逐调分类
  exception/invalid_json/schema_invalid/ok,并记 <think> 泄漏。
- 单元素 llm.pinned((mid,)) = 零回退:失败即计量,绝不静默轮转云端。
- 接地打分复用 kg.extract._grounded;一致性对齐只比接地产物,云输出是「锚」非真值,
  晋升门相对现役基线(glm4-local)而非云。
- 绝不调用 resolve.resolve_or_create(会写 KG);唯一 DB 写入是 llm.complete 的
  llm_usage 记账行(run_id=batch-bench-*,批量预算帽语义,订阅模型 usd=0 恒不触帽)。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import time
import urllib.request
from pathlib import Path

BENCH_DIR = Path.home() / "Project/XAR/bench/phase4"
GOLDEN = BENCH_DIR / "golden_docs.json"
OLLAMA_URL = "http://host.docker.internal:11434"
MAX_CHARS = 12000          # 与 extract_from_document 默认一致
MAX_TOKENS = 4000          # 与 _llm_stage 的 kg 抽取一致
WARM_BACK = "glm4-xar"     # 跑完回暖现役模型,生产 worker 首个本地调用不吃冷加载

# 黄金集分层:source -> (篇数, 回看天数)。wechat 长文稀缺故回看 180d(计划注 6)。
STRATA = {"gangtise": (14, 60), "wechat": (6, 180), "rss": (8, 60),
          "finnhub": (4, 60), "edgar": (8, 60)}

_CORP_SUFFIX = re.compile(
    r"\b(inc|corp|corporation|ltd|limited|co|company|plc|sa|ag|nv|holdings?)\b\.?"
    r"|(股份有限公司|有限公司|株式会社|控股|集团|公司)")
_WS = re.compile(r"[\s\.,·\-—_/()（）'\"]+")


def norm_name(s: str) -> str:
    s = _CORP_SUFFIX.sub("", (s or "").lower())
    return _WS.sub("", s).strip()


def _units(s: str) -> set[str]:
    """模糊匹配单元:ASCII 词 + CJK 字 bigram(与 _grounded 的度量思路一致)。"""
    toks = set(re.findall(r"[a-z0-9]{2,}", s))
    cjk = re.findall(r"[㐀-鿿]", s)
    toks |= {cjk[i] + cjk[i + 1] for i in range(len(cjk) - 1)}
    return toks


def fuzzy_eq(a: str, b: str, thresh: float = 0.6) -> bool:
    if a == b:
        return True
    ua, ub = _units(a), _units(b)
    if not ua or not ub:
        return False
    return len(ua & ub) / len(ua | ub) >= thresh


def doc_lang(text: str) -> str:
    cjk = len(re.findall(r"[㐀-鿿]", text))
    return "cn" if cjk / max(len(text), 1) > 0.15 else "en"


# ── 资源采样(只读)──────────────────────────────────────────────────────────
def sample_resources() -> dict:
    out: dict = {}
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/ps", timeout=5) as r:
            ps = json.load(r)
        out["ollama_ps"] = [{"name": m.get("name"), "size_vram": m.get("size_vram")}
                            for m in ps.get("models", [])]
    except Exception as e:  # noqa: BLE001
        out["ollama_ps_err"] = str(e)[:80]
    try:
        smi = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10)
        out["gpu_mem_used_mib"] = int(smi.stdout.strip().splitlines()[0])
    except Exception as e:  # noqa: BLE001
        out["gpu_err"] = str(e)[:80]
    for k in ("current", "peak"):
        try:
            out[f"mlslice_{k}"] = int(Path(f"/sys/fs/cgroup/ml.slice/memory.{k}").read_text())
        except Exception:  # noqa: BLE001
            pass
    return out


def warm(model: str) -> None:
    try:
        req = urllib.request.Request(
            f"{OLLAMA_URL}/api/generate", method="POST",
            data=json.dumps({"model": model, "prompt": "ok",
                             "options": {"num_predict": 1}}).encode())
        urllib.request.urlopen(req, timeout=120).read()
        print(f"[warm] {model} loaded")
    except Exception as e:  # noqa: BLE001
        print(f"[warm] {model} FAILED: {str(e)[:120]}")


# ── 黄金集 ─────────────────────────────────────────────────────────────────
def pick_golden() -> None:
    from xar.storage import db
    BENCH_DIR.mkdir(parents=True, exist_ok=True)
    docs, short = [], []
    db.execute("SELECT setseed(0.42)")
    for source, (n, days) in STRATA.items():
        rows = db.query(
            "SELECT id, source, length(text) AS len, substr(text,1,%s) AS t FROM documents "
            "WHERE source=%s AND length(text)>800 "
            "AND ingested_at > now() - make_interval(days => %s) "
            "ORDER BY random() LIMIT %s", (MAX_CHARS, source, days, n))
        for r in rows:
            docs.append({"doc_id": r["id"], "source": source,
                         "sha256": hashlib.sha256(r["t"].encode()).hexdigest(),
                         "lang": doc_lang(r["t"])})
        if len(rows) < n:
            short.append(f"{source}: {len(rows)}/{n}")
    GOLDEN.write_text(json.dumps({"picked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                  "docs": docs}, ensure_ascii=False, indent=1))
    print(f"golden set: {len(docs)} docs -> {GOLDEN}")
    if short:
        print("  ⚠ 分层不足(如实计入,不静默凑数):", "; ".join(short))
    for lang in ("cn", "en"):
        print(f"  {lang}: {sum(1 for d in docs if d['lang'] == lang)}")


# ── 单模型跑批 ─────────────────────────────────────────────────────────────
def artifacts_of(res, text: str) -> dict:
    """接地产物 → 归一化键列表(对齐用)+ 计数。仅纯文本归一,绝不 resolve(防写库)。"""
    from xar.kg.extract import _grounded
    from xar.ontology import canonical_kpi
    edges = [e for e in res.edges if _grounded(e.evidence, text)]
    events = [e for e in res.events if _grounded(e.evidence, text)]
    metrics = [m for m in res.metrics if _grounded(m.evidence, text)]
    return {
        "n_nodes": len(res.nodes),
        "emitted": {"edges": len(res.edges), "events": len(res.events), "metrics": len(res.metrics)},
        "grounded": {"edges": len(edges), "events": len(events), "metrics": len(metrics)},
        "keys": {
            "edges": [[norm_name(e.src), norm_name(e.dst), e.rel_type] for e in edges],
            "events": [[e.event_type, norm_name(e.company), e.event_date or ""] for e in events],
            "metrics": [[canonical_kpi(m.metric) or m.metric, m.period or ""] for m in metrics],
        },
    }


def run_model(mid: str, docs: list[dict], limit: int | None) -> None:
    from xar.kg.extract import build_extraction_prompt
    from xar.models import llm
    from xar.ontology import ExtractionResult
    from xar.storage import db

    run_id = f"batch-bench-{mid}-{int(time.time())}"
    out_path = BENCH_DIR / f"{mid}.jsonl"
    fh = out_path.open("w")
    res_samples, t_model = [], time.monotonic()
    todo = docs[:limit] if limit else docs
    for i, g in enumerate(todo):
        rows = db.query("SELECT id, company_id, source, doc_type, title, text FROM documents WHERE id=%s",
                        (g["doc_id"],))
        rec: dict = {"doc_id": g["doc_id"], "source": g["source"], "lang": g["lang"]}
        if not rows:
            rec["status"] = "doc_missing"
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            continue
        d = rows[0]
        text = (d["text"] or "")[:MAX_CHARS]
        if hashlib.sha256(text.encode()).hexdigest() != g["sha256"]:
            rec["drift"] = True  # 文档漂移如实记录,仍照跑
        system, prompt = build_extraction_prompt(d, text)
        instruction = llm.json_instruction(prompt, ExtractionResult)
        t0 = time.monotonic()
        raw, status = "", "ok"
        try:
            with llm.pinned((mid,)):
                raw = llm.complete(instruction, system=system, task="kg_extract",
                                   node="bench", run_id=run_id, max_tokens=MAX_TOKENS,
                                   json_mode=True)
        except Exception as e:  # noqa: BLE001
            name = type(e).__name__
            status = ("timeout" if "Timeout" in name
                      else "connection" if "Connection" in name or "ServiceUnavailable" in name
                      else "empty" if "empty completion" in str(e)
                      else "error")
            rec["error"] = f"{name}: {str(e)[:160]}"
        rec["latency_s"] = round(time.monotonic() - t0, 2)
        rec["think_leak"] = raw.count("<think>")
        if status == "ok":
            obj = llm._extract_json(raw)
            if obj is None:
                status = "invalid_json"
            else:
                try:
                    result = ExtractionResult.model_validate(obj)
                    rec.update(artifacts_of(result, text))
                except Exception as e:  # noqa: BLE001
                    status = "schema_invalid"
                    rec["error"] = str(e)[:160]
        if status != "ok":
            rec["raw_excerpt"] = raw[:500]
        rec["status"] = status
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        fh.flush()
        print(f"  [{mid}] {i+1}/{len(todo)} {g['doc_id'][:12]} {status} {rec['latency_s']}s")
        if i in (0, len(todo) // 2):        # 首调(热身后)与中程各采一次资源
            res_samples.append(sample_resources())
    fh.close()

    usage = db.query(
        "SELECT COUNT(*) AS calls, COALESCE(SUM(input_tokens),0) AS tin, "
        "COALESCE(SUM(output_tokens),0) AS tout FROM llm_usage WHERE run_id=%s", (run_id,))
    meta = {"model": mid, "run_id": run_id, "docs": len(todo),
            "wall_s": round(time.monotonic() - t_model, 1),
            "usage": dict(usage[0]) if usage else {},
            "resources": res_samples}
    (BENCH_DIR / f"{mid}.meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    print(f"[{mid}] done: {meta['wall_s']}s, usage={meta['usage']}")


# ── 汇总与判定 ─────────────────────────────────────────────────────────────
def _match_sets(cand: dict, ref: dict) -> tuple[int, int, int]:
    """(matched, n_cand, n_ref) — 键精确匹配优先,名字段模糊兜底(Jaccard≥0.6)。"""
    matched, used = 0, set()
    for cls in ("edges", "events", "metrics"):
        ck, rk = cand["keys"].get(cls, []), ref["keys"].get(cls, [])
        for c in ck:
            hit = None
            for j, r in enumerate(rk):
                if (cls, j) in used:
                    continue
                if cls == "edges":
                    ok = c[2] == r[2] and fuzzy_eq(c[0], r[0]) and fuzzy_eq(c[1], r[1])
                elif cls == "events":
                    ok = c[0] == r[0] and fuzzy_eq(c[1], r[1]) and (not c[2] or not r[2] or c[2] == r[2])
                else:
                    ok = c[0] == r[0] and (not c[1] or not r[1] or c[1] == r[1])
                if ok:
                    hit = j
                    break
            if hit is not None:
                used.add((cls, hit))
                matched += 1
    n_cand = sum(len(cand["keys"].get(c, [])) for c in ("edges", "events", "metrics"))
    n_ref = sum(len(ref["keys"].get(c, [])) for c in ("edges", "events", "metrics"))
    return matched, n_cand, n_ref


def _load(mid: str) -> dict[str, dict]:
    p = BENCH_DIR / f"{mid}.jsonl"
    if not p.exists():
        return {}
    return {json.loads(ln)["doc_id"]: json.loads(ln) for ln in p.read_text().splitlines() if ln}


def summarize(models: list[str], ref_id: str, baseline: str) -> None:
    ref = _load(ref_id)
    table = []
    for mid in models:
        rows = _load(mid)
        if not rows:
            print(f"[skip] no results for {mid}")
            continue
        n = len(rows)
        ok = [r for r in rows.values() if r["status"] == "ok"]
        lat = sorted(r.get("latency_s", 0) for r in rows.values())
        f1s: dict[str, list[float]] = {"all": [], "cn": [], "en": []}
        gy, gp_num, gp_den = [], 0, 0
        for r in ok:
            g = sum(r["grounded"].values())
            gy.append(g)
            gp_num += g
            gp_den += sum(r["emitted"].values())
            rr = ref.get(r["doc_id"])
            if rr and rr.get("status") == "ok" and mid != ref_id:
                m, nc, nr = _match_sets(r, rr)
                p = m / nc if nc else 0.0
                rec = m / nr if nr else (1.0 if nc == 0 else 0.0)
                f1 = 2 * p * rec / (p + rec) if p + rec else 0.0
                f1s["all"].append(f1)
                f1s[r["lang"]].append(f1)
        meta = json.loads((BENCH_DIR / f"{mid}.meta.json").read_text()) if (BENCH_DIR / f"{mid}.meta.json").exists() else {}
        u = meta.get("usage", {})
        vram = max((m.get("size_vram") or 0) for s in meta.get("resources", [{}])
                   for m in s.get("ollama_ps", [{}])) if meta.get("resources") else 0
        avg = lambda xs: round(sum(xs) / len(xs), 3) if xs else None  # noqa: E731
        table.append({
            "model": mid, "docs": n,
            "ok_rate": round(len(ok) / n, 3),
            "fail": {s: sum(1 for r in rows.values() if r["status"] == s)
                     for s in ("invalid_json", "schema_invalid", "timeout", "connection", "empty", "error")},
            "think_leaks": sum(r.get("think_leak", 0) for r in rows.values()),
            "lat_p50": lat[len(lat) // 2] if lat else None,
            "lat_p95": lat[int(len(lat) * 0.95) - 1] if len(lat) >= 2 else (lat[-1] if lat else None),
            "grounded_yield_avg": avg(gy),
            "grounded_precision": round(gp_num / gp_den, 3) if gp_den else None,
            "agree_f1": avg(f1s["all"]), "agree_f1_cn": avg(f1s["cn"]), "agree_f1_en": avg(f1s["en"]),
            "tok_in_avg": round(u["tin"] / u["calls"]) if u.get("calls") else None,
            "tok_out_avg": round(u["tout"] / u["calls"]) if u.get("calls") else None,
            "vram_max_gib": round(vram / 2**30, 2) if vram else None,
            "wall_s": meta.get("wall_s"),
        })
    (BENCH_DIR / "summary.json").write_text(json.dumps(table, ensure_ascii=False, indent=1))
    cols = ("model", "ok_rate", "think_leaks", "lat_p95", "grounded_yield_avg",
            "grounded_precision", "agree_f1", "agree_f1_cn", "agree_f1_en",
            "tok_out_avg", "vram_max_gib")
    print("\n== summary (full: bench/phase4/summary.json) ==")
    print(" | ".join(f"{c:>18}" for c in cols))
    for row in table:
        print(" | ".join(f"{str(row.get(c)):>18}" for c in cols))
    base = next((r for r in table if r["model"] == baseline), None)
    print(f"\n== 判定门(硬门:ok≥0.95, think=0, p95≤60s;晋升门:F1≥基线+0.05 且 CN-F1≥基线 且 yield≥基线) ==")
    for row in table:
        if row["model"] in (ref_id, baseline):
            continue
        hard = (row["ok_rate"] >= 0.95 and row["think_leaks"] == 0
                and (row["lat_p95"] or 999) <= 60)
        promo = bool(base and row["agree_f1"] is not None and base["agree_f1"] is not None
                     and row["agree_f1"] >= base["agree_f1"] + 0.05
                     and (row["agree_f1_cn"] or 0) >= (base["agree_f1_cn"] or 0)
                     and (row["grounded_yield_avg"] or 0) >= (base["grounded_yield_avg"] or 0))
        print(f"  {row['model']}: 硬门 {'PASS' if hard else 'FAIL'} | 晋升门 {'PASS' if promo else 'FAIL'}"
              f" (VRAM {row['vram_max_gib']}G — 档位红线人工核对: 9B≤10G / 14B≤11G)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pick", action="store_true", help="冻结黄金集(一次)")
    ap.add_argument("--models", default="", help="逗号分隔 registry id(含云参照/基线)")
    ap.add_argument("--ref", default="glm-5.2-sub", help="一致性参照(锚)模型 id")
    ap.add_argument("--baseline", default="glm4-local", help="晋升门基线模型 id")
    ap.add_argument("--limit", type=int, default=None, help="每模型只跑前 N 篇(冒烟)")
    ap.add_argument("--summarize", action="store_true", help="从已有 jsonl 汇总+判定")
    ap.add_argument("--no-warm-back", action="store_true")
    args = ap.parse_args()

    if args.pick:
        pick_golden()
        return
    if args.summarize:
        known = sorted(p.stem for p in BENCH_DIR.glob("*.jsonl"))
        summarize(known, args.ref, args.baseline)
        return
    if not args.models:
        ap.error("--models 或 --pick 或 --summarize 必选其一")
    golden = json.loads(GOLDEN.read_text())["docs"]
    for mid in [m.strip() for m in args.models.split(",") if m.strip()]:
        print(f"== running {mid} over {len(golden)} docs ==")
        run_model(mid, golden, args.limit)
    if not args.no_warm_back:
        warm(WARM_BACK)


if __name__ == "__main__":
    main()
