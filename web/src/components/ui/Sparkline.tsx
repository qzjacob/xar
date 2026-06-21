import { cn } from "../../lib/format";

/** Dependency-free SVG sparkline. Colors up/down by default; pass `color` to override. */
export function Sparkline({
  data,
  width = 72,
  height = 22,
  className,
  color,
  fill = true,
}: {
  data: number[];
  width?: number;
  height?: number;
  className?: string;
  color?: string;
  fill?: boolean;
}) {
  if (!data || data.length < 2) return null;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const pad = 2;
  const x = (i: number) => (i / (data.length - 1)) * width;
  const y = (v: number) => pad + (1 - (v - min) / range) * (height - pad * 2);
  const line = data.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const up = data[data.length - 1] >= data[0];
  const stroke = color ?? (up ? "#16A34A" : "#DC2626");
  const areaId = `spark-${stroke.replace(/[^a-z0-9]/gi, "")}-${data.length}`;
  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className={cn("overflow-visible", className)}
      preserveAspectRatio="none"
      aria-hidden="true"
    >
      {fill && (
        <>
          <defs>
            <linearGradient id={areaId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={stroke} stopOpacity={0.18} />
              <stop offset="100%" stopColor={stroke} stopOpacity={0} />
            </linearGradient>
          </defs>
          <polygon points={`0,${height} ${line} ${width},${height}`} fill={`url(#${areaId})`} />
        </>
      )}
      <polyline
        points={line}
        fill="none"
        stroke={stroke}
        strokeWidth={1.5}
        strokeLinejoin="round"
        strokeLinecap="round"
      />
    </svg>
  );
}
