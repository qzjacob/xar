"""Quote-sheet generation.

Produces a self-contained HTML quote sheet (print-to-PDF in any browser) and,
when WeasyPrint is installed, a real PDF. The mandatory disclosure block and the
input audit snapshot are load-bearing (plan §6 P0 gaps): the sheet is indicative,
model-dependent, capital-at-risk, and records exactly which market inputs were used.
"""

from __future__ import annotations

DISCLAIMER = (
    "INDICATIVE TERMS ONLY — NOT A FIRM QUOTE OR AN OFFER. This document is a "
    "model-generated indication produced for discussion purposes. Prices and the "
    "indicative coupon are model-dependent and rely on assumptions (notably the "
    "implied-volatility skew, correlation and borrow cost) that are not directly "
    "observable for single-name equities and may differ materially from executable "
    "levels. The product places CAPITAL AT RISK: if the knock-in is triggered and "
    "the worst performer is below its strike at maturity, the investor receives less "
    "than par (or physical delivery of the worst performer) and may lose a "
    "substantial part of the investment. The autocall feature can shorten the "
    "investment horizon. This is not investment advice; suitability and risk "
    "disclosures (e.g. PRIIPs KID where applicable) must be reviewed before any "
    "investment. Figures carry Monte Carlo standard error as shown."
)


def _payoff_svg(payoff: dict, width: int = 520, height: int = 220) -> str:
    xs = payoff["worst_of"]
    ys = payoff["redemption"]
    notional = max(ys) if ys else 1.0
    x0, x1 = min(xs), max(xs)
    y0, y1 = 0.0, max(ys) * 1.15
    pad = 30

    def sx(x: float) -> float:
        return pad + (x - x0) / (x1 - x0) * (width - 2 * pad)

    def sy(y: float) -> float:
        return height - pad - (y - y0) / (y1 - y0) * (height - 2 * pad)

    pts = " ".join(f"{sx(x):.1f},{sy(y):.1f}" for x, y in zip(xs, ys, strict=True))
    ki = payoff.get("ki", 0.0)
    strike = payoff.get("strike", 1.0)
    ki_line = (
        f'<line x1="{sx(ki):.1f}" y1="{pad}" x2="{sx(ki):.1f}" y2="{height-pad}" '
        f'stroke="#c0392b" stroke-dasharray="4 3"/>'
        f'<text x="{sx(ki)+3:.1f}" y="{pad+12}" font-size="10" fill="#c0392b">KI {ki:.0%}</text>'
        if ki
        else ""
    )
    par_y = sy(notional)
    return f"""<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">
  <rect x="0" y="0" width="{width}" height="{height}" fill="#fff" stroke="#ddd"/>
  <line x1="{pad}" y1="{par_y:.1f}" x2="{width-pad}" y2="{par_y:.1f}" stroke="#bbb" stroke-dasharray="2 2"/>
  <text x="{width-pad-30:.1f}" y="{par_y-4:.1f}" font-size="10" fill="#888">par</text>
  <line x1="{sx(strike):.1f}" y1="{pad}" x2="{sx(strike):.1f}" y2="{height-pad}" stroke="#2980b9" stroke-dasharray="4 3"/>
  <text x="{sx(strike)+3:.1f}" y="{height-pad-4:.1f}" font-size="10" fill="#2980b9">strike {strike:.0%}</text>
  {ki_line}
  <polyline points="{pts}" fill="none" stroke="#16a085" stroke-width="2"/>
  <text x="{pad}" y="{height-8}" font-size="10" fill="#555">worst-of level at maturity</text>
</svg>"""


def build_quote_sheet_html(context: dict) -> str:
    p = context["pricing"]
    fees = context["fees"]
    product = context["product"]
    market = context["market"]
    scenario = context.get("scenario_table") or []
    greeks = context.get("greeks")
    coupon = context.get("coupon_label", f"{p['coupon_rate']*100:.2f}% p.a.")

    rows_market = "".join(
        f"<tr><td>{a['ticker']}</td><td>{a['spot']:.4g}</td><td>{a['atm_vol']*100:.1f}%</td>"
        f"<td>{a['skew_slope']:+.2f}</td><td>{a['div_yield']*100:.2f}%</td>"
        f"<td>{a['borrow']*100:.2f}%</td></tr>"
        for a in market["assets"]
    )
    scen_head = "".join(f"<th>{r['shock']:+.0%}</th>" for r in scenario)
    scen_pv = "".join(f"<td>{r['price_pct']:.1f}</td>" for r in scenario)
    scen_ki = "".join(f"<td>{r['prob_knock_in']*100:.0f}%</td>" for r in scenario)
    greeks_html = ""
    if greeks:
        deltas = ", ".join(
            f"{t}: {d:+.3f}" for t, d in zip(product["tickers"], greeks["delta"], strict=False)
        )
        vegas = ", ".join(
            f"{t}: {v:+.3f}" for t, v in zip(product["tickers"], greeks["vega"], strict=False)
        )
        greeks_html = f"""<h3>Sensitivities (per unit move)</h3>
        <p class="g">Δ (per +1% spot) — {deltas}<br/>Vega (per +1 vol pt) — {vegas}<br/>
        Rho (funding, /+1bp): {greeks['rho']:+.4f} &nbsp; Carry (growth, /+1bp): {greeks.get('carry',0):+.4f}<br/>
        Skew-vega (put−call wing): {greeks.get('skew_vega',0):+.4f} &nbsp; Corr-sens (/+0.01): {greeks['corr_sens']:+.4f} &nbsp; Theta (1d): {greeks.get('theta',0):+.4f}</p>"""

    return f"""<!doctype html><html><head><meta charset="utf-8"><title>FCN Quote Sheet</title>
<style>
  body{{font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#222;max-width:780px;margin:24px auto;line-height:1.45}}
  h1{{font-size:20px;margin-bottom:0}} h2{{font-size:15px;border-bottom:1px solid #eee;padding-bottom:4px;margin-top:24px}}
  h3{{font-size:13px;margin-bottom:4px}} .sub{{color:#888;font-size:12px}}
  table{{border-collapse:collapse;width:100%;font-size:12px;margin-top:6px}}
  td,th{{border:1px solid #e3e3e3;padding:4px 8px;text-align:right}} th{{background:#fafafa}}
  td:first-child,th:first-child{{text-align:left}}
  .kpi{{display:flex;gap:24px;margin-top:8px}} .kpi div{{flex:1;background:#f7faf9;border:1px solid #e3efe9;border-radius:6px;padding:10px}}
  .kpi .v{{font-size:20px;font-weight:600;color:#0d6b58}} .kpi .l{{font-size:11px;color:#777}}
  .disc{{font-size:10px;color:#777;background:#fcfbf7;border:1px solid #efe9d8;border-radius:6px;padding:10px;margin-top:24px}}
  .g{{font-size:12px}}
</style></head><body>
<h1>Fixed Coupon Note — Indicative Quote</h1>
<div class="sub">{product['variant']} · {product['n_assets']}-name {product['basket']} · {product['currency']} {product['notional']:,.0f} · maturity {product['maturity']}</div>

<div class="kpi">
  <div><div class="l">Indicative coupon</div><div class="v">{coupon}</div></div>
  <div><div class="l">Fair value</div><div class="v">{p['price_pct']:.2f}%</div><div class="l">± {p['pv_se']/p['notional']*100:.3f}% (MC SE)</div></div>
  <div><div class="l">Reoffer</div><div class="v">{fees['reoffer']:.2f}%</div></div>
</div>

<h2>Risk metrics</h2>
<table>
  <tr><th>P(autocall)</th><th>P(knock-in)</th><th>Expected life</th><th>Paths / method</th></tr>
  <tr><td>{p['prob_autocall']*100:.1f}%</td><td>{p['prob_knock_in']*100:.1f}%</td>
      <td>{p['expected_life']:.2f} y</td><td>{p['n_paths']:,} / {p['method']}</td></tr>
</table>

<h2>Payoff at maturity (cash settlement)</h2>
{_payoff_svg(context['payoff_diagram'])}

<h2>Scenario (parallel spot shock)</h2>
<table>
  <tr><th>shock</th>{scen_head}</tr>
  <tr><td>price %par</td>{scen_pv}</tr>
  <tr><td>P(KI)</td>{scen_ki}</tr>
</table>

<h2>Fees</h2>
<table>
 <tr><th>par</th><th>structuring</th><th>distribution</th><th>hedging reserve</th><th>reoffer</th></tr>
 <tr><td>{fees['par']:.2f}</td><td>{fees['structuring']:.2f}</td><td>{fees['distribution']:.2f}</td>
     <td>{fees['hedging_reserve']:.2f}</td><td>{fees['reoffer']:.2f}</td></tr>
</table>

{greeks_html}

<h2>Market inputs used (audit)</h2>
<div class="sub">as of {market['asof']} · rate {market['rate']*100:.2f}% · source: {market['source']}</div>
<table>
 <tr><th>underlying</th><th>spot</th><th>ATM vol</th><th>skew slope</th><th>div yld</th><th>borrow</th></tr>
 {rows_market}
</table>

<div class="disc"><b>Disclaimer.</b> {DISCLAIMER}</div>
</body></html>"""


def render_pdf(html: str) -> bytes | None:
    """Render HTML to PDF via WeasyPrint if available; else None (use browser print)."""
    try:
        from weasyprint import HTML  # type: ignore
    except Exception:
        return None
    return HTML(string=html).write_pdf()
