"""登记簿状态跃迁外发（Phase 2.4b）。

差异化内核之二是"活监控"——光算 verdict 不够，**状态跃迁**（如某断言由 open→falsified、
或 inconclusive→fixation_triggered）才是要惊动人的事件。本模块：

  · `detect_transitions` —— 纯函数：对比"上一轮 status"与"本轮 verdict"，挑出真正变化、
    且值得告警的跃迁（默认：进入终局态 falsified/fixation_triggered/expired，或离开它们）。
  · `post_slack` —— 用标准库 urllib 发 Slack incoming-webhook（不引入 requests 依赖）；
    SLACK_WEBHOOK_URL 未配置 → 安静 no-op（CI/本地默认不外发）。

设计纪律：判定逻辑不在此（仍以 engine.overclaim 为真值）；此处只做"变化检测 + 外发"。
"""
from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass

# 终局/可处置态：进入或离开这些状态值得惊动人。
_ACTIONABLE = {"falsified", "fixation_triggered", "expired"}

_MARK = {
    "falsified": "✗ 证伪", "fixation_triggered": "● 固化", "expired": "⌛ 过期",
    "inconclusive": "… 待识别", "open": "○ 未决",
}


@dataclass(frozen=True)
class Transition:
    claim_key: str
    old: str | None
    new: str

    def is_actionable(self) -> bool:
        # 进入终局态，或从终局态退出（如证伪被新数据推翻回 open），都值得告警。
        return (self.new in _ACTIONABLE or (self.old in _ACTIONABLE)) and self.old != self.new

    def line(self) -> str:
        old = _MARK.get(self.old or "", self.old or "（无）")
        return f"{self.claim_key}：{old} → {_MARK.get(self.new, self.new)}"


def detect_transitions(prev_new: list[tuple[str, str | None, str]]) -> list[Transition]:
    """prev_new = [(claim_key, old_status, new_verdict), ...] → 值得告警的跃迁列表。"""
    out = [Transition(ck, old, new) for ck, old, new in prev_new]
    return [t for t in out if t.is_actionable()]


def post_slack(transitions: list[Transition], as_of: str, webhook: str | None = None) -> bool:
    """发 Slack（incoming-webhook）。无 webhook 或无跃迁 → no-op 返回 False。"""
    webhook = webhook if webhook is not None else os.environ.get("SLACK_WEBHOOK_URL", "").strip()
    if not webhook or not transitions:
        return False
    header = f":rotating_light: 过度宣称登记簿状态跃迁 @ as_of={as_of}"
    body = "\n".join(f"• {t.line()}" for t in transitions)
    payload = json.dumps({"text": f"{header}\n{body}"}).encode("utf-8")
    req = urllib.request.Request(
        webhook, data=payload, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 —— 受信 webhook URL
            return 200 <= resp.status < 300
    except Exception:  # noqa: BLE001 —— 告警失败绝不能阻断判定流水线
        return False


def notify(prev_new: list[tuple[str, str | None, str]], as_of: str,
           webhook: str | None = None) -> list[Transition]:
    """检测跃迁并（若配置了 webhook）外发；返回检出的可处置跃迁，供调用方记录/打印。"""
    transitions = detect_transitions(prev_new)
    post_slack(transitions, as_of, webhook)
    return transitions
