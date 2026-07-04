"""GitHub 开源动能 (signal ``alt.github_momentum``) — 公司级开发者心智遥测.

对每个带 ``github_orgs`` 的公司绑定,拉取该 org 最近被推送的仓库
(``GET /orgs/{org}/repos?sort=pushed&per_page=30``),聚合为一条周频信号:

  * ``value``  = 活跃仓库集合的 star 总量(高频校正引擎据此从周序列算动量);
  * ``meta``   = {repos, top_repo, releases_30d, orgs, open_issues}
  * ``period_end`` = 今天(weekly cadence),``unit`` = stars。

release 节奏取 star 最高的 top-3 仓库的真实 ``/releases``(近 30 天计数),
拿不到时回退到「近 30 天被 push 的仓库数」代理。

默认无 key(公开 API,未认证 60 req/h)。可选 ``GITHUB_TOKEN`` / ``GH_TOKEN``
经 ``os.environ`` 读取(值永不打印),作 Bearer 提升到 5000 req/h。所有 HTTP 走
``_fetch``:每主机 ``polite()`` 硬间隔(默认 2s)+ 每次运行的硬请求上限,
基篮全扫也不越 60/h 上限。逐 org / 逐条失败只记日志并跳过,绝不抛出。
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone

import httpx

from ...config import get_settings
from ...ingestion.base import polite
from ...ontology.altdata import SIGNALS_BY_KEY, bindings
from ...storage.altstore import upsert_signal
from ..base import log

_KEY = "alt.github_momentum"
_BASE = "https://api.github.com"
_HOST = "api.github.com"
# 未认证配额 60 req/h;硬性每次运行请求上限(篮子级保护),token 在时也是安全护栏。
_MAX_REQUESTS = 300
_REPOS_PER_ORG = 30
_TOP_FOR_RELEASES = 3
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def available() -> bool:
    return True  # 公开 API,无 key


# ── 认证头(token 只经 os.environ 读取,永不打印) ─────────────────────────────
def _headers() -> dict:
    s = get_settings()
    h = {
        "User-Agent": s.http_user_agent,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return h


class _Budget:
    """每次运行的请求预算:守住未认证的 60 req/h 硬上限。"""

    def __init__(self, cap: int = _MAX_REQUESTS) -> None:
        self.cap = cap
        self.count = 0

    def spend(self) -> bool:
        if self.count >= self.cap:
            return False
        self.count += 1
        return True


# ── HTTP 面(单次、无重试放大,精确计费;测试里被 monkeypatch) ─────────────────
def _fetch(path: str, params: dict, budget: _Budget):
    if not budget.spend():
        log.warning("github_metrics: per-run request cap %d reached, skipping %s",
                    budget.cap, path)
        return None
    polite(_HOST)  # settings.crawl_delay_seconds 默认 2s 的每主机硬间隔
    try:
        r = httpx.get(_BASE + path, params=params, headers=_headers(),
                      timeout=30, follow_redirects=True)
        if r.status_code in (403, 429) and r.headers.get("x-ratelimit-remaining") == "0":
            log.warning("github_metrics: rate limit exhausted (%s) on %s", r.status_code, path)
            return None
        r.raise_for_status()
        return r.json()
    except Exception as e:  # noqa: BLE001 — 永不打印 str(e)(可能含 token 头解析细节)
        status = getattr(getattr(e, "response", None), "status_code", "")
        log.warning("github_metrics GET %s failed: %s %s", path, type(e).__name__, status)
        return None


def _fetch_repos(org: str, budget: _Budget) -> list | None:
    data = _fetch(f"/orgs/{org}/repos",
                  {"sort": "pushed", "per_page": _REPOS_PER_ORG}, budget)
    return data if isinstance(data, list) else None


def _fetch_releases(full_name: str, budget: _Budget) -> list | None:
    data = _fetch(f"/repos/{full_name}/releases", {"per_page": 10}, budget)
    return data if isinstance(data, list) else None


# ── 纯函数(可离线测试) ────────────────────────────────────────────────────────
def _parse_dt(v) -> datetime | None:
    if not v:
        return None
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _stars(r: dict) -> int:
    try:
        return int(r.get("stargazers_count") or 0)
    except (TypeError, ValueError):
        return 0


def _top_repos(repos: list[dict], n: int) -> list[dict]:
    return sorted(repos, key=_stars, reverse=True)[:n]


def _aggregate(repos: list[dict], *, now: datetime | None = None) -> dict:
    """star 总量 / open-issue 总量 / top_repo / 近 30 天被 push 的仓库数(代理)。"""
    if not repos:
        return {"stars": 0, "open_issues": 0, "repos": 0, "top_repo": None, "pushed_30d": 0}
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=30)
    stars = sum(_stars(r) for r in repos)
    open_issues = 0
    for r in repos:
        try:
            open_issues += int(r.get("open_issues_count") or 0)
        except (TypeError, ValueError):
            pass
    pushed_30d = sum(1 for r in repos if (_parse_dt(r.get("pushed_at")) or _EPOCH) >= cutoff)
    top = _top_repos(repos, 1)[0]
    return {"stars": stars, "open_issues": open_issues, "repos": len(repos),
            "top_repo": top.get("full_name") or top.get("name"), "pushed_30d": pushed_30d}


def _count_releases_30d(releases: list[dict], *, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=30)
    n = 0
    for rel in releases or []:
        if rel.get("draft"):
            continue
        dt = _parse_dt(rel.get("published_at") or rel.get("created_at"))
        if dt and dt >= cutoff:
            n += 1
    return n


# ── 单公司采集(多 org 合并) ──────────────────────────────────────────────────
def _collect(orgs, budget: _Budget, *, now: datetime | None = None) -> dict | None:
    now = now or datetime.now(timezone.utc)
    repos: list[dict] = []
    for org in orgs:
        data = _fetch_repos(org, budget)
        if data:
            repos.extend(data)
    if not repos:
        return None
    agg = _aggregate(repos, now=now)
    # star 最高的 top-3 仓库取真实 release 计数;任一拿到就用真实值,全失败回退代理。
    releases, got = 0, False
    for r in _top_repos(repos, _TOP_FOR_RELEASES):
        full = r.get("full_name")
        if not full:
            continue
        rel = _fetch_releases(full, budget)
        if rel is None:
            continue
        got = True
        releases += _count_releases_30d(rel, now=now)
    agg["releases_30d"] = releases if got else agg["pushed_30d"]
    return agg


# ── 公共入口 ───────────────────────────────────────────────────────────────────
def pull(limit: int | None = None) -> dict:
    """扫描带 github_orgs 的公司绑定,写 alt.github_momentum(company-scope)。

    Returns stats: companies/orgs/written/requests/skipped/errors。
    """
    spec = SIGNALS_BY_KEY[_KEY]
    period_end = date.today()
    now = datetime.now(timezone.utc)
    budget = _Budget()
    stats = {"companies": 0, "orgs": 0, "written": 0,
             "requests": 0, "skipped": 0, "errors": 0}

    targets = [(cid, b.github_orgs) for cid, b in bindings().items() if b.github_orgs]
    if limit is not None:
        targets = targets[:limit]

    for cid, orgs in targets:
        stats["companies"] += 1
        stats["orgs"] += len(orgs)
        try:
            agg = _collect(orgs, budget, now=now)
        except Exception as e:  # noqa: BLE001 — 单公司失败不沉没整篮
            log.warning("github_metrics %s failed: %s", cid, type(e).__name__)
            stats["errors"] += 1
            continue
        if not agg or agg["repos"] == 0:
            stats["skipped"] += 1
            continue
        try:
            upsert_signal(
                spec.key, period_end=period_end, value=float(agg["stars"]),
                company_id=cid, unit=spec.unit, source=spec.source,
                meta={"repos": agg["repos"], "top_repo": agg["top_repo"],
                      "releases_30d": agg["releases_30d"], "orgs": list(orgs),
                      "open_issues": agg["open_issues"]})
            stats["written"] += 1
        except Exception as e:  # noqa: BLE001
            log.warning("github_metrics upsert %s failed: %s", cid, type(e).__name__)
            stats["errors"] += 1

    stats["requests"] = budget.count
    log.info("github_metrics: %s", stats)
    return stats
