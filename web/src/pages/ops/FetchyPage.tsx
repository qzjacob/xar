import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Check, Power, RefreshCw, Satellite } from "lucide-react";
import { ops } from "../../lib/ops";
import type { FetchyConfig, FetchyInfo } from "../../types-ops";
import { cn, relTime } from "../../lib/format";
import { OpsContainer, OpsHeader } from "./_shared";

/**
 * Fetchy — glmworker 管理面:总开关 / 抽取 LLM 选择 / 数据源勾选 / 阶段勾选。
 * 配置写入共享 DB(glm_worker_state),工人下一轮读取生效 —— 无需重启容器。
 */
export function FetchyPage() {
  const [info, setInfo] = useState<FetchyInfo | null>(null);
  const [cfg, setCfg] = useState<FetchyConfig | null>(null);
  const [saving, setSaving] = useState(false);
  const [savedAt, setSavedAt] = useState<number | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const dirtyRef = useRef(false);

  const load = useCallback(async () => {
    try {
      const r = await ops.fetchy();
      setInfo(r);
      // 刷新只更新状态卡/目录;有未保存改动时**保留编辑**,不悄悄用服务端覆盖
      setCfg((prev) => (prev && dirtyRef.current ? prev : r.config));
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);

  const dirty = useMemo(
    () => !!info && !!cfg && JSON.stringify(cfg) !== JSON.stringify(info.config),
    [info, cfg],
  );
  useEffect(() => {
    dirtyRef.current = dirty;
  }, [dirty]);

  async function save() {
    if (!cfg) return;
    setSaving(true);
    setErr(null);
    try {
      await ops.setFetchy(cfg);
      // 保存后整体重取:生效模型链/状态卡随新配置即时更新,不等手动刷新
      const fresh = await ops.fetchy();
      setInfo(fresh);
      setCfg(fresh.config);
      setSavedAt(Date.now());
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  async function reviewAccount(ghId: string, action: "approve" | "block" | "pending") {
    try {
      await ops.wechatReview(ghId, action);
      await load(); // 审批即时生效,重取刷新队列/计数
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }

  async function promoteAccount(ghId: string, action: "approve" | "reject" | "reset") {
    try {
      await ops.wechatPromote(ghId, action);
      await load(); // 晋升审批即时生效,下轮订阅进 werss 名册
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }

  if (!info || !cfg) {
    return (
      <OpsContainer>
        <OpsHeader title="Fetchy" titleCn="抓取工人管理" icon={<Satellite size={18} strokeWidth={2} />} />
        <p className="text-xs text-brand-500">{err ? `加载失败: ${err}` : "Loading…"}</p>
      </OpsContainer>
    );
  }

  const st = info.status;
  const quota = st.quota?.status ?? "unknown";
  const catalog = info.modelCatalog ?? [];
  const wd = info.wechatDiscover;

  return (
    <OpsContainer>
      <OpsHeader
        title="Fetchy"
        titleCn="抓取工人管理"
        icon={<Satellite size={18} strokeWidth={2} />}
        subtitle={`glmworker 常驻抓取/抽取工人 · 配置下一轮生效(约 ${"<"}10 分钟),无需重启`}
        right={
          <div className="flex items-center gap-2">
            <button type="button" onClick={() => void load()}
              className="inline-flex items-center gap-1.5 rounded-lg border border-line bg-surface px-2.5 py-1.5 text-xs text-brand-500 hover:bg-canvas">
              <RefreshCw size={13} /> 刷新
            </button>
            <button type="button" onClick={() => void save()} disabled={!dirty || saving}
              className={cn(
                "inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold text-white",
                dirty ? "bg-accent-600 hover:brightness-110" : "cursor-not-allowed bg-surface-2 text-brand-200",
              )}>
              <Check size={13} />
              {saving ? "保存中…" : dirty ? "保存生效" : savedAt ? "已保存" : "无改动"}
            </button>
          </div>
        }
      />
      {err && <p className="mb-3 text-xs text-neg">{err}</p>}

      {/* ── 工人状态 ── */}
      <div className="mb-5 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label="工人心跳" value={st.counters.last_cycle_at ? relTime(st.counters.last_cycle_at) : "—"}
          hint={`累计 ${st.counters.cycles ?? 0} 轮`} />
        <Stat label="LLM 额度" value={quota === "ok" ? "正常" : quota === "exhausted" ? "耗尽(等窗口)" : "未知(等首轮)"}
          tone={quota === "ok" ? "pos" : quota === "exhausted" ? "warn" : undefined} />
        <Stat label="待抽取积压" value={st.backlog_docs != null ? `${st.backlog_docs} 篇` : "—"} />
        <Stat label="生效模型链" value={st.pin?.[0] ?? "—"} hint={st.pin?.slice(1).join(" → ") || undefined} />
      </div>

      {/* ── 总开关 + LLM 选择 ── */}
      <div className="mb-5 grid grid-cols-1 gap-3 lg:grid-cols-2">
        <div className="rounded-xl border border-line bg-surface p-4">
          <div className="mb-2 flex items-center gap-2 text-sm font-semibold text-brand-900">
            <Power size={15} className={cfg.enabled ? "text-pos" : "text-neg"} /> 工人总开关
          </div>
          <label className="flex cursor-pointer items-center gap-3">
            <input type="checkbox" checked={cfg.enabled}
              onChange={(e) => setCfg({ ...cfg, enabled: e.target.checked })}
              className="h-4 w-4 accent-accent-500" />
            <span className="text-xs text-brand-500">
              {cfg.enabled
                ? "运行中:按下方勾选执行拉取/抽取"
                : "已暂停:工人只保留心跳,所有拉取与 LLM 消耗停止"}
            </span>
          </label>
        </div>
        <div className="rounded-xl border border-line bg-surface p-4">
          <div className="mb-2 text-sm font-semibold text-brand-900">抽取 LLM(钉扎链首)</div>
          <select value={cfg.model} onChange={(e) => setCfg({ ...cfg, model: e.target.value })}
            className="w-full rounded-lg border border-line bg-surface-2 px-2 py-1.5 text-xs text-brand-900">
            {info.models.map((m) => (
              <option key={m.id} value={m.id}>
                {m.id} · {m.billing === "subscription" ? "订阅(零边际)" : "按量计费"}
                {m.preferred ? " ★" : ""}
              </option>
            ))}
          </select>
          <p className="mt-1.5 text-2xs text-brand-200">
            工人是常驻批量任务 —— 建议选订阅制模型;按量模型会持续产生费用。其余钉扎链保留为回退。
          </p>
        </div>
      </div>

      {/* ── 数据源勾选 ── */}
      <div className="mb-5 rounded-xl border border-line bg-surface p-4">
        <div className="mb-3 text-sm font-semibold text-brand-900">
          数据源 <span className="ml-1 text-2xs font-normal text-brand-500">勾掉即停拉(节拍保留,重开即恢复)</span>
        </div>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {info.sources.map((s) => (
            <label key={s.key}
              className={cn(
                "flex cursor-pointer items-start gap-2.5 rounded-lg border px-3 py-2",
                cfg.sources[s.key] ? "border-accent/30 bg-accent/5" : "border-line bg-surface-2 opacity-70",
              )}>
              <input type="checkbox" checked={cfg.sources[s.key] ?? true}
                onChange={(e) => setCfg({ ...cfg, sources: { ...cfg.sources, [s.key]: e.target.checked } })}
                className="mt-0.5 h-4 w-4 accent-accent-500" />
              <span className="min-w-0">
                <span className="block text-xs font-medium text-brand-900">{s.label}</span>
                <span className="block text-2xs text-brand-200">
                  {s.hours != null ? `每 ${s.hours}h` : "按配置节拍"}
                  {s.last ? ` · 上次 ${relTime(s.last)}` : " · 未运行过"}
                </span>
              </span>
            </label>
          ))}
        </div>
      </div>

      {/* ── 阶段勾选 ── */}
      <div className="rounded-xl border border-line bg-surface p-4">
        <div className="mb-3 text-sm font-semibold text-brand-900">
          处理阶段 <span className="ml-1 text-2xs font-normal text-brand-500">细分功能开关(LLM 消耗集中在"语义抽取")</span>
        </div>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {info.stages.map((s) => (
            <label key={s.key}
              className={cn(
                "flex cursor-pointer items-start gap-2.5 rounded-lg border px-3 py-2",
                cfg.stages[s.key] ? "border-accent/30 bg-accent/5" : "border-line bg-surface-2 opacity-70",
              )}>
              <input type="checkbox" checked={cfg.stages[s.key] ?? true}
                onChange={(e) => setCfg({ ...cfg, stages: { ...cfg.stages, [s.key]: e.target.checked } })}
                className="mt-0.5 h-4 w-4 accent-accent-500" />
              <span className="min-w-0">
                <span className="block text-xs font-medium text-brand-900">{s.key}</span>
                <span className="block text-2xs text-brand-200">{s.label}</span>
              </span>
            </label>
          ))}
        </div>
      </div>

      {/* ── 全模型目录(动态路由)── */}
      <div className="mb-5 rounded-xl border border-line bg-surface p-4">
        <div className="mb-1 flex flex-wrap items-center justify-between gap-1">
          <div className="text-sm font-semibold text-brand-900">
            全模型目录
            <span className="ml-1 text-2xs font-normal text-brand-500">
              claude/glm/kimi/deepseek/minimax/local · 共 {catalog.length}
            </span>
          </div>
          {info.routing && (
            <span className="text-2xs text-brand-200">
              动态路由 {info.routing.dynamic ? "开" : "关"} · 复杂度阈值 {info.routing.charsLow}/{info.routing.charsHigh} 字
            </span>
          )}
        </div>
        <p className="mb-2 text-2xs text-brand-200">
          按复杂度×相关性动态选层:简单/批量→便宜/本地,复杂/高价值→强云(订阅优先,成本有界)。灰=缺 key 或 PREVIEW(不可用)。
        </p>
        <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2 lg:grid-cols-3">
          {catalog.map((m) => (
            <div key={m.id} className={cn("rounded-lg border border-line bg-surface-2 px-2.5 py-1.5",
              m.usable ? "" : "opacity-50")}>
              <div className="flex items-center justify-between gap-1">
                <span className="truncate text-xs font-medium text-brand-900">{m.id}{m.preferred ? " ★" : ""}</span>
                <span className="shrink-0 text-2xs text-brand-200">{m.provider}</span>
              </div>
              <div className="mt-0.5 flex flex-wrap items-center gap-1 text-2xs text-brand-500">
                <span className={cn("rounded px-1", m.billing === "subscription" ? "bg-pos/10 text-pos" : "bg-warn-100/10 text-warn-100")}>
                  {m.billing === "subscription" ? "订阅" : "按量"}
                </span>
                {m.tier && <span className="rounded bg-accent/10 px-1 text-accent">{m.tier}</span>}
                {m.status && m.status !== "active" && <span className="rounded bg-brand-200/10 px-1">{m.status}</span>}
                {!m.usable && m.reason && <span className="truncate text-neg" title={m.reason}>· {m.reason}</span>}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* ── 微信全网发现(进化 + human-in-the-loop 门控)── */}
      {wd && (
        <div className="mb-5 rounded-xl border border-line bg-surface p-4">
          <div className="mb-1 flex flex-wrap items-center justify-between gap-1">
            <div className="text-sm font-semibold text-brand-900">
              微信全网发现
              <span className="ml-1 text-2xs font-normal text-brand-500">
                {wd.enabled ? (wd.wcdaConfigured ? "运行中" : "已开·后端未接") : "已关"}
              </span>
            </div>
            <span className="text-2xs text-brand-200">
              发现文档 {wd.discoveredDocs ?? 0} · 门控 {wd.hitlGate ? "严格(只抓批准)" : "轻量(拉黑差号)"}
            </span>
          </div>
          <div className="mb-2 flex flex-wrap gap-x-3 gap-y-0.5 text-2xs text-brand-200">
            <span>晋升漏斗:发现 {wd.funnel?.discovered ?? 0} · 已订阅 {wd.funnel?.promoted ?? 0}
              {" "}· HITL待批 {wd.funnel?.hitl_queued ?? 0} · 够格 {wd.funnel?.eligible_pending ?? 0}</span>
            {(wd.strata ?? []).map((t) => (
              <span key={t.track}>
                {t.track === "discover" ? "WCDA发现流" : t.track === "subscribed" ? "werss订阅流" : "其它"}
                {" "}keep {t.triaged > 0 ? Math.round((t.kept / t.triaged) * 100) + "%" : "—"}
              </span>
            ))}
          </div>
          {wd.evolve?.winners && wd.evolve.winners.length > 0 && (
            <div className="mb-2">
              <div className="mb-1 text-2xs uppercase tracking-wide text-brand-200">进化赛马 · 高命中率查询</div>
              <div className="flex flex-wrap gap-1">
                {wd.evolve.winners.slice(0, 8).map((w) => (
                  <span key={w.query} className="rounded bg-pos/10 px-1.5 py-0.5 text-2xs text-pos">
                    {w.query} {w.keep_rate != null ? `${Math.round(w.keep_rate * 100)}%` : ""}
                  </span>
                ))}
              </div>
            </div>
          )}
          <div className="mb-1 flex items-center gap-2 text-2xs text-brand-200">
            <span className="uppercase tracking-wide">审核队列</span>
            <span>待审 {wd.review?.pending ?? 0} · 批准 {wd.review?.approved ?? 0} · 拉黑 {wd.review?.blocked ?? 0}</span>
          </div>
          <div className="max-h-64 space-y-1 overflow-y-auto">
            {(wd.reviewQueue ?? []).map((a) => (
              <div key={a.gh_id}
                className="flex items-center justify-between gap-2 rounded-lg border border-line bg-surface-2 px-2.5 py-1.5">
                <span className="min-w-0">
                  <span className="block truncate text-xs font-medium text-brand-900">{a.name || a.gh_id}</span>
                  <span className="text-2xs text-brand-200">
                    {a.articles_seen > 0
                      ? `${a.articles_seen} 篇 · keep ${a.keep_rate != null ? Math.round(a.keep_rate * 100) + "%" : "—"}`
                      : "待抓取"}
                  </span>
                </span>
                <span className="flex shrink-0 gap-1">
                  <button type="button" onClick={() => void reviewAccount(a.gh_id, "approve")}
                    className="rounded bg-pos/15 px-2 py-1 text-2xs font-medium text-pos hover:bg-pos/25">批准</button>
                  <button type="button" onClick={() => void reviewAccount(a.gh_id, "block")}
                    className="rounded bg-neg/15 px-2 py-1 text-2xs font-medium text-neg hover:bg-neg/25">拉黑</button>
                </span>
              </div>
            ))}
            {(wd.reviewQueue ?? []).length === 0 && (
              <p className="text-2xs text-brand-200">审核队列为空(无 pending 号)。</p>
            )}
          </div>
          {(wd.promoteQueue ?? []).length > 0 && (
            <div className="mt-2 border-t border-line pt-2">
              <div className="mb-1 flex items-center gap-2 text-2xs text-brand-200">
                <span className="uppercase tracking-wide">晋升待批</span>
                <span>边缘信噪号(0.5–0.7),批准后订阅进 werss 优质名册</span>
              </div>
              <div className="max-h-64 space-y-1 overflow-y-auto">
                {(wd.promoteQueue ?? []).map((a) => (
                  <div key={a.gh_id}
                    className="flex items-center justify-between gap-2 rounded-lg border border-line bg-surface-2 px-2.5 py-1.5">
                    <span className="min-w-0">
                      <span className="block truncate text-xs font-medium text-brand-900">{a.name || a.gh_id}</span>
                      <span className="text-2xs text-brand-200">
                        {a.articles_seen} 篇 · keep {a.keep_rate != null ? Math.round(a.keep_rate * 100) + "%" : "—"}
                      </span>
                    </span>
                    <span className="flex shrink-0 gap-1">
                      <button type="button" onClick={() => void promoteAccount(a.gh_id, "approve")}
                        className="rounded bg-pos/15 px-2 py-1 text-2xs font-medium text-pos hover:bg-pos/25">批准订阅</button>
                      <button type="button" onClick={() => void promoteAccount(a.gh_id, "reject")}
                        className="rounded bg-neg/15 px-2 py-1 text-2xs font-medium text-neg hover:bg-neg/25">拒绝</button>
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </OpsContainer>
  );
}

function Stat({ label, value, hint, tone }: { label: string; value: string; hint?: string; tone?: "pos" | "warn" }) {
  return (
    <div className="rounded-xl border border-line bg-surface px-3 py-2.5">
      <div className="text-2xs uppercase tracking-wide text-brand-200">{label}</div>
      <div className={cn("mt-0.5 text-sm font-semibold tnum",
        tone === "pos" ? "text-pos" : tone === "warn" ? "text-warn-100" : "text-brand-900")}>
        {value}
      </div>
      {hint && <div className="text-2xs text-brand-200">{hint}</div>}
    </div>
  );
}
