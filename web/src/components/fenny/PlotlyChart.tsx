import Plotly from "plotly.js-dist-min";
import createPlotlyComponent from "react-plotly.js/factory";

// Wrapped via the factory over the minified dist so the whole Fenny route (lazy-loaded)
// carries plotly in ONE chunk — it never touches the main bundle.
const Plot = createPlotlyComponent(Plotly);

/** Dark plotly template matching the terminal theme tokens. */
const DARK_LAYOUT: Record<string, unknown> = {
  paper_bgcolor: "rgba(0,0,0,0)",
  plot_bgcolor: "rgba(0,0,0,0)",
  font: { color: "#9caabe", family: "Inter, sans-serif", size: 11 },
  margin: { l: 44, r: 16, t: 24, b: 36 },
  xaxis: { gridcolor: "rgba(52,68,94,0.5)", zerolinecolor: "rgba(52,68,94,0.8)" },
  yaxis: { gridcolor: "rgba(52,68,94,0.5)", zerolinecolor: "rgba(52,68,94,0.8)" },
  legend: { bgcolor: "rgba(0,0,0,0)", font: { size: 10 } },
  colorway: ["#f59e0b", "#2dc876", "#f46060", "#7c9cf5", "#a78bfa"],
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
