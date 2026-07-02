import { CandlestickChart } from "lucide-react";
import { useState } from "react";
import { ModuleNav } from "../../components/ModuleNav";
import { cn } from "../../lib/format";
import { Finder } from "./Finder";
import { MarketRead } from "./MarketRead";
import { OptionsDesk } from "./OptionsDesk";
import { QuoteDesk } from "./QuoteDesk";

const TABS = [
  { key: "desk", label: "Quotation Desk", cn: "报价台", C: QuoteDesk },
  { key: "read", label: "Market Read", cn: "市场解读", C: MarketRead },
  { key: "finder", label: "Underlying Finder", cn: "标的筛选", C: Finder },
  { key: "options", label: "Options Desk", cn: "期权台", C: OptionsDesk },
];

/** XAR Fenny — structured-notes / options desk (lazy-loaded; carries plotly in its chunk). */
export default function FennyApp() {
  const [tab, setTab] = useState("desk");
  const Active = TABS.find((t) => t.key === tab)?.C ?? QuoteDesk;
  return (
    <div className="flex h-full flex-col bg-canvas">
      <div className="flex h-14 shrink-0 items-center justify-between border-b border-line px-4">
        <div className="flex items-center gap-2.5">
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent text-white shadow-card">
            <CandlestickChart size={16} />
          </span>
          <div>
            <div className="text-sm font-bold leading-none text-brand-900">XAR Fenny</div>
            <div className="mt-0.5 text-2xs uppercase tracking-wide text-slate-500">结构化票据 · 期权分析</div>
          </div>
        </div>
        <ModuleNav variant="bar" />
      </div>
      <div className="flex shrink-0 gap-1 overflow-x-auto border-b border-line px-4">
        {TABS.map((t) => (
          <button key={t.key} type="button" onClick={() => setTab(t.key)}
            className={cn("shrink-0 border-b-2 px-3 py-2 text-xs font-semibold transition-colors",
              tab === t.key ? "border-accent-500 text-brand-900"
                : "border-transparent text-slate-400 hover:text-brand-900")}>
            {t.label} <span className="ml-1 text-[10px] text-slate-500">{t.cn}</span>
          </button>
        ))}
      </div>
      <div className="scroll-thin min-h-0 flex-1 overflow-y-auto">
        <Active />
      </div>
    </div>
  );
}
