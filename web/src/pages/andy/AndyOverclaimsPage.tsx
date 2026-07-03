import { useState } from "react";
import { Gavel, Loader2, RefreshCw, Scale } from "lucide-react";
import { andy } from "../../lib/andy";
import { cn } from "../../lib/format";
import { Badge, Card } from "../../components/ui";
import { useAndy } from "../../components/andy/AndyLayout";
import {
  HardnessBadge,
  MetricKeyChip,
  SoftWatermark,
  VerdictLamp,
  verdictMeta,
  WindowCountdown,
} from "../../components/andy/constants";
import { AndyContainer, AndyError, AndyHeader, AndyLoading, useAsync } from "./_shared";
import type { AndyClaim } from "../../types-andy";

/** /andy/overclaims — 过度宣称登记簿: live monitoring of falsifiable claims.
 * Verdicts are point-in-time observations, never causal conclusions. */
export function AndyOverclaimsPage() {
  const { asOf, withAsOf } = useAndy();
  const claimsQ = useAsync(() => andy.overclaims(5), []);
  const [evaluating, setEvaluating] = useState(false);
  const [evalNote, setEvalNote] = useState<string | null>(null);

  const runEvaluate = () => {
    setEvaluating(true);
    setEvalNote(null);
    andy
      .evaluate(asOf)
      .then((r) => {
        setEvalNote(`已判定 ${r.evaluated} 条 · as-of ${r.as_of}`);
        claimsQ.reload();
      })
      .catch((e) => setEvalNote(`判定失败：${String(e)}`))
      .finally(() => setEvaluating(false));
  };

  if (claimsQ.loading && !claimsQ.data) return <AndyLoading label="Loading registry…" />;
  if (claimsQ.error && !claimsQ.data) return <AndyError error={claimsQ.error} />;

  const claims = claimsQ.data?.claims ?? [];

  return (
    <AndyContainer>
      <AndyHeader
        icon={<Scale size={18} />}
        title="Overclaim Registry"
        titleCn="过度宣称登记簿"
        subtitle={claimsQ.data?.disclaimer}
        right={
          <div className="flex items-center gap-2">
            {evalNote && <span className="tnum text-2xs text-slate-400">{evalNote}</span>}
            <button
              type="button"
              onClick={runEvaluate}
              disabled={evaluating}
              className={cn(
                "inline-flex items-center gap-1.5 rounded-lg border border-andy/40 bg-andy-50 px-2.5 py-1.5 text-xs font-medium text-andy-500",
                "transition-colors hover:bg-andy-100",
                evaluating && "cursor-not-allowed opacity-60",
              )}
              title={`POST /overclaims/evaluate?as_of=${asOf}`}
            >
              {evaluating ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} strokeWidth={2.5} />}
              重新判定 Evaluate
            </button>
          </div>
        }
      />

      <div className="flex flex-col gap-4">
        {claims.map((c) => (
          <ClaimCard key={c.claim_key} claim={c} asOf={asOf} withAsOf={withAsOf} />
        ))}
        {claims.length === 0 && (
          <Card className="px-6 py-12 text-center text-sm text-slate-500">
            登记簿为空 · no claims registered
          </Card>
        )}
      </div>
    </AndyContainer>
  );
}

function ClaimCard({ claim: c, asOf, withAsOf }: {
  claim: AndyClaim;
  asOf: string;
  withAsOf: (p: string) => string;
}) {
  const v = verdictMeta(c.status);
  return (
    <Card className="p-4">
      {/* verdict row */}
      <div className="flex flex-wrap items-center gap-2">
        <VerdictLamp status={c.status} size={12} />
        <Badge className={v.chip}>{c.status}</Badge>
        {c.needs_identification && (
          <span className="rounded border border-dashed border-warn/50 bg-warn-50 px-1.5 py-0.5 text-2xs font-medium text-warn-700">
            未识别 · 勿作因果
          </span>
        )}
        <span className="ml-auto">
          <WindowCountdown windowStart={c.window_start} decisionWindow={c.decision_window} asOf={asOf} />
        </span>
      </div>

      {/* claim text */}
      <p className="mt-2 text-base font-medium leading-relaxed text-brand-900">{c.claim_text_zh}</p>

      {/* meta row */}
      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-2xs text-slate-400">
        <span className="font-mono text-slate-500">{c.claim_key}</span>
        <span>owner · {c.owner ?? "—"}</span>
        <HardnessBadge hardness={c.hardness} withEn={false} />
        <span className="tnum">
          窗口 {c.window_start} + {c.decision_window}
        </span>
        {c.last_evaluated && <span className="tnum">上次判定 {c.last_evaluated.slice(0, 16).replace("T", " ")}</span>}
      </div>

      {c.hardness === "soft" && (
        <SoftWatermark compact watermark={c.identification.watermark} className="mt-2" />
      )}

      {/* rules (collapsible) */}
      <details className="group mt-3">
        <summary className="flex cursor-pointer select-none items-center gap-1.5 text-2xs font-medium uppercase tracking-wide text-slate-500 transition-colors hover:text-brand-900">
          <Gavel size={12} strokeWidth={2.25} />
          判定规则 Fixation / Falsify rules
          <span className="text-slate-600 group-open:hidden">▸</span>
          <span className="hidden text-slate-600 group-open:inline">▾</span>
        </summary>
        <div className="mt-2 grid gap-2 md:grid-cols-2">
          <div className="rounded-lg border border-neg/25 bg-neg-50/60 p-2.5">
            <div className="text-2xs font-semibold uppercase tracking-wide text-neg-700">固化规则 fixation_rule</div>
            <pre className="scroll-thin mt-1 overflow-x-auto whitespace-pre-wrap font-mono text-2xs leading-relaxed text-brand-800">{c.fixation_rule}</pre>
          </div>
          <div className="rounded-lg border border-pos/25 bg-pos-50/60 p-2.5">
            <div className="text-2xs font-semibold uppercase tracking-wide text-pos-700">证伪规则 falsify_rule</div>
            <pre className="scroll-thin mt-1 overflow-x-auto whitespace-pre-wrap font-mono text-2xs leading-relaxed text-brand-800">{c.falsify_rule}</pre>
          </div>
        </div>
        {c.verdict_note && (
          <p className="mt-2 rounded-lg bg-surface-2 px-2.5 py-1.5 text-2xs leading-relaxed text-slate-400">
            {c.verdict_note}
          </p>
        )}
      </details>

      {/* evidence: eval log + snapshot */}
      {(c.recent_eval_log?.length ?? 0) > 0 && (
        <div className="mt-3">
          <div className="mb-1 text-2xs font-medium uppercase tracking-wide text-slate-500">
            判定留痕 Recent evaluations
          </div>
          <div className="scroll-thin overflow-x-auto rounded-lg border border-line">
            <table className="w-full min-w-[520px] border-collapse text-2xs">
              <thead>
                <tr className="border-b border-line text-left uppercase tracking-wide text-slate-500">
                  <th className="px-2.5 py-1.5 font-medium">evaluated_at</th>
                  <th className="px-2.5 py-1.5 font-medium">as_of</th>
                  <th className="px-2.5 py-1.5 font-medium">verdict</th>
                  <th className="px-2.5 py-1.5 font-medium">triggered</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {(c.recent_eval_log ?? []).map((e, i) => (
                  <tr key={i} className="tnum">
                    <td className="whitespace-nowrap px-2.5 py-1.5 font-mono text-slate-400">
                      {e.evaluated_at ? e.evaluated_at.slice(0, 19).replace("T", " ") : "—"}
                    </td>
                    <td className="whitespace-nowrap px-2.5 py-1.5 font-mono text-slate-400">{e.as_of_date}</td>
                    <td className="whitespace-nowrap px-2.5 py-1.5">
                      <VerdictLamp status={e.verdict} size={8} />
                    </td>
                    <td className={cn("whitespace-nowrap px-2.5 py-1.5 font-semibold", e.triggered ? "text-neg-700" : "text-slate-500")}>
                      {e.triggered ? "true" : "false"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {c.evidence_snapshot != null && (
        <details className="group mt-2">
          <summary className="cursor-pointer select-none text-2xs font-medium uppercase tracking-wide text-slate-500 transition-colors hover:text-brand-900">
            证据快照 evidence_snapshot <span className="text-slate-600 group-open:hidden">▸</span>
            <span className="hidden text-slate-600 group-open:inline">▾</span>
          </summary>
          <pre className="scroll-thin mt-1.5 max-h-64 overflow-auto rounded-lg bg-surface-2 p-2.5 font-mono text-2xs leading-relaxed text-brand-700">
            {JSON.stringify(c.evidence_snapshot, null, 2)}
          </pre>
        </details>
      )}

      {/* related metrics */}
      {c.related_metrics.length > 0 && (
        <div className="mt-3 flex flex-wrap items-center gap-1.5">
          <span className="text-2xs uppercase tracking-wide text-slate-500">关联指标</span>
          {c.related_metrics.map((mk) => (
            <MetricKeyChip key={mk} metricKey={mk} to={withAsOf(`/andy/metrics/${encodeURIComponent(mk)}`)} />
          ))}
        </div>
      )}
    </Card>
  );
}
