import Plotly from "plotly.js-dist-min";
import createPlotlyComponent from "react-plotly.js/factory";

// Wrapped via the factory over the minified dist. Both lazy routes that draw charts
// (Fenny AND Andy) import THIS specifier, so Vite splits plotly into ONE shared chunk
// loaded on demand — it never touches the main bundle and is never duplicated.
const Plot = createPlotlyComponent(Plotly);

/**
 * 图表色的单一事实源 — Plotly/SVG 读不了 CSS 变量,所以 hex 集中在这里,
 * 与 theme.css 的 token 手工对齐(accent=靛蓝;pos/neg 为涨跌语义色)。
 */
export const CHART = {
  accent: "#818cf8",  // = --c-accent-500 (indigo)
  pos: "#2dc876",     // = --c-pos
  neg: "#f46060",     // = --c-neg
  cool1: "#7c9cf5",   // periwinkle (series 4)
  cool2: "#38bdf8",   // sky (series 5)
  muted: "#9caabe",   // = --c-brand-500
  grid: "rgba(52,68,94,0.5)",
} as const;

/** Dark plotly template matching the terminal theme tokens. */
const DARK_LAYOUT: Record<string, unknown> = {
  paper_bgcolor: "rgba(0,0,0,0)",
  plot_bgcolor: "rgba(0,0,0,0)",
  font: { color: CHART.muted, family: "Inter, sans-serif", size: 11 },
  margin: { l: 44, r: 16, t: 24, b: 36 },
  xaxis: { gridcolor: CHART.grid, zerolinecolor: "rgba(52,68,94,0.8)" },
  yaxis: { gridcolor: CHART.grid, zerolinecolor: "rgba(52,68,94,0.8)" },
  legend: { bgcolor: "rgba(0,0,0,0)", font: { size: 10 } },
  colorway: [CHART.accent, CHART.pos, CHART.neg, CHART.cool1, CHART.cool2],
};

export function PlotlyChart({ data, layout, height = 300 }: {
  data: unknown[];
  layout?: Record<string, unknown>;
  height?: number;
}) {
  return (
    <Plot
      data={data}
      layout={{ ...DARK_LAYOUT, ...layout, autosize: true, height }}
      config={{ displayModeBar: false, responsive: true }}
      style={{ width: "100%", height }}
      useResizeHandler
    />
  );
}
