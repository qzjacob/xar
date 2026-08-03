"""共享的小型持久 key/value 状态(JSONB)—— 常驻工人额度状态、回填游标、节拍等。

单一实现取代 glm_worker/history 各自的私有拷贝:DDL 的权威在 schema.sql
(glm_worker_state 表);这里仅保留一次性的防御式建表(进程内只执行一次),
供 `xar init` 之前的裸 CLI 调用兜底。
"""
from __future__ import annotations

import json

from . import db

_DDL = ("CREATE TABLE IF NOT EXISTS glm_worker_state ("
        "key TEXT PRIMARY KEY, value JSONB NOT NULL, "
        "updated_at TIMESTAMPTZ NOT NULL DEFAULT now())")
_ensured = False


def _ensure() -> None:
    global _ensured
    if not _ensured:
        db.execute(_DDL)
        _ensured = True


def get_state(key: str, default: dict | None = None) -> dict:
    _ensure()
    rows = db.query("SELECT value FROM glm_worker_state WHERE key=%s", (key,))
    return rows[0]["value"] if rows else (default if default is not None else {})


def save_state(key: str, value: dict) -> None:
    _ensure()
    db.execute("INSERT INTO glm_worker_state(key, value, updated_at) "
               "VALUES (%s, %s::jsonb, now()) "
               "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=now()",
               (key, json.dumps(value, ensure_ascii=False, default=str)))


def set_state_field(key: str, field: str, value) -> None:
    """**只写 blob 里的一个字段**,不碰其余字段(2026-08-02 加)。

    为什么必须有这个:`get_state` → 改 → `save_state` 是读-改-写整块 JSON,
    只要那次读拿到的是空/陈旧的字典,回写就会把**其余所有字段一起抹掉**。
    cadence 尤其致命 —— 它一块 blob 里装着 13 个拉取源的心跳戳,
    抹一次就等于 13 个源在监控面板上集体失联(2026-08-02 实测 15 个任务翻 unknown)。
    同类事故本会话已经发生过两次(另一次是 `actions.trigger_pull`,当时也是改成 jsonb_set 修的)。

    这里把「读-改-写」换成数据库端的一次原子 `jsonb_set`:并发写不同字段互不覆盖,
    读到空也不可能造成整块丢失 —— 因为根本不读。
    """
    _ensure()
    payload = json.dumps(value, ensure_ascii=False, default=str)
    db.execute(
        "INSERT INTO glm_worker_state(key, value, updated_at) "
        # 每个占位符都要显式 ::cast —— 否则 Postgres 推不出 jsonb_build_object 的参数类型,
        # 直接报 IndeterminateDatatype(实测)。
        "VALUES (%s::text, jsonb_build_object(%s::text, %s::jsonb), now()) "
        "ON CONFLICT (key) DO UPDATE SET "
        "  value = jsonb_set(coalesce(glm_worker_state.value, '{}'::jsonb), "
        "                    ARRAY[%s::text], %s::jsonb, true), "
        "  updated_at = now()",
        (key, field, payload, field, payload))


def merge_state_field(key: str, field: str, patch: dict) -> None:
    """把 `patch` **浅合并**进 blob 的一个二层子对象,不碰兄弟子对象、也不碰该子对象里
    未出现在 patch 中的字段。用于 `sub_quota` 这类 `{provider: {...}}` 的两层状态。

    ⚠️ **为什么不用 `jsonb_set(value, ARRAY['zhipu','status'], ...)` 这种深 path**:
    jsonb_set 的 `create_missing` **只创建路径的最后一段**。当 `value->'zhipu'` 还不存在时,
    整条语句会**静默 no-op**(返回原值、不报错)—— 这是最难查的一类 bug:写了、没报错、
    数据没进去。所以这里 path 只到一层(provider),第二层用 `||` 做浅合并,
    并用 `coalesce(... -> field, '{}')` 兜住子对象不存在的情况。

    ⚠️ 为什么 `sub_quota` 需要它:那块 blob 有**三个进程**在写(subpool 容器 / glmworker /
    app 的 on-demand phanny),而 `subpool._STATE_LOCK` 是 `threading.Lock` —— 跨容器完全无效。
    整块读-改-写下,两个容器同时冷却不同 provider 会丢一次更新:被丢掉的那家仍被当作可用,
    继续派活直到再失败三次。合并放到 DB 端做,这个窗口才真正关掉。
    """
    _ensure()
    payload = json.dumps(patch, ensure_ascii=False, default=str)
    db.execute(
        "INSERT INTO glm_worker_state(key, value, updated_at) "
        "VALUES (%s::text, jsonb_build_object(%s::text, %s::jsonb), now()) "
        "ON CONFLICT (key) DO UPDATE SET "
        "  value = jsonb_set(coalesce(glm_worker_state.value, '{}'::jsonb), "
        "                    ARRAY[%s::text], "
        "                    coalesce(glm_worker_state.value -> %s::text, '{}'::jsonb) "
        "                    || %s::jsonb, true), "
        "  updated_at = now()",
        (key, field, payload, field, field, payload))


def delete_state(key: str) -> None:
    _ensure()
    db.execute("DELETE FROM glm_worker_state WHERE key=%s", (key,))
