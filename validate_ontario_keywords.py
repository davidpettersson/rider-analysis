#!/usr/bin/env python3
"""
Validate Ontario keyword list against the CompeteEasy Nominate page.

Fetches the Nominate page filtered to ON province + Dressage discipline,
extracts show/organizer names, and flags any not covered by the current
ONTARIO_KEYWORDS list in scrape_ontario_dressage.py.

Run periodically to catch new Ontario show series.
"""

import re

import requests
from bs4 import BeautifulSoup

from scrape_ontario_dressage import ONTARIO_KEYWORDS, EXCLUDE_KEYWORDS, is_ontario_show

BASE_URL = "https://www.competeeasy.com/Equest"

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
})


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


def get_nominate_ontario_shows():
    """
    Fetch the Nominate page filtered to Ontario + Dressage and extract show names.

    The Nominate page lists upcoming/current shows and has a province dropdown
    (ctl00_ddlvenuestates). We filter to ON to discover Ontario show organizers.
    """
    print("Loading Nominate page...")
    resp = session.get(f"{BASE_URL}/Nominate.aspx")
    soup = BeautifulSoup(resp.text, "lxml")
    asp_fields = get_asp_fields(soup)

    # Post back with ON province filter and Dressage discipline
    post_data = {
        "ctl00_ctl00_ScriptManager1_HiddenField": asp_fields.get("ctl00_ctl00_ScriptManager1_HiddenField", ""),
        "__EVENTTARGET": "ctl00$ddlvenuestates",
        "__EVENTARGUMENT": "",
        "__LASTFOCUS": "",
        "__VIEWSTATE": asp_fields.get("__VIEWSTATE", ""),
        "__VIEWSTATEGENERATOR": asp_fields.get("__VIEWSTATEGENERATOR", ""),
        "__EVENTVALIDATION": asp_fields.get("__EVENTVALIDATION", ""),
        "ctl00$ddlvenuestates": "ON",
        "eventlist": "2",  # Dressage
    }

    print("Filtering to Ontario + Dressage...")
    resp2 = session.post(f"{BASE_URL}/Nominate.aspx", data=post_data)
    soup2 = BeautifulSoup(resp2.text, "lxml")

    # Extract show names from the nominate listing
    # Shows appear in event containers with show name and "State: ON"
    shows = []

    # Look for event name elements — these vary by page structure
    # Try common patterns: links with event names, divs with event titles
    for link in soup2.find_all("a", href=re.compile(r'EventID=\d+')):
        text = link.text.strip()
        if text and len(text) > 3:
            shows.append(text)

    # Also check for table cells or spans that contain event info
    for el in soup2.find_all(string=re.compile(r'State:\s*ON')):
        parent = el.find_parent("tr") or el.find_parent("div")
        if parent:
            # Get all text in this row/container for the show name
            full_text = parent.get_text(separator=" ").strip()
            shows.append(full_text)

    # Deduplicate
    seen = set()
    unique_shows = []
    for s in shows:
        normalized = s.strip().lower()
        if normalized not in seen:
            seen.add(normalized)
            unique_shows.append(s.strip())

    return unique_shows


def main():
    nominate_shows = get_nominate_ontario_shows()

    if not nominate_shows:
        print("\nNo shows found on the Nominate page for ON + Dressage.")
        print("(This may be normal if there are no upcoming dressage shows listed.)")
        return

    print(f"\nFound {len(nominate_shows)} show entries on Nominate page (ON + Dressage):")
    for s in nominate_shows:
        print(f"  {s}")

    # Check which ones are NOT matched by current keywords
    print(f"\n{'='*60}")
    print("Checking against current ONTARIO_KEYWORDS...")
    print(f"{'='*60}")

    unmatched = []
    matched = []
    for show_text in nominate_shows:
        if is_ontario_show(show_text):
            matched.append(show_text)
        else:
            unmatched.append(show_text)

    if matched:
        print(f"\nMatched by current keywords ({len(matched)}):")
        for s in matched:
            print(f"  [OK] {s}")

    if unmatched:
        print(f"\nNOT matched by current keywords ({len(unmatched)}):")
        for s in unmatched:
            print(f"  [MISSING] {s}")
        print("\nConsider adding keywords for the above shows to ONTARIO_KEYWORDS.")
    else:
        print("\nAll Ontario shows are covered by current keywords.")


if __name__ == "__main__":
    main()
