import { useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { useData } from "../context";
import { RegimeSummaryCard } from "../components/RegimeSummaryCard";
import { MacroStrip } from "../components/MacroStrip";
import { ChainHeatmap } from "../components/ChainHeatmap";
import { SegmentRankingTable } from "../components/SegmentRankingTable";
import { SignalFeed } from "../components/SignalFeed";
import { CompanyWatchlist } from "../components/CompanyWatchlist";
import { CatalystCalendar } from "../components/CatalystCalendar";
import { DecisionRail } from "../components/DecisionRail";

/** Home dashboard. Every module click navigates to a detail page. */
export function DashboardPage() {
  const nav = useNavigate();
  const { overview, companies, signals, catalysts, market, theme } = useData();

  const marketCompanies = useMemo(
    () => (market === "ALL" ? companies : companies.filter((c) => c.market === market)),
    [companies, market],
  );
  const companyMarket = useMemo(
    () => Object.fromEntries(companies.map((c) => [c.id, c.market])),
    [companies],
  );
  const marketSignals = useMemo(
    () =>
      market === "ALL"
        ? signals
        : signals.filter((s) => !s.companyId || companyMarket[s.companyId] === market),
    [signals, market, companyMarket],
  );

  if (!overview) return null;
  const { regime, segments, decision } = overview;
  const goSegment = (id: string | null) => id && nav(`/genny/segment/${id}`);
  const goCompany = (id: string) => nav(`/genny/company/${id}`);

  return (
    <div className="mx-auto flex max-w-[1200px] flex-col gap-5">
      <RegimeSummaryCard regime={regime} segments={segments} />
      <MacroStrip theme={theme} />
      <ChainHeatmap segments={segments} selectedSegmentId={null} onSelectSegment={goSegment} />
      <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
        <div className="flex min-w-0 flex-col gap-5">
          <SegmentRankingTable segments={segments} selectedSegmentId={null} onSelectSegment={goSegment} />
          <CompanyWatchlist
            companies={marketCompanies}
            segments={segments}
            selectedSegmentId={null}
            market={market}
            onCompany={goCompany}
          />
        </div>
        <div className="flex min-w-0 flex-col gap-5">
          <SignalFeed
            signals={marketSignals}
            segments={segments}
            selectedSegmentId={null}
            onCompany={goCompany}
          />
          <CatalystCalendar catalysts={catalysts} selectedSegmentId={null} />
        </div>
      </div>
      <div className="xl:hidden">
        <DecisionRail decision={decision} segments={segments} onSelectSegment={goSegment} inline />
      </div>
    </div>
  );
}
