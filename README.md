# rider-analysis

Analysis of dressage show entries in Ontario using data scraped from the [CompeteEasy Equest portal](https://www.competeeasy.com/Equest/Results.aspx).

## Project Summary

This project scrapes and analyzes Ontario dressage competition data from CompeteEasy. It collects both **class-level summaries** (rider counts by competition level and rider status) and **per-rider detail** (rider names, horse names, scores, and placements), enabling tracking of individual riders across multiple shows.

## How the Scraping Works

CompeteEasy is an ASP.NET WebForms application. The scraper in `scrape_ontario_dressage.py` uses `requests` + `BeautifulSoup` (no browser/Selenium needed) and works in two phases:

### Phase 1: Show Discovery & Class-Level Summary (fast, ~2 minutes)

1. **Discover all dressage shows**: GET `Equest/Results.aspx`, POST back with `eventlist=2` (Dressage) to populate a dropdown of ~740 events with IDs and names.
2. **Filter for Ontario shows**: Keyword matching on show names using `ONTARIO_KEYWORDS` / `EXCLUDE_KEYWORDS` lists and date filtering to the target year range.
3. **Scrape show summaries**: For each Ontario show, GET `DressageReport.aspx?EventID={id}`. Each class is in a `div.resultoutbl` wrapper (with a `gotonextpage(classid, eventid)` onclick) containing a `table.resulttbl` with the class name, rider count, and ClassID.

### Phase 2: Per-Rider Detail (slow, ~8 hours first run)

4. **Scrape class detail pages**: For each class with a ClassID, GET `NewDressageReportClass.aspx?ClassID={cid}&EventID={eid}` and extract:
   - Rider name (`input[id^=rider_]`)
   - Horse name (`input[id^=horse_]`)
   - Overall score percentage (`span[id*=lblDRGood]`)
   - Placement (`span[id*=lblDRPlace]`)
   - Bridle number (`span.numberclass`)
   - Status — SCR/ELIM/etc. (`input[id*=lblDRStatus]`)

### Caching & Resumability

All fetched pages are cached to `cache/` on disk. If the scraper is interrupted, re-running it skips already-cached pages instantly (no delays). Historical results don't change, so the cache has no expiry.

### Organic Browsing Delays

To avoid hammering the server, uncached requests use randomized delays: 2–5s between class pages, 5–10s between shows, and a 15–30s pause every ~50 requests. Cached pages skip all delays.

### Class Name Classification

Class names encode both competition level and rider status in their prefix codes:
- **Competition level**: `BR*` = Bronze, `S*`/`SFEI*` = Silver, `SC*` = Silver Championship, digits/`G*` = Gold, `ON*` = Ontario Championship (Gold), `CA*`/`CAD*` = CADORA, `WSDAC*`/`WS*` = Western Sport Dressage (CADORA equivalent), `HC*`/`NC*` = Non-Competing
- **Rider status**: suffix `AA` = Adult Amateur, `JR` = Junior, `OP` = Open, `SR` = Senior (treated as Open), no suffix = Unspecified

## Usage

```bash
# Full run (phase 1 + phase 2 rider detail)
uv run python scrape_ontario_dressage.py

# Phase 1 only (show/class discovery, no per-rider detail)
uv run python scrape_ontario_dressage.py --skip-detail

# Clear cache and re-run from scratch
uv run python scrape_ontario_dressage.py --clear-cache

# Validate Ontario keywords against the Nominate page
uv run python validate_ontario_keywords.py
```

## Files

| File | Description |
|------|-------------|
| `scrape_ontario_dressage.py` | Main scraping script (two-phase architecture). |
| `validate_ontario_keywords.py` | Validates keyword list against CompeteEasy Nominate page (ON + Dressage). |
| `discover_shows.py` | Discovery script to search CompeteEasy for shows matching specific keywords. |
| `reviewed-robots-2026-03-01.txt` | robots.txt review confirming no restrictions on scraped endpoints. |
| `analysis_report.md` | Full analysis report with tables, trends, and findings. |
| `ontario_dressage_rider_results.csv` | Per-rider detail: one row per rider per class with scores and placements. |
| `ontario_dressage_raw_results.json` | Raw class-level data with ClassIDs. |
| `ontario_dressage_raw_results.csv` | Class-level CSV for Excel pivot tables. |
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

- **Ontario identification is heuristic**: shows are matched by name keywords, not by a province field. Run `validate_ontario_keywords.py` periodically to catch new Ontario show series.
- **No 2021 data**: CompeteEasy dressage results start in 2022.
- **"Unspecified" rider status**: ~39% of entries (mostly Gold and CADORA classes) don't have AA/JR/OP suffixes in their class codes.
- **Entry counts are class-entries, not unique riders**: one rider entering 3 classes = 3 entries. The rider-level CSV enables deduplication.
- **Some shows may appear under multiple event IDs** (e.g., separate CDI and national classes at the same venue/weekend).

## Dependencies

Managed with [uv](https://docs.astral.sh/uv/). Dependencies are declared in `pyproject.toml`:

- `requests`
- `beautifulsoup4`
- `lxml`

Python 3.10+.
