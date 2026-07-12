import { useMemo } from "react";
import { Link } from "react-router-dom";
import { BrickWall, ShieldQuestion } from "lucide-react";
import { andy } from "../../lib/andy";
import { Badge, Card } from "../../components/ui";
import { useAndy } from "../../components/andy/AndyLayout";
import { AnchorChip, HardnessBadge, sourceGradeLabel } from "../../components/andy/constants";
import { AndyContainer, AndyError, AndyHeader, AndyLoading, useAsync } from "./_shared";

/** /andy/walls — 承重墙: the deliberately-unquantifiable boundary conditions
 * ("why AI cannot move this") + the 合法性代理 proxy section (代理 ≠ 概念本身). */
export function AndyWallsPage() {
  const { withAsOf } = useAndy();
  const metricsQ = useAsync(() => andy.metrics(), []);

  const walls = useMemo(
    () => (metricsQ.data?.metrics ?? []).filter((m) => m.hardness === "wall"),
    [metricsQ.data],
  );
  const proxies = useMemo(
    () => (metricsQ.data?.metrics ?? []).filter((m) => m.metric_key.startsWith("proxy.legitimacy.")),
    [metricsQ.data],
  );

  if (metricsQ.loading) return <AndyLoading label="Loading walls…" />;
  if (metricsQ.error) return <AndyError error={metricsQ.error} />;

  return (
    <AndyContainer>
      <AndyHeader
        icon={<BrickWall size={18} />}
        title="Load-bearing Walls"
        titleCn="承重墙"
        subtitle="AI 动不了的定性边界 — 不可量化 BY DESIGN；每面墙给出机制与「赌注」（若此墙倒塌，理论证伪）。"
        right={
          <Badge className="bg-surface-2 text-brand-500 ring-1 ring-inset ring-line">
            <span className="tnum">{walls.length}</span> walls
          </Badge>
        }
      />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {walls.map((m) => (
          <Card key={m.metric_key} className="flex flex-col p-4">
            <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
              <h2 className="text-sm font-semibold text-brand-900">{m.display_name_zh}</h2>
              <span className="font-mono text-2xs text-brand-200">{m.metric_key}</span>
            </div>
            <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
              <HardnessBadge hardness={m.hardness} withEn={false} />
              {m.theory_anchor.map((a) => (
                <AnchorChip key={a} anchor={a} />
              ))}
            </div>

            <div className="mt-3 flex flex-col gap-2.5 text-xs leading-relaxed">
              <div>
                <div className="text-2xs uppercase tracking-wide text-brand-200">为什么 AI 动不了它 · Mechanism</div>
                <p className="mt-0.5 text-brand-800">{m.mechanism ?? "—"}</p>
              </div>
              {m.falsification_condition && (
                <div className="rounded-lg border border-andy/25 bg-andy-50/60 px-2.5 py-2">
                  <div className="text-2xs font-semibold uppercase tracking-wide text-andy-500">
                    赌注 · 若倒塌则证伪 Falsification
                  </div>
                  <p className="mt-0.5 text-brand-800">{m.falsification_condition}</p>
                </div>
              )}
              {m.caveat && (
                <div>
                  <div className="text-2xs uppercase tracking-wide text-brand-200">Caveat</div>
                  <p className="mt-0.5 text-brand-500">{m.caveat}</p>
                </div>
              )}
            </div>

            <div className="mt-auto pt-3">
              <div className="rounded-lg border border-line bg-surface-2/70 px-2.5 py-1.5 text-center text-2xs font-medium text-brand-500">
                🧱 不可量化 · value 恒为 NULL（point-in-time 视图无读数，设计如此）
              </div>
            </div>
          </Card>
        ))}
        {walls.length === 0 && (
          <Card className="col-span-full px-6 py-12 text-center text-sm text-brand-200">
            无承重墙登记 · no wall metrics registered
          </Card>
        )}
      </div>

      {/* 合法性代理 proxies */}
      <div className="mt-6">
        <AndyHeader
          icon={<ShieldQuestion size={18} />}
          title="Legitimacy Proxies"
          titleCn="合法性代理"
          subtitle="proxy.legitimacy.* — 对不可量化之墙的可观测代理。"
        />
        <div className="mb-3 flex items-start gap-2 rounded-lg border border-dashed border-warn/50 bg-warn-50 px-3 py-2 text-warn-700">
          <span className="text-xs font-semibold">代理 ≠ 概念本身</span>
          <span className="text-2xs leading-relaxed opacity-80">
            这些指标只是「合法性」这面承重墙的可观测投影 — 代理变动不等于墙体位移，严禁把代理读数当作墙本身的量化。
          </span>
        </div>
        {proxies.length === 0 ? (
          <Card className="px-6 py-8 text-center text-xs text-brand-200">
            暂无合法性代理登记 · no proxy.legitimacy.* metrics
          </Card>
        ) : (
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-3">
            {proxies.map((m) => (
              <Link
                key={m.metric_key}
                to={withAsOf(`/andy/metrics/${encodeURIComponent(m.metric_key)}`)}
                className="group rounded-xl border border-line bg-surface p-3 shadow-card transition-colors hover:border-andy/40"
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate text-xs font-semibold text-brand-900 transition-colors group-hover:text-andy-500">
                    {m.display_name_zh}
                  </span>
                  <HardnessBadge hardness={m.hardness} withEn={false} />
                </div>
                <div className="mt-0.5 truncate font-mono text-2xs text-brand-200">{m.metric_key}</div>
                <div className="mt-1.5 flex items-center gap-2 text-2xs text-brand-500">
                  <span>{sourceGradeLabel(m.source_grade)}</span>
                  <span>·</span>
                  <span>{m.unit ?? "—"}</span>
                </div>
              </Link>
            ))}
          </div>
        )}
      </div>
    </AndyContainer>
  );
}
