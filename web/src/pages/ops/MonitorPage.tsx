// 任务监控面板 —— 2026-07-29 全链路审计的产物。
//
// 审计当时的教训直接决定了这一页的版面:Dagster 队列死锁 7 天零执行、wechat/futu 静默哑火
// 数周,而**看板上什么都看不出来**。所以这里刻意把「上次尝试」与「上次产出」并列成两列 ——
// 只看前者就是当初那场 7 天无人察觉的成因(cadence 戳在源死透之后仍是绿的)。
import { useCallback, useMemo, useState } from "react";
import { Activity, AlertTriangle, BellOff, RefreshCw, Zap } from "lucide-react";
import { Badge, Card, MetricPill, SectionHeader } from "../../components/ui";
import { cn, relTime } from "../../lib/format";
import { ops } from "../../lib/ops";
import type { MonitorHistoryRow, MonitorInfo, MonitorState, MonitorTask } from "../../types-ops";
import { OpsContainer, OpsError, OpsHeader, OpsLoading, useAsync, usePolling } from "./_shared";

const POLL_MS = 30_000;

/** 监控态 → 既有 ops 配色语汇(_shared 的 statusChip 只认 ok/degraded/fail/…)。 */
const CHIP: Record<MonitorState, string> = {
  ok: "bg-pos-50 text-pos-700 ring-1 ring-inset ring-pos/20",
  stale: "bg-warn-50 text-warn-700 ring-1 ring-inset ring-warn/20",
  down: "bg-neg-50 text-neg-700 ring-1 ring-inset ring-neg/20",
  unknown: "bg-surface-2 text-brand-500 ring-1 ring-inset ring-line",
  unconfigured: "bg-surface-2 text-brand-500 ring-1 ring-inset ring-line",
};
const DOT: Record<MonitorState, string> = {
  ok: "bg-pos",
  stale: "bg-warn",
  down: "bg-neg",
  unknown: "bg-brand-500",
  unconfigured: "bg-brand-500",
};
const STATE_CN: Record<MonitorState, string> = {
  ok: "正常",
  stale: "滞后",
  down: "停摆",
  unknown: "无信号",
  unconfigured: "未配置",
};

function age(s: number | null | undefined): string {
  if (s == null) return "—";
  const v = Math.max(0, Math.round(s));
  if (v < 90) return `${v}s`;
  if (v < 5400) return `${Math.round(v / 60)}m`;
  if (v < 172800) return `${(v / 3600).toFixed(1)}h`;
  return `${(v / 86400).toFixed(1)}d`;
}

function StateChip({ state, muted }: { state: MonitorState; muted?: boolean }) {
  return (
    <span className={cn("inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-2xs font-medium", CHIP[state])}>
      <span className={cn("inline-block h-1.5 w-1.5 rounded-full", DOT[state],
        state === "down" && "animate-pulse")} />
      {STATE_CN[state]}
      {muted && <BellOff className="h-2.5 w-2.5 opacity-70" />}
    </span>
  );
}

/** SLA 对比:年龄 / 阈值。超阈标红,便于扫视。 */
function AgeVsSla({ ageS, slaS }: { ageS: number | null; slaS: number | null }) {
  if (ageS == null) return <span className="text-brand-500">—</span>;
  const over = slaS != null && ageS > slaS;
  return (
    <span className={cn("tnum", over ? "text-neg" : "text-brand-900")}>
      {age(ageS)}
      {slaS != null && <span className="text-brand-500"> / {age(slaS)}</span>}
    </span>
  );
}

function ActionButtons({ task, onDone }: { task: MonitorTask; onDone: () => void }) {
  const [busy, setBusy] = useState<string | null>(null);
  if (!task.actions.length) return null;
  const label = (a: string) =>
    a.startsWith("restart:") ? "重启" : a === "dagster:unstick" ? "清队列" : "立即拉取";
  const confirmText = (a: string) =>
    a.startsWith("restart:")
      ? `重启 ${a.slice(8)}?进程会在下一轮循环干净退出,由 docker 拉起。\n` +
        `若它已卡到进不了循环检查,这个按钮无效 —— 需人工 docker restart。`
      : a === "dagster:unstick"
        ? "终止全部在飞的 dagster run 以释放并发槽?已排队的陈旧 run 会一并作废。"
        : `清除该源的 cadence 戳,让 worker 下一轮立即拉取?`;
  return (
    <div className="flex flex-wrap gap-1">
      {task.actions.map((a) => (
        <button
          key={a}
          disabled={busy === a}
          title={confirmText(a)}
          onClick={async () => {
            if (!window.confirm(confirmText(a))) return;
            setBusy(a);
            try {
              await ops.monitorAction(a);
              onDone();
            } catch (e) {
              window.alert(String(e));
            } finally {
              setBusy(null);
            }
          }}
          className="rounded border border-line bg-surface-2 px-1.5 py-0.5 text-2xs text-brand-700
                     hover:border-accent/40 hover:text-brand-900 disabled:opacity-50"
        >
          {busy === a ? "…" : label(a)}
        </button>
      ))}
    </div>
  );
}

function WorkerCard({ task, onDone }: { task: MonitorTask; onDone: () => void }) {
  const d = task.detail || {};
  return (
    <Card className="p-3">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <StateChip state={task.state} muted={task.muted} />
            <span className="truncate text-xs font-medium text-brand-900">{task.labelCn}</span>
          </div>
          <div className="mt-0.5 truncate font-mono text-2xs text-brand-500">{task.id}</div>
        </div>
        <ActionButtons task={task} onDone={onDone} />
      </div>
      <div className="mt-2 space-y-1 text-2xs">
        <Row k="心跳 / SLA"><AgeVsSla ageS={task.hbAgeS} slaS={task.hbSlaS} /></Row>
        {task.yieldSlaS != null && (
          <Row k="产出 / SLA"><AgeVsSla ageS={task.yieldAgeS} slaS={task.yieldSlaS} /></Row>
        )}
        {task.observed !== task.state && (
          <Row k="待确认"><span className="text-warn">观测到 {STATE_CN[task.observed]},等下一轮确认</span></Row>
        )}
        {d.worstBy === "yield" && (
          <Row k="判定依据"><span className="text-neg">心跳绿但零产出</span></Row>
        )}
        {typeof d.inCycleSinceS === "number" && (
          <Row k="本轮已运行"><span className="tnum">{age(d.inCycleSinceS as number)}</span></Row>
        )}
        {typeof d.reason === "string" && <Row k="原因"><span className="text-brand-500">{d.reason}</span></Row>}
      </div>
      {task.note && <div className="mt-2 border-t border-line pt-1.5 text-2xs text-brand-500">{task.note}</div>}
    </Card>
  );
}

function Row({ k, children }: { k: string; children: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-2">
      <span className="text-brand-500">{k}</span>
      {children}
    </div>
  );
}

/** 双列表格:上次尝试 vs 上次产出 —— 这一页存在的全部理由。 */
function TaskTable({ tasks, onDone, minWidth = 760 }: {
  tasks: MonitorTask[]; onDone: () => void; minWidth?: number;
}) {
  return (
    <div className="scroll-thin overflow-x-auto">
      <table className="w-full border-collapse text-xs" style={{ minWidth }}>
        <thead>
          <tr className="border-b border-line text-2xs uppercase tracking-wide text-brand-200">
            <th className="px-2 py-1.5 text-left font-medium">任务</th>
            <th className="px-2 py-1.5 text-left font-medium">状态</th>
            <th className="px-2 py-1.5 text-right font-medium">上次尝试 / SLA</th>
            <th className="px-2 py-1.5 text-right font-medium">上次产出 / SLA</th>
            <th className="px-2 py-1.5 text-left font-medium">操作</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {tasks.map((t) => (
            <tr key={t.id} className="hover:bg-canvas">
              <td className="px-2 py-1.5">
                <div className="text-brand-900">{t.labelCn}</div>
                <div className="font-mono text-2xs text-brand-500">{t.id}</div>
              </td>
              <td className="px-2 py-1.5"><StateChip state={t.state} muted={t.muted} /></td>
              <td className="px-2 py-1.5 text-right"><AgeVsSla ageS={t.hbAgeS} slaS={t.hbSlaS} /></td>
              <td className="px-2 py-1.5 text-right">
                {t.yieldSlaS == null
                  ? <span className="text-2xs text-brand-500">不可度量</span>
                  : <AgeVsSla ageS={t.yieldAgeS} slaS={t.yieldSlaS} />}
              </td>
              <td className="px-2 py-1.5"><ActionButtons task={t} onDone={onDone} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** 时间线:纯 div 拼色带,零图表依赖。把跃迁行按任务分组连成区间。 */
function Timeline({ rows, hours }: { rows: MonitorHistoryRow[]; hours: number }) {
  const byTask = useMemo(() => {
    const m = new Map<string, MonitorHistoryRow[]>();
    for (const r of rows) {
      if (!m.has(r.task_id)) m.set(r.task_id, []);
      m.get(r.task_id)!.push(r);
    }
    // 只画曾经不正常的任务 —— 全绿的任务画出来是一条无信息的绿条。
    const out: [string, MonitorHistoryRow[]][] = [];
    for (const [k, v] of m) {
      const asc = [...v].reverse();
      if (asc.some((r) => r.state !== "ok" && r.state !== "unconfigured")) out.push([k, asc]);
    }
    return out.sort((a, b) => a[0].localeCompare(b[0]));
  }, [rows]);

  const now = Date.now();
  const span = hours * 3600_000;
  if (!byTask.length) {
    return <div className="px-3 py-6 text-center text-2xs text-brand-500">
      近 {hours}h 内没有异常状态记录。
    </div>;
  }
  return (
    <div className="space-y-1.5 p-3">
      {byTask.map(([task, evs]) => (
        <div key={task} className="flex items-center gap-2">
          <span className="w-44 shrink-0 truncate font-mono text-2xs text-brand-500" title={task}>{task}</span>
          <div className="relative h-3 flex-1 overflow-hidden rounded bg-surface-2">
            {evs.map((e, i) => {
              const start = new Date(e.at).getTime();
              const end = i + 1 < evs.length ? new Date(evs[i + 1].at).getTime() : now;
              const left = Math.max(0, ((start - (now - span)) / span) * 100);
              const width = Math.max(0.4, ((end - start) / span) * 100);
              if (left > 100) return null;
              return (
                <span
                  key={i}
                  title={`${e.state} @ ${new Date(e.at).toLocaleString()}`}
                  className={cn("absolute top-0 h-full", DOT[e.state])}
                  style={{ left: `${left}%`, width: `${Math.min(width, 100 - left)}%` }}
                />
              );
            })}
          </div>
        </div>
      ))}
      <div className="pt-1 text-2xs text-brand-500">
        左 = {hours}h 前,右 = 现在。绿=正常 黄=滞后 红=停摆 灰=无信号/未配置。
      </div>
    </div>
  );
}

export function MonitorPage() {
  const { data, loading, error, reload } = useAsync<MonitorInfo>(() => ops.monitor(), []);
  const hist = useAsync(() => ops.monitorHistory({ hours: 168 }), []);
  const refreshAll = useCallback(() => { reload(); hist.reload(); }, [reload, hist]);
  usePolling(reload, POLL_MS);

  if (loading && !data) return <OpsLoading />;
  if (error) return <OpsError error={error} />;
  if (!data) return <OpsError error="no data" />;

  const s = data.summary || {};
  const groups = (g: MonitorTask["group"]) => data.tasks.filter((t) => t.group === g);
  const monBeat = data.monitor?.lastSweepAt;
  const monStale = !monBeat || Date.now() - new Date(monBeat).getTime() > 10 * 60_000;

  return (
    <OpsContainer>
      <OpsHeader
        title="Monitor"
        titleCn="任务监控"
        icon={<Activity className="h-4 w-4" />}
        subtitle={
          <>全部常驻/间歇任务的实时状态、停摆检测与历史。「上次尝试」与「上次产出」分列 ——
          只看前者就是 2026-07-22 那次 Dagster 停摆 7 天无人察觉的成因。</>
        }
        right={
          <>
            <span className={cn("inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-2xs",
              monStale ? CHIP.down : CHIP.ok)}>
              <span className={cn("inline-block h-1.5 w-1.5 rounded-full", monStale ? DOT.down : DOT.ok)} />
              巡检 {monBeat ? relTime(monBeat) : "未运行"}
              {data.monitor?.sweepMs != null && <span className="text-brand-500"> · {data.monitor.sweepMs}ms</span>}
            </span>
            <button
              onClick={refreshAll}
              className="inline-flex items-center gap-1 rounded border border-line bg-surface px-2 py-1
                         text-2xs text-brand-700 hover:border-accent/40 hover:text-brand-900"
            >
              <RefreshCw className={cn("h-3 w-3", loading && "animate-spin")} /> 刷新
            </button>
          </>
        }
      />

      {data.monitor?.telegram !== "ok" && (
        <Card className="mb-4 border-warn/30 p-3">
          <div className="flex items-start gap-2">
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warn" />
            <div className="text-2xs">
              <div className="font-medium text-brand-900">
                停摆报警尚未接通手机 —— 目前只有页内告警。
              </div>
              <div className="mt-0.5 text-brand-500">
                {data.monitor.telegram === "no_token"
                  ? "缺 BOT_HTTP_API(Telegram bot token)。"
                  : <>缺推送目标:在 .env 设 <code className="text-brand-700">XAR_MONITOR_TELEGRAM_CHAT</code>
                    {data.knownChats?.length
                      ? <> —— 库里已知的 chat id:{data.knownChats.map((c) => (
                          <code key={c} className="ml-1 text-brand-700">{c}</code>))}</>
                      : "(库里还没有已知的 telegram chat)"}</>}
              </div>
            </div>
          </div>
        </Card>
      )}

      <div className="mb-4 grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
        <MetricPill label="正常" value={s.ok ?? 0} />
        <MetricPill label="滞后" value={s.stale ?? 0} />
        <MetricPill label="停摆" value={s.down ?? 0} />
        <MetricPill label="无信号" value={s.unknown ?? 0} />
        <MetricPill label="未配置" value={s.unconfigured ?? 0} />
        <MetricPill label="未解决告警" value={data.monitor?.openAlerts ?? 0} />
      </div>

      {!!data.alerts?.length && (
        <Card className="mb-4 overflow-hidden">
          <SectionHeader title="Open alerts" titleCn="未解决告警"
            icon={<AlertTriangle className="h-3.5 w-3.5" />} />
          <div className="divide-y divide-line">
            {data.alerts.map((a) => (
              <div key={a.id} className="flex items-center justify-between gap-3 px-3 py-2">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <Badge className={a.severity === "critical" ? CHIP.down : CHIP.stale}>
                      {a.severity === "critical" ? "critical" : "warn"}
                    </Badge>
                    <span className="truncate text-xs text-brand-900">{a.title}</span>
                  </div>
                  <div className="mt-0.5 text-2xs text-brand-500">
                    开始 {relTime(a.opened_at)}
                    {a.state === "acked" && " · 已确认(不再提醒)"}
                    {a.last_notified_at && ` · 上次推送 ${relTime(a.last_notified_at)}`}
                  </div>
                </div>
                <div className="flex shrink-0 gap-1">
                  {a.state === "open" && (
                    <button
                      onClick={async () => { await ops.monitorAck(a.id); refreshAll(); }}
                      className="rounded border border-line bg-surface-2 px-1.5 py-0.5 text-2xs
                                 text-brand-700 hover:border-accent/40"
                    >确认</button>
                  )}
                  <button
                    onClick={async () => { await ops.monitorResolveAlert(a.id); refreshAll(); }}
                    className="rounded border border-line bg-surface-2 px-1.5 py-0.5 text-2xs
                               text-brand-700 hover:border-accent/40"
                  >关闭</button>
                </div>
              </div>
            ))}
          </div>
        </Card>
      )}

      <Card className="mb-4 overflow-hidden">
        <SectionHeader title="Resident workers" titleCn="常驻工人"
          icon={<Zap className="h-3.5 w-3.5" />} />
        <div className="grid gap-2 p-3 sm:grid-cols-2 lg:grid-cols-3">
          {[...groups("workers"), ...groups("platform")].map((t) => (
            <WorkerCard key={t.id} task={t} onDone={refreshAll} />
          ))}
        </div>
      </Card>

      <Card className="mb-4 overflow-hidden">
        <SectionHeader title="Dagster & macro" titleCn="Dagster 调度 + 宏观连接器"
          right={<span className="text-2xs text-brand-500">只认 runs.status,不采信 job_ticks</span>} />
        <TaskTable tasks={[...groups("dagster"), ...groups("slx")]} onDone={refreshAll} minWidth={720} />
      </Card>

      <Card className="mb-4 overflow-hidden">
        <SectionHeader title="Pull sources" titleCn="拉取源(13)"
          right={<span className="text-2xs text-brand-500">「尝试」绿而「产出」红 = 静默哑火</span>} />
        <TaskTable tasks={groups("fetchy")} onDone={refreshAll} />
      </Card>

      <Card className="overflow-hidden">
        <SectionHeader title="Timeline (7d)" titleCn="状态时间线(7 天)"
          right={<span className="text-2xs text-brand-500">
            {hist.data?.rows?.length ?? 0} 条记录
          </span>} />
        {hist.loading && !hist.data
          ? <div className="px-3 py-6 text-center text-2xs text-brand-500">Loading…</div>
          : <Timeline rows={hist.data?.rows ?? []} hours={168} />}
      </Card>
    </OpsContainer>
  );
}
