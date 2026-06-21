import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api } from "./lib/api";
import type { Catalyst, Company, Market, Overview, Period, Signal } from "./types";

interface DataState {
  overview: Overview | null;
  companies: Company[];
  signals: Signal[];
  catalysts: Catalyst[];
  loading: boolean;
  error: string | null;
  theme: string;
  setTheme: (t: string) => void;
  market: Market;
  setMarket: (m: Market) => void;
  period: Period;
  setPeriod: (p: Period) => void;
}

const Ctx = createContext<DataState | null>(null);

/** Loads the global dashboard payloads once and shares them across routes. */
export function DataProvider({ children }: { children: ReactNode }) {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [companies, setCompanies] = useState<Company[]>([]);
  const [signals, setSignals] = useState<Signal[]>([]);
  const [catalysts, setCatalysts] = useState<Catalyst[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [theme, setTheme] = useState<string>("ai_optical");
  const [market, setMarket] = useState<Market>("ALL");
  const [period, setPeriod] = useState<Period>("1M");

  useEffect(() => {
    let on = true;
    setLoading(true);
    setError(null);
    Promise.all([
      api.getOverview(theme),
      api.getCompanies(theme),
      api.getSignals(theme),
      api.getCatalysts(theme),
    ])
      .then(([o, c, s, ca]) => {
        if (!on) return;
        setOverview(o);
        setCompanies(c);
        setSignals(s);
        setCatalysts(ca);
        setLoading(false);
      })
      .catch((e) => {
        if (!on) return;
        setError(String(e));
        setLoading(false);
      });
    return () => {
      on = false;
    };
  }, [theme]);

  const value = useMemo<DataState>(
    () => ({ overview, companies, signals, catalysts, loading, error, theme, setTheme, market, setMarket, period, setPeriod }),
    [overview, companies, signals, catalysts, loading, error, theme, market, period],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useData(): DataState {
  const c = useContext(Ctx);
  if (!c) throw new Error("useData must be used within DataProvider");
  return c;
}
