import { ArrowUpRight, BookOpen, FileText, MessageSquare, TrendingUp } from "lucide-react";
import { useParams } from "react-router-dom";
import { Card } from "../../components/ui";
import { exploration } from "../../lib/exploration";
import { cn } from "../../lib/format";
import { OpsError, OpsLoading } from "../ops/_shared";
import { horizonLabel, maturityChip, MomentumBar, useAsync } from "./_shared";

export function ExplorationSectionPage() {
  const { sectionId = "ai" } = useParams();
  const { data, loading, error } = useAsync(() => exploration.section(sectionId), [sectionId]);

  if (loading) return <OpsLoading />;
  if (error) return <OpsError error={error} />;
  if (!data || !data.section) return <OpsError error="section not found" />;
  const s = data.section;

  return (
    <div className="mx-auto max-w-[1200px]">
      {/* section header */}
      <div className="mb-5">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <h1 className="text-xl font-semibold tracking-tight text-brand-900">{s.name}</h1>
          <span className="text-sm text-brand-500">{s.nameCn}</span>
          <span className="rounded bg-explore-50 px-1.5 py-0.5 text-2xs font-semibold uppercase text-explore-700 ring-1 ring-inset ring-explore/20">
            momentum {s.momentum}
          </span>
        </div>
        <p className="mt-2 max-w-3xl text-sm leading-snug text-brand-800">{s.headline}</p>
        <div className="mt-2 flex items-center gap-3 text-2xs text-brand-500">
          <span className="tnum">{s.frontCount} research fronts</span>
          <span className="tnum">{s.paperCount} preprints</span>
          <span className="tnum">{s.voiceCount} expert voices</span>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
        {/* research fronts (main column) */}
        <div className="lg:col-span-2">
          <div className="mb-2 flex items-center gap-1.5 text-2xs font-medium uppercase tracking-wide text-brand-500">
            <TrendingUp size={13} /> Research Fronts · 研究前沿
          </div>
          <div className="flex flex-col gap-3">
            {data.fronts.length === 0 && (
              <Card className="p-6 text-center text-sm text-brand-500">
                No fronts synthesized yet — hit Refresh to ingest + synthesize.
              </Card>
            )}
            {data.fronts.map((f) => (
              <Card key={f.id} className="p-4">
                <div className="flex items-start justify-between gap-3">
                  <h3 className="text-sm font-semibold text-brand-900">{f.title}</h3>
                  <div className="flex shrink-0 items-center gap-1.5">
                    <span className={cn("rounded px-1.5 py-0.5 text-2xs", maturityChip(f.maturity))}>
                      {f.maturity}
                    </span>
                    <span className="rounded bg-surface-2 px-1.5 py-0.5 text-2xs text-brand-200 ring-1 ring-inset ring-line">
                      {horizonLabel(f.horizon)}
                    </span>
                  </div>
                </div>

                <div className="mt-2">
                  <MomentumBar value={f.momentum} />
                </div>

                <p className="mt-3 text-xs leading-relaxed text-brand-500">{f.summary}</p>

                {f.direction && (
                  <div className="mt-3 rounded-lg bg-explore-50/70 px-3 py-2 ring-1 ring-inset ring-explore/15">
                    <div className="mb-0.5 text-2xs font-semibold uppercase tracking-wide text-explore-700">
                      Direction · 方向
                    </div>
                    <p className="text-xs leading-relaxed text-brand-800">{f.direction}</p>
                  </div>
                )}

                {f.significance && (
                  <p className="mt-2 text-xs leading-relaxed text-brand-200">
                    <span className="font-medium text-brand-500">Significance: </span>
                    {f.significance}
                  </p>
                )}

                {f.keyTerms.length > 0 && (
                  <div className="mt-3 flex flex-wrap gap-1">
                    {f.keyTerms.map((t) => (
                      <span key={t} className="rounded bg-surface-2 px-1.5 py-0.5 text-2xs text-brand-200">
                        {t}
                      </span>
                    ))}
                  </div>
                )}

                {f.papers.length > 0 && (
                  <div className="mt-3 border-t border-line pt-2">
                    <div className="mb-1 text-2xs uppercase tracking-wide text-brand-500">Grounded in</div>
                    <div className="flex flex-col gap-1">
                      {f.papers.map((p) => (
                        <a
                          key={p.arxivId}
                          href={p.url}
                          target="_blank"
                          rel="noreferrer"
                          className="group flex items-start gap-1.5 text-2xs text-brand-200 hover:text-explore-700"
                        >
                          <FileText size={12} className="mt-0.5 shrink-0 text-brand-700 group-hover:text-explore-500" />
                          <span className="truncate">
                            <span className="tnum text-brand-500">{p.arxivId}</span> · {p.title}
                          </span>
                        </a>
                      ))}
                    </div>
                  </div>
                )}
              </Card>
            ))}
          </div>
        </div>

        {/* sources rail (preprints + voices) */}
        <div className="flex flex-col gap-5">
          <div>
            <div className="mb-2 flex items-center gap-1.5 text-2xs font-medium uppercase tracking-wide text-brand-500">
              <FileText size={13} /> Latest Preprints · arXiv
            </div>
            <Card className="divide-y divide-line">
              {data.papers.slice(0, 12).map((p) => (
                <a
                  key={p.arxivId}
                  href={p.url}
                  target="_blank"
                  rel="noreferrer"
                  className="group flex items-start gap-2 px-3 py-2 transition hover:bg-canvas"
                >
                  <ArrowUpRight size={13} className="mt-0.5 shrink-0 text-brand-700 group-hover:text-explore-500" />
                  <div className="min-w-0">
                    <div className="line-clamp-2 text-2xs font-medium leading-snug text-brand-800">{p.title}</div>
                    <div className="mt-0.5 truncate text-2xs text-brand-500">
                      {(p.authors ?? []).slice(0, 2).join(", ")}
                      {(p.authors?.length ?? 0) > 2 ? " et al." : ""}
                    </div>
                  </div>
                </a>
              ))}
              {data.papers.length === 0 && (
                <div className="px-3 py-4 text-center text-2xs text-brand-700">no preprints ingested</div>
              )}
            </Card>
          </div>

          {data.articles && data.articles.length > 0 && (
            <div>
              <div className="mb-2 flex items-center gap-1.5 text-2xs font-medium uppercase tracking-wide text-brand-500">
                <BookOpen size={13} /> Curated Articles · 期刊/资讯
              </div>
              <Card className="divide-y divide-line">
                {data.articles.slice(0, 8).map((a, i) => (
                  <a
                    key={i}
                    href={a.url}
                    target="_blank"
                    rel="noreferrer"
                    className="group block px-3 py-2 transition hover:bg-canvas"
                  >
                    <div className="line-clamp-2 text-2xs font-medium leading-snug text-brand-800 group-hover:text-explore-700">
                      {a.title}
                    </div>
                    {a.summary && (
                      <div className="mt-0.5 line-clamp-2 text-2xs leading-snug text-brand-500">{a.summary}</div>
                    )}
                  </a>
                ))}
              </Card>
            </div>
          )}

          {data.voices.length > 0 && (
            <div>
              <div className="mb-2 flex items-center gap-1.5 text-2xs font-medium uppercase tracking-wide text-brand-500">
                <MessageSquare size={13} /> Expert Voices · X
              </div>
              <Card className="divide-y divide-line">
                {data.voices.slice(0, 8).map((v, i) => (
                  <a
                    key={i}
                    href={v.url}
                    target="_blank"
                    rel="noreferrer"
                    className="block px-3 py-2 transition hover:bg-canvas"
                  >
                    <div className="flex items-center gap-1.5">
                      <span className="text-2xs font-semibold text-explore-700">@{v.author ?? "?"}</span>
                      {v.expert && (
                        <span className="rounded bg-explore-50 px-1 py-0.5 text-2xs font-medium text-explore-700 ring-1 ring-inset ring-explore/20">
                          curated
                        </span>
                      )}
                    </div>
                    <div className="mt-0.5 line-clamp-3 text-2xs leading-snug text-brand-200">{v.text}</div>
                  </a>
                ))}
              </Card>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
