# FMP-backed product ideas

## 1. Opportunity scanner for active traders

Build on current MarketTool monitoring. Combine OHLCV, quote batch, technical indicators, earnings calendar and news. Sell alert packs by watchlist size, timeframe depth and notification channels.

Why it can sell: users pay for timely decisions, not raw charts. The app already has analysis, monitoring, Telegram and mobile/web surfaces.

MVP:

- Curated symbols and watchlists.
- Setup score per symbol/timeframe.
- Telegram alerts with entry, invalidation and risk notes.
- Backtest-lite view showing recent occurrences.

## 2. Fundamental quality dashboard

Use profile, market cap, historical market cap, key metrics, ratios, financial scores, enterprise values and financial statements.

Why it can sell: fundamentals are cacheable, cheaper to serve and easier to package as reports.

MVP:

- Ranking by quality, growth, profitability, debt and valuation.
- Company detail with trend charts.
- Compare 2-5 companies.
- Export PDF/report.

## 3. Earnings and catalyst monitor

Use earnings calendar, earnings reports, earnings surprises, dividends, splits, IPO calendar, press releases and stock news.

Why it can sell: event-driven traders need a filtered calendar, not a raw calendar.

MVP:

- Weekly event calendar.
- Pre-earnings volatility and trend context.
- Post-event movement tracker.
- Alerts for high-impact events.

## 4. Insider, institutional and Senate tracker

Use insider trades, insider statistics, 13F filings, holder performance, ownership analytics and Senate disclosures.

Why it can sell: the story is simple and marketable: detect what informed actors are buying or selling.

MVP:

- Unusual insider buys/sells.
- Fund accumulation/rotation by sector.
- Politician trade alerts.
- Cross-check with price trend and fundamentals.

## 5. ETF exposure explorer

Use ETF holdings, country weighting, sector weighting, asset exposure, ETF quotes and disclosure endpoints.

Why it can sell: retail investors often know themes but not the right instrument.

MVP:

- Search by theme/company/exposure.
- Compare holdings overlap.
- Sector/country exposure breakdown.
- Risk and cost summary.

## 6. Internal data lake as a product foundation

Use the harvester to persist OHLCV, fundamentals, events, ratings and ownership into GCS/local cache/Redis.

Why it matters: this reduces FMP dependency, improves latency and enables later backtesting and paid screeners.

MVP:

- Harvest manifests by dataset.
- Quality score per symbol/timeframe.
- Gap repair queue.
- Admin view for coverage, last update and replacement decisions.

## Recommended sequence

1. Finish data lake quality and gap repair.
2. Build company fundamental dashboard.
3. Add opportunity scanner alerts.
4. Add earnings/catalyst module.
5. Add insider/institutional module.

This order gives the app reusable data infrastructure first, then products that can be monetized without burning live API calls.
