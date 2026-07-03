"""本体完整性（CI 红线，无需 DB）：JSON Schema + 锚点词表 + 引用 + 三层纪律。"""
from __future__ import annotations

from pathlib import Path

import yaml

from slx.tools.validate_registry import main as validate

import slx
REG = Path(slx.__file__).resolve().parent / "registry"  # XAR vendor: registry lives in the slx package


def test_registry_validates_green():
    assert validate() == 0


def test_every_soft_metric_has_identification():
    for mf in (REG / "metrics").glob("*.yml"):
        for m in yaml.safe_load(mf.read_text("utf-8"))["metrics"]:
            if m["hardness"] == "soft":
                assert m.get("identification_strategy"), f"soft 指标缺识别策略: {m['metric_key']}"


def test_every_wall_is_non_quantifiable():
    for mf in (REG / "metrics").glob("*.yml"):
        for m in yaml.safe_load(mf.read_text("utf-8"))["metrics"]:
            if m["hardness"] == "wall":
                assert m.get("is_quantifiable") is False, f"wall 指标须 is_quantifiable=false: {m['metric_key']}"


def test_overclaim_related_metrics_exist():
    metric_keys = set()
    for mf in (REG / "metrics").glob("*.yml"):
        for m in yaml.safe_load(mf.read_text("utf-8"))["metrics"]:
            metric_keys.add(m["metric_key"])
    claims = yaml.safe_load((REG / "overclaim_registry.yml").read_text("utf-8"))["claims"]
    for c in claims:
        for rm in c.get("related_metrics", []):
            assert rm in metric_keys, f"登记簿 {c['claim_key']} 引用了未登记指标 {rm}"
