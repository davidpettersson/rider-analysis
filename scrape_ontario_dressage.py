#!/usr/bin/env python3
"""
Scrape CompeteEasy for Ontario dressage show entry data.
Collects entries by competition level (Bronze/Silver/Gold) and
rider status (Junior/Adult Amateur/Open) over the last 5 years.
"""

import requests
from bs4 import BeautifulSoup
import re
import json
import time
import csv
from collections import defaultdict
from datetime import datetime

BASE_URL = "https://www.competeeasy.com/Equest"
SCOREBOARD_URL = "https://www.competeeasy.com/scoreboard/results/Web"

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


def scrape_show_results(event_id, event_name):
    """Scrape the DressageReport page for a show to get class entries."""
    url = f"{SCOREBOARD_URL}/DressageReport.aspx?EventID={event_id}"
    try:
        resp = session.get(url, timeout=30)
        if resp.status_code != 200:
            print(f"    HTTP {resp.status_code} for {event_name}")
            return []
    except requests.RequestException as e:
        print(f"    Error fetching {event_name}: {e}")
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    tables = soup.find_all("table")

    classes = []
    for table in tables:
        rows = table.find_all("tr")
        if len(rows) >= 2:
            # First row has the class name, second has rider count
            class_name_el = rows[0]
            rider_count_el = rows[1]

            class_name = class_name_el.text.strip()
            rider_text = rider_count_el.text.strip()

            # Extract rider count
            match = re.search(r'Riders:\s*(\d+)', rider_text)
            if match:
                rider_count = int(match.group(1))
                comp_level, rider_status = classify_class_entry(class_name)
                classes.append({
                    "class_name": class_name,
                    "rider_count": rider_count,
                    "comp_level": comp_level,
                    "rider_status": rider_status,
                })

    return classes


def main():
    # Step 1: Get all dressage shows
    all_shows = get_all_dressage_shows()

    # Step 2: Filter for Ontario shows in the last 5 years
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
            # If we can't parse the date, try to extract year from name
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

    # Step 3: Scrape each show's results
    all_results = []
    total = len(ontario_shows)
    for i, show in enumerate(ontario_shows):
        print(f"\n[{i+1}/{total}] Scraping: {show['name']}")
        classes = scrape_show_results(show["id"], show["name"])
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
                "rider_count": c["rider_count"],
                "comp_level": c["comp_level"],
                "rider_status": c["rider_status"],
            })

        # Be polite to the server
        time.sleep(0.3)

    # Step 4: Save raw results
    with open("/home/user/rider-analysis/ontario_dressage_raw_results.json", "w") as f:
        json.dump(all_results, f, indent=2)
    print(f"\nSaved {len(all_results)} class entries to ontario_dressage_raw_results.json")

    # Step 5: Aggregate and analyze
    print("\n" + "=" * 80)
    print("ANALYSIS: Ontario Dressage Entries by Year, Competition Level, and Rider Status")
    print("=" * 80)

    # By year -> comp_level -> rider_status -> total entries
    summary = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    show_counts = defaultdict(int)

    for r in all_results:
        year = r["year"]
        comp = r["comp_level"]
        rider = r["rider_status"]
        count = r["rider_count"]
        summary[year][comp][rider] += count
        show_counts[year] += 0  # just to track years

    # Count unique shows per year
    shows_per_year = defaultdict(set)
    for r in all_results:
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

    # Save summary as CSV
    with open("/home/user/rider-analysis/ontario_dressage_summary.csv", "w", newline="") as f:
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
