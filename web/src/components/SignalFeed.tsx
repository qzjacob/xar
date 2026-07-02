import { useMemo } from "react";
import { Radio } from "lucide-react";
import {
  catalystLabel,
  SOURCE_LABEL,
  type Segment,
  type Signal,
} from "../types";
import { cn, polarityChip, polarityHex, relTime } from "../lib/format";
import { Badge, Card, SectionHeader } from "./ui";

/**
 * Live catalyst tape: the most recent typed signals across the chain, filtered
 * to the selected segment when one is active. Each row is polarity-coded with a
 * catalyst-type chip, an ingestion-source tag, relative time, and a compact meta
 * line (ticker · segment · magnitude · model confidence).
 */
export function SignalFeed({
  signals,
  segments,
  selectedSegmentId,
  onCompany,
}: {
  signals: Signal[];
  segments: Segment[];
  selectedSegmentId: string | null;
  /** Navigate to the originating company's detail page. */
  onCompany?: (id: string) => void;
}) {
  const segmentName = useMemo(() => {
    const m = new Map<string, string>();
    for (const s of segments) m.set(s.id, s.name);
    return m;
  }, [segments]);

  const visible = useMemo(() => {
    const filtered = selectedSegmentId
      ? signals.filter((s) => s.segmentId === selectedSegmentId)
      : signals;
    return [...filtered].sort((a, b) => b.ts.localeCompare(a.ts));
  }, [signals, selectedSegmentId]);

  return (
    <Card>
      <SectionHeader
        title="Key Signals"
        titleCn="关键信号"
        icon={<Radio size={15} strokeWidth={2} />}
        right={
          <Badge className="bg-slate-100 text-slate-600 ring-1 ring-inset ring-slate-200">
            {visible.length}
          </Badge>
        }
      />

      {visible.length === 0 ? (
        <div className="px-4 py-10 text-center text-sm text-slate-400">
          No signals for this segment.
        </div>
      ) : (
        <ul className="scroll-thin max-h-[460px] divide-y divide-line overflow-y-auto">
          {visible.map((sig) => (
            <SignalRow
              key={sig.id}
              sig={sig}
              segmentName={segmentName.get(sig.segmentId)}
              selectedSegmentId={selectedSegmentId}
              onCompany={onCompany}
            />
          ))}
        </ul>
      )}
    </Card>
  );
}

function SignalRow({
  sig,
  segmentName,
  selectedSegmentId,
  onCompany,
}: {
  sig: Signal;
  segmentName?: string;
  selectedSegmentId: string | null;
  onCompany?: (id: string) => void;
}) {
  const accent = polarityHex(sig.polarity);
  const meta: string[] = [];
  if (sig.ticker) meta.push(sig.ticker);
  if (segmentName && (!selectedSegmentId || sig.segmentId !== selectedSegmentId))
    meta.push(segmentName);
  if (sig.magnitude) meta.push(sig.magnitude);

  const clickable = Boolean(onCompany && sig.companyId);
  const go = () => clickable && onCompany!(sig.companyId!);

  return (
    <li
      onClick={clickable ? go : undefined}
      onKeyDown={
        clickable
          ? (e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                go();
              }
            }
          : undefined
      }
      role={clickable ? "button" : undefined}
      tabIndex={clickable ? 0 : undefined}
      aria-label={clickable ? `Open ${sig.ticker ?? "company"}` : undefined}
      className={cn(
        "relative flex gap-3 py-2.5 pl-4 pr-3 transition-colors hover:bg-canvas",
        clickable && "cursor-pointer focus-visible:bg-canvas",
      )}
    >
      {/* polarity accent bar */}
      <span
        className="mt-0.5 w-0.5 shrink-0 self-stretch rounded-full"
        style={{ backgroundColor: accent }}
        aria-hidden="true"
      />

      <div className="min-w-0 flex-1">
        {/* top line: catalyst chip · source · time */}
        <div className="flex items-center gap-1.5">
          <Badge className={polarityChip(sig.polarity)}>
            <span
              className="h-1.5 w-1.5 rounded-full"
              style={{ backgroundColor: accent }}
              aria-hidden="true"
            />
            {catalystLabel(sig.type).en}
          </Badge>
          <Badge
            className="bg-slate-100 text-slate-500 ring-1 ring-inset ring-slate-200"
            title={`Source · ${SOURCE_LABEL[sig.source]}`}
          >
            {SOURCE_LABEL[sig.source]}
          </Badge>
          <span className="tnum ml-auto shrink-0 whitespace-nowrap text-2xs text-slate-400">
            {relTime(sig.ts)}
          </span>
        </div>

        {/* title */}
        <p className="mt-1 line-clamp-2 text-sm leading-snug text-brand-900">{sig.title}</p>

        {/* meta line */}
        <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-2xs text-slate-400">
          {meta.map((part, i) => (
            <span key={i} className="flex items-center gap-1">
              {i > 0 && <span className="text-slate-300" aria-hidden="true">·</span>}
              <span
                className={cn(
                  i === 0 && sig.ticker && "tnum font-medium text-slate-500",
                )}
              >
                {part}
              </span>
            </span>
          ))}
          {meta.length > 0 && <span className="text-slate-300" aria-hidden="true">·</span>}
          <span className="tnum inline-flex items-center gap-1">
            <span
              className="h-1.5 w-1.5 rounded-full bg-slate-300"
              style={{ opacity: 0.35 + sig.confidence * 0.65 }}
              aria-hidden="true"
            />
            conf {Math.round(sig.confidence * 100)}%
          </span>
        </div>
      </div>
    </li>
  );
}
