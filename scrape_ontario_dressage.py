#!/usr/bin/env python3
"""
Scrape CompeteEasy for Ontario dressage show entry data.

Two-phase architecture:
  Phase 1 (fast): Discover shows, filter Ontario, get class-level summary
                  with class names, rider counts, and ClassIDs.
  Phase 2 (slow): For each class, fetch per-rider detail (placement, score,
                  rider name, horse name, bridle number, status).

Usage:
  python scrape_ontario_dressage.py              # Full run (phase 1 + 2)
  python scrape_ontario_dressage.py --skip-detail # Phase 1 only
  python scrape_ontario_dressage.py --clear-cache # Clear cache before running
"""

import argparse
import csv
import json
import os
import random
import re
import time
from collections import defaultdict
from datetime import datetime

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.competeeasy.com/Equest"
SCOREBOARD_URL = "https://www.competeeasy.com/scoreboard/results/Web"
CACHE_DIR = "cache"

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
})

# Ontario show identifiers - confirmed Ontario-based organizations/venues
ONTARIO_KEYWORDS = [
    "caledon",
    "angelstone",
    "centreline dressage",   # Centreline Dressage = Ontario org
    "dressage niagara",
    "niagara",
    "kawartha",
    "royal agricultural winter fair",
    "royal winter fair",
    "western ontario dressage",
    "working equitation central ontario",
    "wits end",
    "dreamcrest",
    "silver dressage championship",  # Silver Championship at Caledon
    "palgrave",
    "glanbrook",
    "lda dressage",
    "lda - virtual dressage",
    "qslb",
    "quantum",
    "queenswood",
    "stevens creek",
    "westar",
    "canyon creek",
    "ontario",
]

# Keywords that indicate NON-Ontario shows (to exclude false positives)
EXCLUDE_KEYWORDS = [
    "southlands",     # BC
    "esdcta",         # Alberta (Edmonton/Strathcona)
    "highthorn",      # Alberta
    "wild rose",      # Alberta
    "eaada",          # Edmonton Area Alberta
    "mdc ",           # Manitoba Dressage Club
    "gingerwood",     # PEI
    "tropics",        # Florida/elsewhere
    "prince edward",  # PEI
    "manitoba",
    "alberta",
    "british columbia",
    "bc ",
    "quebec",
    "saskatchewan",
    "nova scotia",
]

# Year range: last 5 years
YEAR_START = 2021
YEAR_END = 2026  # inclusive of shows in early 2026 if any

# Request counter for organic pauses
_request_count = 0


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _ensure_cache_dir():
    os.makedirs(CACHE_DIR, exist_ok=True)


def _cache_path(prefix, *parts):
    return os.path.join(CACHE_DIR, f"{prefix}_{'_'.join(str(p) for p in parts)}.html")


def _read_cache(path):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    return None


def _write_cache(path, content):
    _ensure_cache_dir()
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def clear_cache():
    """Remove all cached files."""
    if os.path.exists(CACHE_DIR):
        for fname in os.listdir(CACHE_DIR):
            fpath = os.path.join(CACHE_DIR, fname)
            if os.path.isfile(fpath):
                os.remove(fpath)
        print(f"Cleared cache directory: {CACHE_DIR}/")


# ---------------------------------------------------------------------------
# Organic delay helpers
# ---------------------------------------------------------------------------

def _delay_between_classes():
    """2-5 second random delay between class page requests."""
    global _request_count
    _request_count += 1
    # Every ~50 requests, take a longer break
    if _request_count % 50 == 0:
        pause = random.uniform(15, 30)
        print(f"    (pausing {pause:.0f}s after {_request_count} requests)")
        time.sleep(pause)
    else:
        time.sleep(random.uniform(2, 5))


def _delay_between_shows():
    """5-10 second random delay between shows."""
    time.sleep(random.uniform(5, 10))


# ---------------------------------------------------------------------------
# ASP.NET helpers
# ---------------------------------------------------------------------------

def get_asp_fields(soup):
    """Extract ASP.NET hidden fields for postback."""
    fields = {}
    for name in ["__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION",
                  "__EVENTTARGET", "__EVENTARGUMENT", "__LASTFOCUS"]:
        el = soup.find("input", {"name": name})
        if el:
            fields[name] = el.get("value", "")
    el = soup.find("input", {"name": "ctl00_ctl00_ScriptManager1_HiddenField"})
    if el:
        fields["ctl00_ctl00_ScriptManager1_HiddenField"] = el.get("value", "")
    return fields


# ---------------------------------------------------------------------------
# Phase 1: Show discovery and class-level summary
# ---------------------------------------------------------------------------

def get_all_dressage_shows():
    """Get the full list of dressage shows from the Results page."""
    print("Loading Results page...")
    resp = session.get(f"{BASE_URL}/Results.aspx")
    soup = BeautifulSoup(resp.text, "lxml")
    asp_fields = get_asp_fields(soup)

    print("Posting back for Dressage show list...")
    post_data = {
        "ctl00_ctl00_ScriptManager1_HiddenField": asp_fields.get("ctl00_ctl00_ScriptManager1_HiddenField", ""),
        "__EVENTTARGET": "ctl00$ctl00$ChildContent1$NominateMeContent$rdbSearch",
        "__EVENTARGUMENT": "",
        "__LASTFOCUS": "",
        "__VIEWSTATE": asp_fields["__VIEWSTATE"],
        "__VIEWSTATEGENERATOR": asp_fields.get("__VIEWSTATEGENERATOR", ""),
        "__EVENTVALIDATION": asp_fields.get("__EVENTVALIDATION", ""),
        "eventlist": "2",  # Dressage
        "ctl00$ctl00$ChildContent1$NominateMeContent$rdbSearch": "event",
    }
    resp2 = session.post(f"{BASE_URL}/Results.aspx", data=post_data)
    soup2 = BeautifulSoup(resp2.text, "lxml")

    ddl = soup2.find("select", {"id": "ddlEvents"})
    if not ddl:
        ddl = soup2.find("select", {"name": "ctl00$ctl00$ChildContent1$NominateMeContent$ddlEvents"})

    shows = []
    for opt in ddl.find_all("option"):
        val = opt.get("value", "0")
        text = opt.text.strip()
        if val != "0":
            shows.append({"id": val, "name": text})

    print(f"Total dressage shows found: {len(shows)}")
    return shows


def parse_show_date(show_name):
    """Extract date from show name (format: '... - MM/DD/YYYY')."""
    match = re.search(r'(\d{2}/\d{2}/\d{4})\s*$', show_name)
    if match:
        try:
            return datetime.strptime(match.group(1), "%m/%d/%Y")
        except ValueError:
            pass
    return None


def is_ontario_show(show_name):
    """Check if a show is from Ontario based on name keywords."""
    name_lower = show_name.lower()

    # First check exclusions
    for kw in EXCLUDE_KEYWORDS:
        if kw in name_lower:
            return False

    # Then check inclusions
    for kw in ONTARIO_KEYWORDS:
        if kw in name_lower:
            return True

    return False


def classify_class_entry(class_name):
    """
    Parse a class name to extract competition level and rider status.

    Competition levels based on EC sanctioning:
      - Bronze: Introductory through First Level (class codes starting with BR)
      - Silver: Training through Fourth Level + FEI at Silver (codes starting with S)
      - Gold: All levels at Gold sanctioning (codes starting with digits)
      - CADORA: Regional/chapter classes (codes starting with CA/CAD)

    Rider statuses:
      - Junior (JR)
      - Adult Amateur (AA)
      - Open (OP/OPEN)
      - Unspecified (no suffix)
    """
    name_lower = class_name.lower().strip()
    class_code = name_lower.split("-")[0] if "-" in name_lower else name_lower

    # Determine competition level
    comp_level = "Unknown"
    if "bronze" in name_lower or class_code.startswith("br"):
        comp_level = "Bronze"
    elif class_code.startswith("sc") or ("silver" in name_lower and "championship" in name_lower):
        comp_level = "Silver"  # Silver Championship classes
    elif "silver" in name_lower or re.match(r'^s\d', class_code) or class_code.startswith("sfei"):
        comp_level = "Silver"
    elif class_code.startswith("on") or "ontario champ" in name_lower:
        comp_level = "Gold"  # Ontario Championships = Gold level
    elif "gold" in name_lower or re.match(r'^(g\d|\d)', class_code):
        comp_level = "Gold"
    elif class_code.startswith("ca") or "cadora" in name_lower or "canadian champ" in name_lower:
        comp_level = "CADORA"
    elif class_code.startswith("wsdac") or class_code.startswith("ws") or "wsdac" in name_lower or class_code.startswith("wd"):
        comp_level = "CADORA"  # Western Sport Dressage - treat as CADORA equivalent
    elif re.match(r'^(fei|gp|psg|int)', class_code):
        comp_level = "Gold"
    elif class_code.startswith("hc") or class_code.startswith("nc") or "non-compete" in name_lower or "hors concours" in name_lower:
        comp_level = "Non-Competing"  # HC/NC = not competing

    # Determine rider status from class code suffix and class name
    rider_status = "Unspecified"
    # Check class code for AA/JR/OP suffix
    if re.search(r'aa$', class_code) or "- aa" in name_lower or name_lower.endswith(" aa"):
        rider_status = "Adult Amateur"
    elif re.search(r'jr$', class_code) or "- jr" in name_lower or name_lower.endswith(" jr") or "junior" in name_lower:
        rider_status = "Junior"
    elif re.search(r'op$', class_code) or "- open" in name_lower or name_lower.endswith(" open"):
        rider_status = "Open"
    elif "- sr" in name_lower or name_lower.endswith(" sr") or re.search(r'sr$', class_code):
        # SR = Senior, treat as Open equivalent
        rider_status = "Open"

    return comp_level, rider_status


def _fetch_show_page(event_id):
    """Fetch show summary page, using cache if available.

    Returns (html, from_cache) tuple.
    """
    cache = _cache_path("show", event_id)
    html = _read_cache(cache)
    if html is not None:
        return html, True

    url = f"{SCOREBOARD_URL}/DressageReport.aspx?EventID={event_id}"
    try:
        resp = session.get(url, timeout=30)
        if resp.status_code != 200:
            return None, False
        _write_cache(cache, resp.text)
        return resp.text, False
    except requests.RequestException:
        return None, False


def scrape_show_results(event_id, event_name):
    """
    Scrape the DressageReport page for a show.

    Returns (classes, from_cache) where classes is a list of dicts with:
    class_name, rider_count, comp_level, rider_status, class_id.
    """
    html, from_cache = _fetch_show_page(event_id)
    if html is None:
        print(f"    Failed to fetch {event_name}")
        return [], False

    soup = BeautifulSoup(html, "lxml")

    # Each class is wrapped in: div.resultoutbl (has onclick) > table.resulttbl
    classes = []
    for wrapper in soup.find_all("div", class_="resultoutbl"):
        table = wrapper.find("table", class_="resulttbl")
        if not table:
            continue

        rows = table.find_all("tr")
        if len(rows) < 2:
            continue

        class_name = rows[0].text.strip()
        rider_text = rows[1].text.strip()

        match = re.search(r'Riders:\s*(\d+)', rider_text)
        if not match:
            continue

        rider_count = int(match.group(1))
        comp_level, rider_status = classify_class_entry(class_name)

        # Extract ClassID from gotonextpage(classid, eventid) on the wrapper div
        class_id = None
        onclick = wrapper.get("onclick", "")
        onclick_match = re.search(r'gotonextpage\(\s*(\d+)', onclick)
        if onclick_match:
            class_id = onclick_match.group(1)

        classes.append({
            "class_name": class_name,
            "rider_count": rider_count,
            "comp_level": comp_level,
            "rider_status": rider_status,
            "class_id": class_id,
        })

    return classes, from_cache


# ---------------------------------------------------------------------------
# Phase 2: Per-class rider detail scraping
# ---------------------------------------------------------------------------

def _fetch_class_page(event_id, class_id):
    """Fetch class detail page, using cache if available.

    Returns (html, from_cache) tuple.
    """
    cache = _cache_path("class", event_id, class_id)
    html = _read_cache(cache)
    if html is not None:
        return html, True

    url = f"{SCOREBOARD_URL}/NewDressageReportClass.aspx?ClassID={class_id}&EventID={event_id}"
    try:
        resp = session.get(url, timeout=30)
        if resp.status_code != 200:
            return None, False
        _write_cache(cache, resp.text)
        _delay_between_classes()
        return resp.text, False
    except requests.RequestException:
        return None, False


def scrape_class_detail(event_id, class_id):
    """
    Fetch per-class detail page and extract per-rider results.

    Returns (riders, from_cache) where riders is a list of dicts with:
    placement, score, rider_name, horse_name, bridle_number, status.
    """
    html, from_cache = _fetch_class_page(event_id, class_id)
    if html is None:
        return [], False

    soup = BeautifulSoup(html, "lxml")
    result_table = soup.find("table", class_="resulttbl")
    if not result_table:
        return []

    riders = []
    rows = result_table.find_all("tr")
    for row in rows:
        # Look for rider hidden input — this identifies a rider row
        rider_input = row.find("input", {"id": re.compile(r'^rider_')})
        if not rider_input:
            continue

        rider_name = rider_input.get("value", "").strip()
        horse_input = row.find("input", {"id": re.compile(r'^horse_')})
        horse_name = horse_input.get("value", "").strip() if horse_input else ""

        # Placement
        place_el = row.find("span", {"id": re.compile(r'lblDRPlace')})
        placement = place_el.text.strip() if place_el else ""

        # Score (%)
        score_el = row.find("span", {"id": re.compile(r'lblDRGood')})
        score = score_el.text.strip() if score_el else ""

        # Bridle number
        bridle_el = row.find("span", class_="numberclass")
        bridle_number = bridle_el.text.strip() if bridle_el else ""

        # Status (SCR, ELIM, etc.)
        status_el = row.find("input", {"id": re.compile(r'lblDRStatus')})
        status = status_el.get("value", "").strip() if status_el else ""

        riders.append({
            "placement": placement,
            "score": score,
            "rider_name": rider_name,
            "horse_name": horse_name,
            "bridle_number": bridle_number,
            "status": status,
        })

    return riders, from_cache


# ---------------------------------------------------------------------------
# Progress / ETA
# ---------------------------------------------------------------------------

def _format_eta(seconds):
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{seconds / 60:.0f}m"
    hours = seconds / 3600
    return f"{hours:.1f}h"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Scrape CompeteEasy for Ontario dressage results."
    )
    parser.add_argument(
        "--skip-detail", action="store_true",
        help="Run phase 1 only (show/class discovery, no per-rider detail)."
    )
    parser.add_argument(
        "--clear-cache", action="store_true",
        help="Clear the HTTP cache before running."
    )
    args = parser.parse_args()

    if args.clear_cache:
        clear_cache()

    _ensure_cache_dir()

    # -----------------------------------------------------------------------
    # Phase 1: Show discovery and class-level summary
    # -----------------------------------------------------------------------
    all_shows = get_all_dressage_shows()

    # Filter for Ontario shows in the date range
    ontario_shows = []
    for show in all_shows:
        if not is_ontario_show(show["name"]):
            continue

        show_date = parse_show_date(show["name"])
        if show_date:
            show["date"] = show_date
            show["year"] = show_date.year
            if YEAR_START <= show_date.year <= YEAR_END:
                ontario_shows.append(show)
        else:
            year_match = re.search(r'20(2[1-6])', show["name"])
            if year_match:
                year = int("20" + year_match.group(1))
                show["year"] = year
                show["date"] = None
                if YEAR_START <= year <= YEAR_END:
                    ontario_shows.append(show)

    print(f"\nOntario dressage shows in {YEAR_START}-{YEAR_END}: {len(ontario_shows)}")
    for s in ontario_shows:
        date_str = s.get("date", "").strftime("%Y-%m-%d") if s.get("date") else "unknown"
        print(f"  [{s['id']}] {s['name']} (year={s.get('year')}, date={date_str})")

    # Scrape class-level summary for each show
    all_results = []
    total_shows = len(ontario_shows)
    for i, show in enumerate(ontario_shows):
        print(f"\n[{i+1}/{total_shows}] Scraping: {show['name']}")
        classes, from_cache = scrape_show_results(show["id"], show["name"])
        print(f"  Found {len(classes)} classes")

        total_riders = sum(c["rider_count"] for c in classes)
        print(f"  Total riders/entries: {total_riders}")

        for c in classes:
            all_results.append({
                "show_id": show["id"],
                "show_name": show["name"],
                "year": show.get("year"),
                "date": show.get("date").isoformat() if show.get("date") else None,
                "class_name": c["class_name"],
                "class_id": c["class_id"],
                "rider_count": c["rider_count"],
                "comp_level": c["comp_level"],
                "rider_status": c["rider_status"],
            })

        if not from_cache:
            time.sleep(0.3)

    # Save raw results (JSON)
    with open("ontario_dressage_raw_results.json", "w") as f:
        json.dump(all_results, f, indent=2)
    print(f"\nSaved {len(all_results)} class entries to ontario_dressage_raw_results.json")

    # Save raw results (CSV — class-level)
    with open("ontario_dressage_raw_results.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Show ID", "Show Name", "Year", "Date", "Class Name",
                         "Class ID", "Rider Count", "Competition Level", "Rider Status"])
        for r in all_results:
            writer.writerow([r["show_id"], r["show_name"], r["year"], r["date"],
                             r["class_name"], r["class_id"], r["rider_count"],
                             r["comp_level"], r["rider_status"]])
    print(f"Saved {len(all_results)} class entries to ontario_dressage_raw_results.csv")

    # -----------------------------------------------------------------------
    # Phase 2: Per-rider detail scraping
    # -----------------------------------------------------------------------
    if not args.skip_detail:
        # Build list of (show_info, class_info) pairs that have ClassIDs
        detail_tasks = []
        for r in all_results:
            if r["class_id"]:
                detail_tasks.append(r)

        total_classes = len(detail_tasks)
        print(f"\n{'='*60}")
        print(f"Phase 2: Fetching per-rider detail for {total_classes} classes")
        print(f"{'='*60}")

        rider_results = []
        phase2_start = time.time()
        last_fetched_show_id = None

        for idx, r in enumerate(detail_tasks):
            # Progress + ETA
            elapsed = time.time() - phase2_start
            if idx > 0:
                avg_per_class = elapsed / idx
                remaining = avg_per_class * (total_classes - idx)
                eta_str = _format_eta(remaining)
            else:
                eta_str = "calculating..."

            # Find show index for display
            show_classes = [t for t in detail_tasks if t["show_id"] == r["show_id"]]
            class_num = next(
                (j + 1 for j, t in enumerate(show_classes) if t["class_id"] == r["class_id"]),
                "?"
            )
            show_class_total = len(show_classes)

            print(
                f"  [{idx+1}/{total_classes}] {r['show_name'][:50]} "
                f"(class {class_num}/{show_class_total}) "
                f"[est. {eta_str} remaining]"
            )

            # Inter-show delay (only when actually fetching from server)
            if last_fetched_show_id is not None and r["show_id"] != last_fetched_show_id:
                _delay_between_shows()

            riders, from_cache = scrape_class_detail(r["show_id"], r["class_id"])
            if not from_cache:
                last_fetched_show_id = r["show_id"]
            for rider in riders:
                rider_results.append({
                    "show_id": r["show_id"],
                    "show_name": r["show_name"],
                    "year": r["year"],
                    "date": r["date"],
                    "class_name": r["class_name"],
                    "comp_level": r["comp_level"],
                    "rider_status": r["rider_status"],
                    "placement": rider["placement"],
                    "score": rider["score"],
                    "rider_name": rider["rider_name"],
                    "horse_name": rider["horse_name"],
                    "bridle_number": rider["bridle_number"],
                    "status": rider["status"],
                })

        # Save rider-level CSV
        with open("ontario_dressage_rider_results.csv", "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "Show ID", "Show Name", "Year", "Date", "Class Name",
                "Competition Level", "Rider Status", "Placement", "Score (%)",
                "Rider Name", "Horse Name", "Bridle Number", "Status",
            ])
            for r in rider_results:
                writer.writerow([
                    r["show_id"], r["show_name"], r["year"], r["date"],
                    r["class_name"], r["comp_level"], r["rider_status"],
                    r["placement"], r["score"], r["rider_name"],
                    r["horse_name"], r["bridle_number"], r["status"],
                ])

        elapsed_total = time.time() - phase2_start
        print(f"\nSaved {len(rider_results)} rider entries to ontario_dressage_rider_results.csv")
        print(f"Phase 2 completed in {_format_eta(elapsed_total)}")

    # -----------------------------------------------------------------------
    # Summary / analysis (same as before)
    # -----------------------------------------------------------------------
    print("\n" + "=" * 80)
    print("ANALYSIS: Ontario Dressage Entries by Year, Competition Level, and Rider Status")
    print("=" * 80)

    summary = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    shows_per_year = defaultdict(set)

    for r in all_results:
        year = r["year"]
        comp = r["comp_level"]
        rider = r["rider_status"]
        count = r["rider_count"]
        summary[year][comp][rider] += count
        shows_per_year[r["year"]].add(r["show_id"])

    comp_levels = ["Bronze", "Silver", "Gold", "CADORA", "Non-Competing", "Unknown"]
    rider_statuses = ["Junior", "Adult Amateur", "Open", "Unspecified"]

    for year in sorted(summary.keys()):
        print(f"\n{'='*60}")
        print(f"YEAR: {year}  ({len(shows_per_year[year])} shows)")
        print(f"{'='*60}")

        year_total = 0
        for comp in comp_levels:
            if comp not in summary[year]:
                continue
            comp_total = sum(summary[year][comp].values())
            year_total += comp_total
            print(f"\n  {comp} (subtotal: {comp_total} entries)")
            for rider in rider_statuses:
                count = summary[year][comp].get(rider, 0)
                if count > 0:
                    print(f"    {rider:20s}: {count:5d}")

        print(f"\n  YEAR TOTAL: {year_total} entries across {len(shows_per_year[year])} shows")

    # Grand totals
    print(f"\n{'='*60}")
    print("GRAND TOTALS (All Years Combined)")
    print(f"{'='*60}")

    grand_total = defaultdict(lambda: defaultdict(int))
    for year in summary:
        for comp in summary[year]:
            for rider in summary[year][comp]:
                grand_total[comp][rider] += summary[year][comp][rider]

    overall_total = 0
    for comp in comp_levels:
        if comp not in grand_total:
            continue
        comp_total = sum(grand_total[comp].values())
        overall_total += comp_total
        print(f"\n  {comp} (subtotal: {comp_total} entries)")
        for rider in rider_statuses:
            count = grand_total[comp].get(rider, 0)
            if count > 0:
                print(f"    {rider:20s}: {count:5d}")

    print(f"\n  OVERALL TOTAL: {overall_total} entries across {sum(len(v) for v in shows_per_year.values())} show-instances")

    # Save summary CSV
    with open("ontario_dressage_summary.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Year", "Competition Level", "Rider Status", "Entry Count", "Show Count"])
        for year in sorted(summary.keys()):
            for comp in comp_levels:
                for rider in rider_statuses:
                    count = summary[year][comp].get(rider, 0)
                    if count > 0:
                        writer.writerow([year, comp, rider, count, len(shows_per_year[year])])

    print("\nSaved summary to ontario_dressage_summary.csv")


if __name__ == "__main__":
    main()
