"""任务监控:实时状态 + 停摆检测 + 报警 + 处置。

2026-07-29 全链路审计的产物。当时暴露的不是某个 bug,而是「停摆不可见」这一类问题:
Dagster 队列死锁 7 天零执行无人察觉、glmworker 被 phanny 拖死 3.5 小时只能翻 docker logs、
wechat/futu 静默哑火数周而 cadence 戳仍绿。本包把这些信号收拢成一个可判、可报、可复盘的面。

模块分工:
  catalog   —— 任务注册表(code-as-truth)+ 只读探针
  detector  —— 纯状态机(零 I/O,fake-clock 可测),规避审计发现的三个判定陷阱
  dagster_gql — Dagster 只读探针(GraphQL,不碰 sqlite 卷)
  sweep     —— 巡检循环:判态 → 写历史 → 驱动告警 → 写自身心跳 → 惰性清理
  alerts    —— 告警生命周期 + Telegram 推送(去重靠部分唯一索引,不靠应用记账)
  control   —— worker 侧软重启协议(零 docker socket)
  actions   —— 面板处置动作
"""
from __future__ import annotations

from .sweep import start_background

__all__ = ["start_background"]
