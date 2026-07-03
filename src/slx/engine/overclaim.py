"""过度宣称登记簿判定引擎 —— 差异化内核的活监控。

把 K 文第六节的 fixation_rule / falsify_rule 在 point-in-time 视图上求值，得出每条断言的
verdict：fixation_triggered（断言固化）| falsified（断言被证伪）| expired（判定窗已过仍未决）|
inconclusive（v1 暂不可判，如依赖 DID/面板）| open。

DSL 安全求值：支持 value(metric)、slope(metric, Nq)，以及 Phase 2.2 识别族函数
did_estimate / did_pvalue / panel_fixed_effects / panel_fixed_effects_pvalue。
仍未支持的函数（significant / structural_change / excess_return_after_beta_strip ...）
的规则一律判 inconclusive——这把"绝不把相关当因果"焊进执行路径：未拿到识别的 soft
断言不会被误判为成立。

识别族函数是**纯语法糖**：它们展开成 value(<派生指标>)，读取 engine/identification.py
经双时态写回的"系数 / p 值"派生 observation（约定后缀 .did.coef / .did.pvalue /
.fe.coef / .fe.pvalue）。识别只在 identification 运行器里算【一次】，此处与 dbt 侧
(macros/overclaim_rule.sql) 用同一展开读同一持久化估计——故 assert_overclaim_parity 天然为绿。

    python -m engine.overclaim [YYYY-MM-DD]
"""
from __future__ import annotations

import ast
import calendar
import json
import operator
import re
import sys
from datetime import date

from slx.engine.point_in_time import NoData, PointInTimeContext

# value/slope（原生）+ 识别族（语法糖，展开为 value）。与 dbt supported_overclaim_funcs() 同步。
SUPPORTED_FUNCS = {
    "value", "slope",
    "did_estimate", "did_pvalue", "panel_fixed_effects", "panel_fixed_effects_pvalue",
}

_FUNC_NAME_RE = re.compile(r"([a-zA-Z_][a-zA-Z0-9_]*)\s*\(")
_VALUE_RE = re.compile(r"value\(\s*([a-z0-9_.]+)\s*\)")
_SLOPE_RE = re.compile(r"slope\(\s*([a-z0-9_.]+)\s*,\s*(\d+)\s*q\s*\)")

# 识别族语法糖 → value(<派生指标>)。后缀约定与 ingestion/identification_panels.DERIVED_KEYS
# 及 dbt macros/overclaim_rule.sql 的展开【逐字一致】。`\(` 锚点保证 panel_fixed_effects(
# 不会误吃 panel_fixed_effects_pvalue(。
_IDENT_SUGAR = [
    (re.compile(r"did_pvalue\(\s*([a-z0-9_.]+)\s*\)"), r"value(\1.did.pvalue)"),
    (re.compile(r"did_estimate\(\s*([a-z0-9_.]+)\s*\)"), r"value(\1.did.coef)"),
    (re.compile(r"panel_fixed_effects_pvalue\(\s*([a-z0-9_.]+)\s*\)"), r"value(\1.fe.pvalue)"),
    (re.compile(r"panel_fixed_effects\(\s*([a-z0-9_.]+)\s*\)"), r"value(\1.fe.coef)"),
]


def _expand_identification_sugar(rule: str) -> str:
    """把识别族函数展开成 value(<派生指标>)；其余原样返回。"""
    for pat, repl in _IDENT_SUGAR:
        rule = pat.sub(repl, rule)
    return rule

_CMP = {
    ast.Lt: operator.lt, ast.LtE: operator.le,
    ast.Gt: operator.gt, ast.GtE: operator.ge,
    ast.Eq: operator.eq, ast.NotEq: operator.ne,
}


class NotEvaluable(Exception):
    """规则含 v1 未支持的算子/函数（如 DID、统计显著性）。"""


# ── 安全布尔求值（ast 白名单，绝不 eval 任意代码）──────────────────────────────
def _ev(node):
    if isinstance(node, ast.BoolOp):
        vals = [_ev(v) for v in node.values]
        return all(vals) if isinstance(node.op, ast.And) else any(vals)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return not _ev(node.operand)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -_ev(node.operand)
    if isinstance(node, ast.Compare):
        left = _ev(node.left)
        ok = True
        for op, comp in zip(node.ops, node.comparators):
            right = _ev(comp)
            if type(op) not in _CMP:
                raise NotEvaluable([type(op).__name__])
            ok = ok and _CMP[type(op)](left, right)
            left = right
        return ok
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float, bool)):
        return node.value
    raise NotEvaluable([type(node).__name__])


def _safe_bool(expr: str) -> bool:
    return bool(_ev(ast.parse(expr, mode="eval").body))


def evaluate_rule(rule: str, ctx) -> bool:
    """对单条规则求值。含未支持函数 → NotEvaluable；缺数据 → NoData。"""
    names = set(_FUNC_NAME_RE.findall(rule))
    unsupported = names - SUPPORTED_FUNCS
    if unsupported:
        raise NotEvaluable(sorted(unsupported))
    rule = _expand_identification_sugar(rule)  # 识别族 → value(派生指标)，随后走 value 解析
    expr = _SLOPE_RE.sub(lambda m: repr(ctx.slope(m.group(1), int(m.group(2)))), rule)
    expr = _VALUE_RE.sub(lambda m: repr(ctx.value(m.group(1))), expr)
    expr = re.sub(r"\bAND\b", " and ", expr)
    expr = re.sub(r"\bOR\b", " or ", expr)
    expr = re.sub(r"\bNOT\b", " not ", expr)
    return _safe_bool(expr)


def _add_months(d: date, n: int) -> date:
    m = d.month - 1 + n
    y = d.year + m // 12
    m = m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def window_months(window: str) -> int:
    """'1-2y'→24，'2-3y'→36，'10-15y'→180，'1-2q'→6，'2y'→24（取上界）。"""
    nums = re.findall(r"\d+", window)
    hi = int(nums[-1]) if nums else 0
    return hi * 12 if window.strip().endswith("y") else hi * 3


def readings(claim: dict, ctx) -> dict:
    """采集相关指标当前读数，作为证据快照（缺数据记 None）。"""
    out = {}
    for m in claim.get("related_metrics", []):
        try:
            out[m] = ctx.value(m)
        except NoData:
            out[m] = None
    return out


def evaluate_claim(claim: dict, ctx: PointInTimeContext) -> tuple[str, dict]:
    """返回 (verdict, evidence)。语义：fixation_rule 成立→fixation_triggered；
    否则 falsify_rule 成立→falsified；否则按判定窗判 expired/open；
    任一规则不可求值或缺数据→inconclusive。"""
    evidence = readings(claim, ctx)
    try:
        if evaluate_rule(claim["fixation_rule"], ctx):
            return "fixation_triggered", evidence
        if evaluate_rule(claim["falsify_rule"], ctx):
            return "falsified", evidence
    except (NotEvaluable, NoData):
        return "inconclusive", evidence

    ws = claim["window_start"]
    if isinstance(ws, str):
        ws = date.fromisoformat(ws)
    expiry = _add_months(ws, window_months(claim["decision_window"]))
    return ("expired" if ctx.as_of > expiry else "open"), evidence


# ── 批量评估全部登记簿断言，写 eval_log + 更新 status ──────────────────────────
def run(as_of: date | None = None) -> list[tuple[str, str]]:
    from slx.db import connect

    as_of = as_of or date.today()
    results: list[tuple[str, str]] = []
    prev_new: list[tuple[str, str | None, str]] = []  # (claim_key, 上一轮 status, 本轮 verdict)
    with connect() as conn:
        claims = conn.execute(
            "SELECT claim_key, claim_text_zh, related_metrics, decision_window, "
            "window_start, fixation_rule, falsify_rule, status FROM overclaim_registry ORDER BY claim_key"
        ).fetchall()
        ctx = PointInTimeContext(conn, as_of)
        for ck, text, related, dw, ws, fix, fals, old_status in claims:
            claim = {
                "claim_key": ck, "related_metrics": related or [], "decision_window": dw,
                "window_start": ws, "fixation_rule": fix, "falsify_rule": fals,
            }
            verdict, evidence = evaluate_claim(claim, ctx)
            triggered = verdict in ("fixation_triggered", "falsified", "expired")
            conn.execute(
                "INSERT INTO overclaim_eval_log (claim_key, as_of_date, verdict, metric_readings, triggered) "
                "VALUES (%s,%s,%s,%s,%s)",
                (ck, as_of, verdict, json.dumps(evidence), triggered),
            )
            conn.execute(
                "UPDATE overclaim_registry SET status=%s, last_evaluated=now(), evidence_snapshot=%s "
                "WHERE claim_key=%s",
                (verdict, json.dumps(evidence), ck),
            )
            prev_new.append((ck, old_status, verdict))
            results.append((ck, verdict))
        conn.commit()
    # 状态跃迁外发（Slack；未配 SLACK_WEBHOOK_URL 时安静 no-op，绝不阻断判定）。
    try:
        from slx.engine.notify import notify

        notify(prev_new, as_of.isoformat())
    except Exception:  # noqa: BLE001 —— 外发失败不影响判定结果
        pass
    return results


if __name__ == "__main__":
    when = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date.today()
    print(f"过度宣称登记簿评估 @ as_of={when}")
    print("─" * 60)
    for ck, verdict in run(when):
        mark = {"falsified": "✗证伪", "fixation_triggered": "●固化",
                "expired": "⌛过期", "inconclusive": "…待识别", "open": "○未决"}.get(verdict, verdict)
        print(f"  {mark:8} {ck}")
