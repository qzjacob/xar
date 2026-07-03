"""理论本体 CI 红线校验器（离线，无需数据库）。

把"经过审讯的序列"这条纪律做成可执行的门禁：
  1) registry/*.yml 须通过对应 JSON Schema（含 soft⇒identification_strategy、wall⇒is_quantifiable=false）。
  2) 每个 metric.theory_anchor 元素都在 theory_anchors.yml 受控词表内。
  3) metric_key / claim_key 不重复。
  4) overclaim.related_metrics 每个都指向已登记指标。
  5) 每条 active 指标都有至少一个数据源（wall 不可量化项除外）。

任一违反即非零退出——对接 CI / pre-commit。

    python -m tools.validate_registry
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parent.parent          # siliconomics/
REG = ROOT / "registry"
SCHEMA_DIR = REG / "schema"


def _load_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _schema_errors(label: str, schema: dict, doc: dict) -> list[str]:
    validator = Draft202012Validator(schema)
    out: list[str] = []
    for err in sorted(validator.iter_errors(doc), key=lambda e: list(e.path)):
        loc = "/".join(str(p) for p in err.path) or "<root>"
        out.append(f"[schema] {label} @ {loc}: {err.message}")
    return out


def main() -> int:
    errors: list[str] = []

    anchor_schema = json.loads((SCHEMA_DIR / "theory_anchor.schema.json").read_text("utf-8"))
    metric_schema = json.loads((SCHEMA_DIR / "metric.schema.json").read_text("utf-8"))
    overclaim_schema = json.loads((SCHEMA_DIR / "overclaim.schema.json").read_text("utf-8"))

    # ── 1) 理论锚点受控词表 ───────────────────────────────────────────────
    anchors_doc = _load_yaml(REG / "theory_anchors.yml")
    errors += _schema_errors("theory_anchors.yml", anchor_schema, anchors_doc)
    anchor_keys = {a["anchor_key"] for a in anchors_doc.get("anchors", [])}

    # ── 2) 指标注册表 ─────────────────────────────────────────────────────
    metrics: dict[str, dict] = {}
    by_hardness: dict[str, int] = {}
    for mf in sorted((REG / "metrics").glob("*.yml")):
        doc = _load_yaml(mf)
        errors += _schema_errors(mf.name, metric_schema, doc)
        for m in doc.get("metrics", []):
            key = m.get("metric_key", "<missing>")
            if key in metrics:
                errors.append(f"[dup] metric_key 重复: {key}（{mf.name} 与 {metrics[key]['_file']}）")
            m["_file"] = mf.name
            metrics[key] = m
            by_hardness[m.get("hardness", "?")] = by_hardness.get(m.get("hardness", "?"), 0) + 1

            for a in m.get("theory_anchor", []):
                if a not in anchor_keys:
                    errors.append(f"[anchor] {key}: theory_anchor '{a}' 不在受控词表内")

            if m.get("hardness") == "soft" and not m.get("identification_strategy"):
                errors.append(f"[discipline] soft 指标 {key} 缺 identification_strategy（绝不把相关当因果）")

            if m.get("hardness") == "wall" and m.get("is_quantifiable", True) is not False:
                errors.append(f"[discipline] wall 指标 {key} 必须 is_quantifiable=false")

            quantifiable_active = m.get("hardness") != "wall" and m.get("status") == "active"
            if quantifiable_active and not m.get("sources"):
                errors.append(f"[source] active 可量化指标 {key} 无数据源")

    # ── 3) 过度宣称登记簿 ─────────────────────────────────────────────────
    oc_doc = _load_yaml(REG / "overclaim_registry.yml")
    errors += _schema_errors("overclaim_registry.yml", overclaim_schema, oc_doc)
    claim_keys: set[str] = set()
    for c in oc_doc.get("claims", []):
        ck = c.get("claim_key", "<missing>")
        if ck in claim_keys:
            errors.append(f"[dup] claim_key 重复: {ck}")
        claim_keys.add(ck)
        for rm in c.get("related_metrics", []):
            if rm not in metrics:
                errors.append(f"[ref] overclaim '{ck}': related_metric '{rm}' 未登记")

    # ── 报告 ──────────────────────────────────────────────────────────────
    print("─" * 64)
    print(f"理论锚点 anchors        : {len(anchor_keys)}")
    print(f"指标 metrics            : {len(metrics)}  {dict(sorted(by_hardness.items()))}")
    print(f"登记簿 overclaims       : {len(claim_keys)}")
    print("─" * 64)
    if errors:
        print(f"✗ 校验失败，共 {len(errors)} 处：")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("✓ 本体校验通过：JSON Schema + 锚点词表 + 引用完整性 + 三层纪律 全绿。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
