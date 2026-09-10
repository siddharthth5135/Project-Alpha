# Market data for promote-grade training (not Yahoo)

**Final decision:**  
- **Primary training feed:** TrueData Market Data API (NSE **NFO** historical)  
- **Backup:** Global Datafeeds  
- **Angel One SmartAPI:** execution broker + short live-contract candles only — **not** a multi-year futures history source  
- **Yahoo / synthetic:** plumbing / unit tests only  

## Why Angel alone is not enough

Angel’s historical candle API generally returns data for **currently listed** NFO contracts.  
Expired futures months are typically unavailable, so you cannot reconstruct years of continuous futures history from Angel alone. That blocks promote-grade walk-forward training.

Docs: https://smartapi.angelone.in/docs/Historical  

## Exact links

| Item | URL |
| --- | --- |
| TrueData home | https://www.truedata.in/ |
| TrueData Market Data API | https://www.truedata.in/products/marketdataapi |
| TrueData API guide | https://feedback.truedata.in/knowledge-base/article/getting-started-with-truedata-market-data-api |
| Global Datafeeds (backup) | https://globaldatafeeds.in/ |
| Angel SmartAPI (broker, not history archive) | https://smartapi.angelbroking.com/ |
| Angel contract note help | https://www.angelone.in/support/reports-and-statements/contract-note |

## What to buy from TrueData

- Segment: **NSE F&O / NFO**  
- Symbols: NIFTY + BANKNIFTY futures  
- History: ≥ **3 years**, 1-minute preferred  
- License: algo use  
- Delivery: REST/API + Python  

Human checklist: [`HOQR_ACTION_CHECKLIST.md`](HOQR_ACTION_CHECKLIST.md).

**Full requirements + architecture review + TrueData sales RFQ:**  
[`MARKET_DATA_REQUIREMENTS.md`](MARKET_DATA_REQUIREMENTS.md)
