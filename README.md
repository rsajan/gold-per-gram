# Gold per Gram

Interactive gold price history per gram in USD, INR and SGD, from 1970 to today.

- `index.html` is the whole site. It needs only `data.js` and `vendor/lightweight-charts.js`.
- `tools/update_data.py` rebuilds `data.js` from free public sources (no API key). A GitHub Action runs it every six hours.
- Prices are the international gold price converted at market exchange rates. Local retail prices add duty, tax and making charges.

Charts use [TradingView Lightweight Charts](https://github.com/tradingview/lightweight-charts) (Apache 2.0).
