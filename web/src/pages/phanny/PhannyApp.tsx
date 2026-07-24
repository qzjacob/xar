import { useEffect, useState } from "react";
import { BarChart3, Gauge } from "lucide-react";
import { Navigate, Outlet, Route, Routes } from "react-router-dom";
import { ModuleShell } from "../../components/shell/ModuleShell";
import { SidebarFrame } from "../../components/shell/SidebarFrame";
import { SidebarNav, type SideNavItem } from "../../components/shell/SidebarNav";
import { phannyApi } from "../../lib/phanny";
import type { PhannyCalBucket, PhannyPortfolio } from "../../types-phanny";

const NAV: SideNavItem[] = [
  { to: "/phanny", label: "Portfolio", cn: "多空组合", icon: BarChart3, exact: true },
  { to: "/phanny/calibration", label: "Calibration", cn: "回看校准", icon: Gauge },
];

function PhannyLayout() {
  return (
    <ModuleShell
      sidebar={
        <SidebarFrame title="Phanny" titleCn="季报交易" badge="Book">
          <SidebarNav heading="Views · 视图" items={NAV} />
        </SidebarFrame>
      }
    >
      <Outlet />
    </ModuleShell>
  );
}

function StatTile({ label, value, good }: { label: string; value: string; good?: boolean }) {
  return (
    <div className="rounded-lg border border-white/10 bg-white/5 px-3 py-2">
      <div className="text-[10px] uppercase tracking-wide text-brand-500">{label}</div>
      <div className={`text-sm font-semibold ${good === undefined ? "" : good ? "text-emerald-400" : "text-amber-400"}`}>
        {value}
      </div>
    </div>
  );
}

/** conviction 1-10 直方图(纯 CSS 条,不引 plotly)。 */
function Histogram({ hist }: { hist: Record<string, number> }) {
  const buckets = Array.from({ length: 10 }, (_, i) => String(i + 1));
  const max = Math.max(1, ...buckets.map((b) => hist[b] ?? 0));
  return (
    <div className="flex h-28 items-end gap-1.5">
      {buckets.map((b) => {
        const n = hist[b] ?? 0;
        return (
          <div key={b} className="flex flex-1 flex-col items-center justify-end gap-1">
            <div className="text-[9px] text-brand-500">{n || ""}</div>
            <div
              className="w-full rounded-t bg-emerald-500/70"
              style={{ height: `${(n / max) * 88}%` }}
              title={`conviction ${b}: ${n}`}
            />
            <div className="text-[9px] text-brand-500">{b}</div>
          </div>
        );
      })}
    </div>
  );
}

function PortfolioPage() {
  const [pf, setPf] = useState<PhannyPortfolio | null>(null);
  const [err, setErr] = useState<string>("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    phannyApi.portfolio().then(setPf).catch((e) => setErr(String(e)));
  }, []);

  const build = async () => {
    setBusy(true);
    try {
      await phannyApi.buildBook(true);
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  };

  if (err) return <div className="p-6 text-sm text-rose-400">加载失败:{err}</div>;
  if (!pf) return <div className="p-6 text-sm text-brand-500">Loading…</div>;
  const d = pf.distribution;

  return (
    <div className="flex flex-col gap-4 overflow-auto p-6">
      <div className="flex items-center justify-between">
        <h2 className="text-base font-semibold">季报多空组合 · Phanny Book</h2>
        <button
          onClick={build}
          disabled={busy}
          className="rounded-md border border-emerald-500/40 bg-emerald-500/10 px-3 py-1.5 text-xs text-emerald-300 hover:bg-emerald-500/20 disabled:opacity-50"
        >
          {busy ? "已排队…" : "跑整本 book"}
        </button>
      </div>

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-6">
        <StatTile label="names" value={String(d.n)} />
        <StatTile label="normal?" value={d.ok ? "✓ 正态" : "✗ 未达标"} good={d.ok} />
        <StatTile label="mean" value={d.mean != null ? d.mean.toFixed(2) : "—"} />
        <StatTile label="std" value={d.std != null ? d.std.toFixed(2) : "—"} />
        <StatTile label="high≥7" value={d.high_ratio != null ? `${(d.high_ratio * 100).toFixed(0)}%` : "—"} />
        <StatTile label="shapiro p" value={d.shapiro_p != null ? d.shapiro_p.toFixed(3) : "—"} />
      </div>
      {!d.ok && <div className="text-xs text-amber-400">正态门:{d.reason}</div>}

      <div className="rounded-lg border border-white/10 bg-white/5 p-4">
        <div className="mb-2 text-xs uppercase tracking-wide text-brand-500">Conviction 分布(1-10)</div>
        <Histogram hist={pf.histogram} />
      </div>

      <div className="overflow-x-auto rounded-lg border border-white/10">
        <table className="w-full text-left text-xs">
          <thead className="bg-white/5 text-brand-500">
            <tr>
              {["公司", "财报日", "方向", "conviction", "size%", "状态"].map((h) => (
                <th key={h} className="px-3 py-2 font-medium">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {pf.trades.map((t) => (
              <tr key={`${t.company_id}-${t.event_date}`} className="border-t border-white/5">
                <td className="px-3 py-1.5 font-mono">{t.company_id}</td>
                <td className="px-3 py-1.5">{t.event_date}</td>
                <td
                  className={`px-3 py-1.5 font-semibold ${
                    t.direction === "long" ? "text-emerald-400" : t.direction === "short" ? "text-rose-400" : "text-brand-500"
                  }`}
                >
                  {t.direction ?? "—"}
                </td>
                <td className="px-3 py-1.5">{t.conviction != null ? t.conviction.toFixed(1) : "—"}</td>
                <td className="px-3 py-1.5">{t.size_pct != null ? `${t.size_pct}%` : "—"}</td>
                <td className="px-3 py-1.5 text-brand-500">{t.ensemble_status ?? (t.direction ? "" : "未生成")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function CalibrationPage() {
  const [cal, setCal] = useState<Record<string, PhannyCalBucket> | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    phannyApi.calibration().then(setCal).catch((e) => setErr(String(e)));
  }, []);
  if (err) return <div className="p-6 text-sm text-rose-400">加载失败:{err}</div>;
  if (!cal) return <div className="p-6 text-sm text-brand-500">Loading…</div>;
  return (
    <div className="p-6">
      <h2 className="mb-3 text-base font-semibold">回看校准 · 按 conviction 分桶命中率</h2>
      <div className="overflow-x-auto rounded-lg border border-white/10">
        <table className="w-full text-left text-xs">
          <thead className="bg-white/5 text-brand-500">
            <tr>
              {["桶", "n", "已判", "命中率", "平均反应%"].map((h) => (
                <th key={h} className="px-3 py-2 font-medium">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {Object.entries(cal).map(([bucket, b]) => (
              <tr key={bucket} className="border-t border-white/5">
                <td className="px-3 py-1.5 font-mono">{bucket}</td>
                <td className="px-3 py-1.5">{b.n}</td>
                <td className="px-3 py-1.5">{b.decided}</td>
                <td className="px-3 py-1.5">{b.hit_rate != null ? `${(b.hit_rate * 100).toFixed(0)}%` : "—"}</td>
                <td className="px-3 py-1.5">{b.avg_reaction_pct != null ? b.avg_reaction_pct.toFixed(2) : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** XAR Phanny — 季报多空事件交易 book(lazy-loaded)。 */
export default function PhannyApp() {
  return (
    <Routes>
      <Route element={<PhannyLayout />}>
        <Route index element={<PortfolioPage />} />
        <Route path="calibration" element={<CalibrationPage />} />
        <Route path="*" element={<Navigate to="/phanny" replace />} />
      </Route>
    </Routes>
  );
}
