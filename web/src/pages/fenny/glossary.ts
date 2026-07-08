// Plain-language glossary for the Fenny structured-note desk (FN-2).
// Goal: a relationship manager or a non-options client should understand every field
// without an options background. Each term carries a plain bilingual label + a one-line
// tooltip that explains it in everyday words (no greeks, no jargon). Referenced by the
// QuoteDesk / MarketRead InfoDot tooltips and section labels.

export interface Term {
  /** short English label shown on the control */
  label: string;
  /** Chinese label shown beneath / beside it */
  cn: string;
  /** one-line plain-language explanation (bilingual), shown in the ⓘ tooltip */
  tip: string;
}

export const FENNY_TERMS: Record<string, Term> = {
  // ── product / structure inputs ─────────────────────────────────────────────
  variant: {
    label: "Product type", cn: "产品类型",
    tip: "选择票据的结构。FCN 每期收固定票息;Phoenix 有派息条件线;Snowball 雪球逐步下移收回线。The note structure you want.",
  },
  fcn: {
    label: "FCN · fixed coupon", cn: "FCN · 固定派息",
    tip: "每期收固定票息;只要不触及保护线,到期拿回本金。Pays a fixed coupon each period; principal back at maturity unless the protection level is breached.",
  },
  phoenix: {
    label: "Phoenix", cn: "凤凰",
    tip: "只有当标的高于「派息线」时才派息(可带记忆补发)。Pays a coupon only when the stock is above the coupon level (optionally with memory).",
  },
  snowball: {
    label: "Snowball", cn: "雪球",
    tip: "收回线随时间逐步下移,越容易提前收回。An auto-call level that steps down over time, making early redemption easier.",
  },
  underlyings: {
    label: "Linked stock(s)", cn: "挂钩标的",
    tip: "票据挂钩的股票。选 2–3 只时,由「表现最差的一只」决定你的结果(worst-of)。With 2–3 names, the WORST performer decides your outcome.",
  },
  ticker: { label: "Ticker", cn: "代码", tip: "股票代码,如 AAPL。The stock symbol." },
  spot: { label: "Price now", cn: "现价", tip: "该股票当前价格。The current share price." },
  volatility: {
    label: "Volatility", cn: "波动率",
    tip: "股价平时的波动幅度。波动越大 → 票息越高,但触及保护线的机会也越大。Bigger swings → higher coupon but more risk.",
  },
  amount: {
    label: "Amount", cn: "投资金额",
    tip: "你投资的名义本金。The face amount you invest.",
  },
  tradeDate: { label: "Trade date", cn: "交易日", tip: "成交日期。The day the trade is agreed." },
  strikeDate: {
    label: "Pricing date", cn: "定价日",
    tip: "设定初始参考价的日子;所有涨跌都相对这天。The day the reference price is set; all levels are measured against it.",
  },
  maturity: {
    label: "Maturity", cn: "到期日",
    tip: "若未提前收回,票据到期结算的日子。When the note settles if it hasn't auto-called earlier.",
  },
  protection: {
    label: "Protection level", cn: "本金保护线",
    tip: "到期时最差股票只要不跌破此水平(相对定价日),本金 100% 拿回;跌破了才按跌幅承受损失。越低越安全。At maturity, principal is fully returned unless the worst stock closes below this level. Lower = safer.",
  },
  autocall: {
    label: "Early-exit level", cn: "提前收回线",
    tip: "观察日若股价 ≥ 此水平,票据提前到期,你拿回本金 + 已累计票息。If the stock is at/above this on an observation date, the note redeems early with principal + coupons.",
  },
  couponBarrier: {
    label: "Coupon level", cn: "派息线",
    tip: "只有当股价高于此水平时才派发当期票息(Phoenix)。The coupon is paid only when the stock is above this level.",
  },
  memory: {
    label: "Memory coupon", cn: "记忆派息",
    tip: "开启后,之前被跳过的票息会在下次达标时一次补发。Missed coupons are paid later once the level is met again.",
  },
  targetCoupon: {
    label: "Target coupon", cn: "目标票息",
    tip: "年化票息目标(% p.a.)。「解出票息」会算出当前条款下公平的票息。Annual coupon you target; “Solve” finds the fair coupon for these terms.",
  },
  riskFree: {
    label: "Risk-free rate", cn: "无风险利率",
    tip: "定价用的市场利率;一般用默认即可。The market rate used to discount cash flows; the default is usually fine.",
  },
  correlation: {
    label: "Correlation", cn: "相关性",
    tip: "多只标的一起涨跌的紧密程度(0–1)。越低,worst-of 的风险越大。How tightly the stocks move together; lower means the worst-of is riskier.",
  },

  // ── outputs ────────────────────────────────────────────────────────────────
  coupon: {
    label: "Coupon", cn: "票息",
    tip: "你每年可收到的利息(年化)。The annual interest you receive.",
  },
  fairValue: {
    label: "Fair value", cn: "公平价值",
    tip: "模型算出的票据当前价值,占面值的百分比(100% = 平价)。The model value of the note as a % of face (100% = par).",
  },
  issuePrice: {
    label: "Issue price", cn: "发行价",
    tip: "客户认购价(占面值%);低于 100% 表示折价发行。The subscription price as % of face.",
  },
  chanceEarlyExit: {
    label: "Chance of early exit", cn: "提前收回概率",
    tip: "票据在到期前被提前收回的估计概率——通常是好事(拿回本金+票息)。Estimated chance the note redeems early — usually a good outcome.",
  },
  chanceLoss: {
    label: "Chance of loss zone", cn: "进入亏损区概率",
    tip: "到期时最差股票跌破保护线、本金开始受损的估计概率。Estimated chance the worst stock ends below the protection level and principal is at risk.",
  },
  expectedLife: {
    label: "Expected life", cn: "预计存续期",
    tip: "考虑提前收回后,票据平均存续的年数。Average years the note is expected to stay outstanding.",
  },

  // ── market read ─────────────────────────────────────────────────────────────
  volLevel: {
    label: "Volatility level", cn: "市场波动水平",
    tip: "大盘当前的波动程度(3 个月)。高波动 → 卖出型票据票息更高。Market swing level now; higher favours income notes.",
  },
  putSkew: {
    label: "Downside premium", cn: "下跌保护溢价",
    tip: "市场为下跌保护付出的额外成本。偏高时,收息型票据更划算。Extra price the market pays for downside protection; high favours income notes.",
  },
  termSlope: {
    label: "Term slope", cn: "期限结构",
    tip: "长期 vs 短期波动的差。正 = 后市预期更平静。The gap between long- and short-dated volatility.",
  },
  vixProxy: {
    label: "Fear gauge", cn: "恐慌指数(近似)",
    tip: "以 SPY 30 天波动近似的市场恐慌温度。A VIX-style read of market stress.",
  },
  suitability: {
    label: "Note fit score", cn: "票据适配度",
    tip: "当前市场对每类票据的适配打分(0–100),附带理由。How well today's market fits each note type, 0–100, with reasons.",
  },
};

/** Convenience: the tooltip text for a term key (empty string if unknown). */
export function tipOf(key: string): string {
  return FENNY_TERMS[key]?.tip ?? "";
}
