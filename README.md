# rider-analysis

Analysis of dressage show entries in Ontario using data scraped from the [CompeteEasy Equest portal](https://www.competeeasy.com/Equest/Results.aspx).

## Project Summary

This project scrapes and analyzes Ontario dressage competition entry numbers from CompeteEasy, broken down by **competition level** (Bronze, Silver, Gold) and **rider status** (Junior, Adult Amateur, Open). The full analysis report is in [`analysis_report.md`](analysis_report.md).

## How the Scraping Works

CompeteEasy is an ASP.NET WebForms application. The scraper in `scrape_ontario_dressage.py` uses `requests` + `BeautifulSoup` (no browser/Selenium needed) and works in three stages:

### 1. Discover all dressage shows

- GET `Equest/Results.aspx` to load the page and capture ASP.NET hidden fields (`__VIEWSTATE`, `__EVENTVALIDATION`, etc.).
- POST back to the same URL with `eventlist=2` (Dressage discipline) and `rdbSearch=event` (search-by-Show mode). This triggers an ASP.NET postback that populates a `<select id="ddlEvents">` dropdown containing **all dressage shows** (~740 events) with their event IDs and names.
- Parse all `<option>` elements from that dropdown.

### 2. Filter for Ontario shows

- The portal has **no province filter on the Results page** (only the Nominate/upcoming-shows page has one). Ontario shows are identified by keyword matching on the show name using two lists:
  - **`ONTARIO_KEYWORDS`** — known Ontario venues/organizations: Caledon, Angelstone, Dressage Niagara, Kawartha, Centreline Dressage, Wits End, Royal Winter Fair, Dreamcrest, Palgrave, Glanbrook, LDA Dressage, QSLB, Quantum, Queenswood, Stevens Creek, Westar, Canyon Creek, etc.
  - **`EXCLUDE_KEYWORDS`** — non-Ontario shows that might match broadly: Southlands (BC), ESDCTA/Highthorn/Wild Rose (Alberta), EAADA (Edmonton), MDC (Manitoba), Gingerwood (PEI), etc.
- Show dates are extracted from the show name string (format `- MM/DD/YYYY` at end of name), then filtered to the target year range.

### 3. Scrape individual show results

- For each Ontario show, GET `scoreboard/results/Web/DressageReport.aspx?EventID={id}`.
- This page contains one `<table>` per class, each with two rows: the class name and a "Riders: N" count.
- Class names encode both competition level and rider status in their prefix codes:
  - **Competition level**: `BR*` = Bronze, `S*`/`SFEI*` = Silver, `SC*` = Silver Championship, digits/`G*` = Gold, `ON*` = Ontario Championship (Gold), `CA*`/`CAD*` = CADORA, `WSDAC*`/`WS*` = Western Sport Dressage (CADORA equivalent), `HC*`/`NC*` = Non-Competing
  - **Rider status**: suffix `AA` = Adult Amateur, `JR` = Junior, `OP` = Open, `SR` = Senior (treated as Open), no suffix = Unspecified

## Files

| File | Description |
|------|-------------|
| `scrape_ontario_dressage.py` | Main scraping script. Run with `uv run --with requests --with beautifulsoup4 --with lxml python scrape_ontario_dressage.py`. |
| `discover_shows.py` | Discovery script to search CompeteEasy for shows matching specific keywords. |
| `reviewed-robots-2026-03-01.txt` | robots.txt review confirming no restrictions on scraped endpoints. |
| `analysis_report.md` | Full analysis report with tables, trends, and findings. |
| `ontario_dressage_raw_results.json` | Raw class-level data: show ID/name, date, class name, rider count, classified comp level and rider status. |
| `ontario_dressage_summary.csv` | Aggregated CSV: year, competition level, rider status, entry count, show count. |

## Key Findings (2022-2025)

- **17,113 total entries** across **177 show instances** in 4 years (no 2021 data on platform).
- **Stable participation**: entries ranged from 3,979 (2022) to 4,582 (2023), with 4,241 in 2025.
- **Growing show count**: 39 shows in 2022 to 52 in 2025.
- **Gold** is the largest category (47.2% of classified entries), followed by Bronze (28.1%), Silver (20.3%), CADORA (4.3%).
- **Adult Amateur** riders are the largest group (48.7% of classified entries), dominant at Bronze/Silver level.
- **Open** riders concentrate at Gold level (36.5% of classified entries).
- **Junior** riders are the smallest group (14.8%).

## Known Limitations & Future Work

- **Ontario identification is heuristic**: shows are matched by name keywords, not by a province field. Some smaller Ontario shows with generic names may be missed; verify against the `ONTARIO_KEYWORDS` list.
- **No 2021 data**: CompeteEasy dressage results start in 2022.
- **"Unspecified" rider status**: ~39% of entries (mostly Gold and CADORA classes) don't have AA/JR/OP suffixes in their class codes, so rider status can't be determined.
- **Entry counts are class-entries, not unique riders**: one rider entering 3 classes = 3 entries.
- **Some shows may appear under multiple event IDs** (e.g., separate CDI and national classes at the same venue/weekend). These are counted as separate show instances.
- To expand coverage, the `ONTARIO_KEYWORDS` and `EXCLUDE_KEYWORDS` lists can be updated as new shows appear on CompeteEasy.

## Dependencies

```
uv run --with requests --with beautifulsoup4 --with lxml python scrape_ontario_dressage.py
```

Or install manually: `pip install requests beautifulsoup4 lxml`. Python 3.8+.
