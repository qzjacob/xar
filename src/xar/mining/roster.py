"""策展微信账号名册(wechat_accounts 表)—— 运营方在 we-mp-rss UI 订阅垂直号后,
在此登记 feed_id → 主题/公司/层级。T1 采集据此逐号拉取(名册空则退回聚合 /rss)。
"""
from __future__ import annotations

from ..storage import db


def active_feeds() -> list[dict]:
    return db.query("SELECT feed_id, name, theme, segment, company_id, tier "
                    "FROM wechat_accounts WHERE active ORDER BY tier, feed_id")


def register(feed_id: str, *, name: str = "", theme: str | None = None,
             segment: str | None = None, company_id: str | None = None,
             tier: int = 2) -> None:
    db.execute(
        "INSERT INTO wechat_accounts(feed_id, name, theme, segment, company_id, tier) "
        "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (feed_id) DO UPDATE SET "
        "name=EXCLUDED.name, theme=EXCLUDED.theme, segment=EXCLUDED.segment, "
        "company_id=EXCLUDED.company_id, tier=EXCLUDED.tier, active=TRUE",
        (feed_id, name, theme, segment, company_id, tier))


def deactivate(feed_id: str) -> None:
    db.execute("UPDATE wechat_accounts SET active=FALSE WHERE feed_id=%s", (feed_id,))


def status() -> dict:
    rows = db.query("SELECT count(*) total, count(*) FILTER (WHERE active) active, "
                    "count(DISTINCT theme) themes, count(company_id) bound FROM wechat_accounts")
    return dict(rows[0]) if rows else {}
