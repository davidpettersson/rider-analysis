# rider-analysis

Analysis of dressage show entries in Ontario via the [CompeteEasy Equest portal](https://www.competeeasy.com/Equest/Results.aspx).

## Overview

This project scrapes the CompeteEasy Equest portal to collect and analyze dressage competition entry data for Ontario over the last 5 years. The data is broken down by:

- **Competition status**: Bronze, Silver, Gold (as sanctioned by Equestrian Canada)
- **Rider status**: Junior (Jr/YR), Adult Amateur (AA), Open

## Background

### Competition Levels (Equestrian Canada)

| Level | Description |
|-------|-------------|
| **Bronze** | Entry-level EC sanctioned competitions, approved by provincial organizations (e.g., Ontario Equestrian) |
| **Silver** | Mid-level provincial circuit competitions |
| **Gold** | National-level competitions sanctioned by Equestrian Canada |

### Rider Categories

| Category | Description |
|----------|-------------|
| **Junior (Jr/YR)** | Youth riders, up to the calendar year they turn 22 |
| **Adult Amateur (AA)** | Adult riders who ride as a hobby; amateur status issued by EC |
| **Open** | All riders regardless of age or status, including professionals |

## Scripts

### 1. `scrape_competeeasy.py` — Requests-based scraper

Lightweight scraper using `requests` + `BeautifulSoup`. Handles ASP.NET ViewState/postback mechanics directly via HTTP.

```bash
python3 scrape_competeeasy.py --output results.csv --years 5
```

### 2. `scrape_selenium.py` — Selenium-based scraper (recommended)

Uses a headless Chrome browser for more robust interaction with the ASP.NET WebForms portal, including JavaScript-driven postbacks.

```bash
python3 scrape_selenium.py --output results.csv --years 5

# Run with visible browser for debugging
python3 scrape_selenium.py --headed --verbose
```

**Prerequisites**: Chrome/Chromium and ChromeDriver must be installed.

### 3. `analyze.py` — Data analysis

Reads scraped data and produces summary tables broken down by competition level, rider status, and year.

```bash
python3 analyze.py --input ontario_dressage_entries.json
```

## Setup

```bash
pip install -r requirements.txt

# For Selenium scraper, also install ChromeDriver:
# Ubuntu/Debian: sudo apt install chromium-chromedriver
# macOS: brew install chromedriver
```

## Output Files

| File | Description |
|------|-------------|
| `ontario_dressage_entries.csv` | Raw entry data in CSV format |
| `ontario_dressage_entries.json` | Raw entry data in JSON format |
| `analysis_summary.txt` | Text summary report |

## Data Source

All data is scraped from the [CompeteEasy Equest portal](https://www.competeeasy.com/Equest/Results.aspx), which is the platform used by many Ontario dressage show organizers for competition entries and results.

Key portal pages:
- **Results**: https://www.competeeasy.com/Equest/Results.aspx
- **Shows**: https://www.competeeasy.com/Equest/Nominate.aspx
- **Scoreboard**: https://www.competeeasy.com/scoreboard/results/
