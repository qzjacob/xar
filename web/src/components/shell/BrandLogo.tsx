import { Link } from "react-router-dom";
import { cn } from "../../lib/format";

/**
 * 全项目唯一品牌区(参考 X.com 的金属科技感):
 *  - iOS 风格圆角方块底(默认暗色系),内嵌金属银渐变的极简 X;
 *  - "XAR" 字标在方块右侧,与方块同高、同金属银;
 *  - "powered by AI from HKU.CDS" 在 XAR 右侧,右倾斜体,高度为 XAR 的 2/3,
 *    下底与 XAR 对齐。
 * 刻意零 per-module prop:进入任何模块都不变(设计约束,勿加变体)。
 */

// 与 h-12 顶栏匹配的品牌尺度:方块 28px = XAR 字高;powered-by = XAR 的 2/3,下底对齐。
const TILE = 28;
const XAR_PX = 28;
const POWERED_PX = Math.round((XAR_PX * 2) / 3); // ≈ 19px

export function BrandLogo({ to = "/", className }: { to?: string; className?: string }) {
  return (
    <Link to={to} className={cn("group flex select-none items-end gap-2", className)} title="XAR">
      {/* iOS 圆角方块(默认暗色系底) + 金属银 X */}
      <span
        className="flex shrink-0 items-center justify-center rounded-[7px] border border-line bg-gradient-to-b from-surface-2 to-canvas shadow-card transition-transform duration-200 group-hover:scale-105"
        style={{ width: TILE, height: TILE }}
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden>
          <defs>
            {/* 金属银:上亮下沉的冷银渐变 */}
            <linearGradient id="xar-silver" x1="4" y1="3" x2="20" y2="21" gradientUnits="userSpaceOnUse">
              <stop offset="0" stopColor="#F5F8FB" />
              <stop offset="0.5" stopColor="#C3CDD9" />
              <stop offset="1" stopColor="#8E9AAC" />
            </linearGradient>
          </defs>
          <path d="M4.5 3.5L19.5 20.5" stroke="url(#xar-silver)" strokeWidth="3.2" strokeLinecap="round" />
          <path d="M19.5 3.5L4.5 20.5" stroke="url(#xar-silver)" strokeWidth="3.2" strokeLinecap="round" />
        </svg>
      </span>
      {/* XAR — 与方块同高的金属银字标 */}
      <span
        className="bg-gradient-to-b from-[#F5F8FB] via-[#C3CDD9] to-[#8E9AAC] bg-clip-text font-extrabold tracking-tight text-transparent"
        style={{ fontSize: XAR_PX, lineHeight: `${TILE}px` }}
      >
        XAR
      </span>
      {/* powered by — XAR 右侧,右倾斜体,高度 = XAR 的 2/3,下底与 XAR 对齐(items-end) */}
      <span
        className="whitespace-nowrap pl-0.5 italic text-[#AEB9C8]/75"
        style={{ fontSize: POWERED_PX, lineHeight: `${POWERED_PX}px` }}
      >
        powered by AI from HKU.CDS
      </span>
    </Link>
  );
}
