# Backtest of the rule-based filters

Monthly rebalancing on up to 10 years of daily closes, costs included. The watchlists are today's, so every stock strategy benefits from survivorship bias: compare strategies with each other more than with the benchmark.

## US stocks (benchmark SPY, 8.8 years)

| strategy | CAGR | max drawdown | volatility | Sharpe | total costs (x start capital) |
|---|---|---|---|---|---|
| bench | +13.1% | -22.3% | 16.8% | 0.82 | 0.1% |
| ew | +21.8% | -25.4% | 19.6% | 1.11 | 1.8% |
| ew_trend | +14.2% | -14.4% | 12.4% | 1.13 | 3.8% |
| ew_filter | +11.5% | -12.9% | 11.7% | 0.99 | 3.2% |
| mom5 | +41.0% | -25.0% | 33.2% | 1.20 | 34.4% |
| mom5_trend | +33.8% | -27.3% | 29.9% | 1.13 | 27.2% |
| core60_ew | +16.6% | -23.5% | 17.7% | 0.96 | 0.7% |
| core60_ew_filter | +12.6% | -17.8% | 14.0% | 0.92 | 1.4% |
| core60_mom5_trend | +21.7% | -20.3% | 20.2% | 1.08 | 6.5% |

## ASX stocks (benchmark STW.AX, 8.8 years)

| strategy | CAGR | max drawdown | volatility | Sharpe | total costs (x start capital) |
|---|---|---|---|---|---|
| bench | +4.4% | -23.9% | 14.2% | 0.37 | 0.2% |
| ew | +13.7% | -23.0% | 15.9% | 0.89 | 2.0% |
| ew_trend | +7.2% | -13.2% | 8.8% | 0.84 | 5.2% |
| ew_filter | +5.1% | -10.2% | 7.8% | 0.68 | 4.3% |
| mom5 | +23.7% | -25.0% | 26.7% | 0.93 | 31.6% |
| mom5_trend | +16.2% | -36.9% | 26.2% | 0.71 | 28.7% |
| core60_ew | +8.1% | -23.5% | 14.6% | 0.61 | 0.8% |
| core60_ew_filter | +4.8% | -18.2% | 11.0% | 0.49 | 1.8% |
| core60_mom5_trend | +9.5% | -23.6% | 17.3% | 0.61 | 7.3% |

## crypto (benchmark BTC-USD, 9.2 years)

| strategy | CAGR | max drawdown | volatility | Sharpe | total costs (x start capital) |
|---|---|---|---|---|---|
| bench | +45.2% | -77.2% | 77.8% | 0.84 | 0.4% |
| ew | +41.4% | -87.4% | 124.2% | 0.74 | 92.1% |
| ew_trend | +35.4% | -67.8% | 96.6% | 0.66 | 144.8% |
| ew_filter | +30.6% | -67.8% | 93.1% | 0.61 | 81.6% |
| mom5 | +35.6% | -89.4% | 120.9% | 0.64 | 427.6% |
| mom5_trend | +42.4% | -72.7% | 114.5% | 0.65 | 392.9% |
| core60_ew | +50.6% | -81.4% | 87.8% | 0.86 | 77.4% |
| core60_ew_filter | +46.8% | -72.4% | 71.6% | 0.87 | 85.0% |
| core60_mom5_trend | +54.8% | -69.4% | 75.8% | 0.91 | 265.0% |

