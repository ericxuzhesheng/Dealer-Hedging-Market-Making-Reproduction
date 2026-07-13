# Dealer-Hedging Reproduction Agent Notes

Use this file when updating the teacher-facing LaTeX report or backtest evidence
for this repository.

## Report Logic

- Write one article-style LaTeX report in `report/report.tex` and compile
  `report/report.pdf`.
- Follow the user's `leadlag2复现报告.pdf` style: title page, abstract, table of
  contents, model mechanism, data and assumptions, backtest design, result
  tables, limitations, and a short human learning reflection.
- The long proxy backtest should use cached or fetched Tushare `510300.SH`
  minute bars as the CSI 300 ETF market proxy.
- The hftbacktest run should be described as a framework-valid order
  state-machine check on `synthetic_l2`, not historical dealer-market Level2
  replay.
- State explicitly that true customer RFQ flow, dealer quote decisions,
  inter-dealer hedge fills, displayed depth, and market impact curves are
  proxied.
- The core interpretation is the risk tradeoff: external hedging can reduce
  inventory volatility, but it pays spread, fee, and impact costs.

## Completion Checks

- `README.md`, `report/report.tex`, `report/report.pdf`, and
  `results/hftbacktest/summary.json` must agree on data source, market, sample
  window, framework, and limitations.
- Remove LaTeX auxiliary files and generated feed caches before committing.
- Stage only task-relevant files.
