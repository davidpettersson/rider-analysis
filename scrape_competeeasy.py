#!/usr/bin/env python3
"""
Scrape CompeteEasy Equest portal for Ontario dressage show entry data.

This script navigates the CompeteEasy ASP.NET WebForms portal to collect
dressage competition entries in Ontario over the last 5 years, categorized by:
- Competition status: Bronze, Silver, Gold
- Rider status: Junior, Adult Amateur, Open

Usage:
    python3 scrape_competeeasy.py [--output results.csv] [--years 5]

The script handles ASP.NET ViewState/postback mechanics and rate-limits
requests to be respectful to the server.
"""

import argparse
import csv
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timedelta
from urllib.parse import urljoin, urlencode

import requests
from bs4 import BeautifulSoup

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

BASE_URL = "https://www.competeeasy.com"
RESULTS_URL = f"{BASE_URL}/Equest/Results.aspx"
SHOWS_URL = f"{BASE_URL}/Equest/Nominate.aspx"
EVENT_URL = f"{BASE_URL}/Equest/Event.aspx"
ENTRY_STATUS_URL = f"{BASE_URL}/Equest/EntryStatus.aspx"

# Reasonable delay between requests (seconds)
REQUEST_DELAY = 1.5

# Competition level keywords to look for in event names/descriptions
COMPETITION_LEVELS = {
    "Gold": ["gold", "cdi", "cdi1*", "cdi2*", "cdi3*", "cdi4*", "cdi5*", "platinum"],
    "Silver": ["silver"],
    "Bronze": ["bronze", "schooling"],
}

# Rider status patterns found in class names
RIDER_STATUS_PATTERNS = {
    "Junior": [
        r"\bjunior\b", r"\bjr\b", r"\byoung\s*rider\b", r"\byr\b",
        r"\bjr/yr\b", r"\byouth\b", r"\bpony\b", r"\bchildren\b",
    ],
    "Adult Amateur": [
        r"\badult\s*amateur\b", r"\baa\b", r"\bamateur\b", r"\bad\.\s*am\b",
        r"\badult\s*am\b",
    ],
    "Open": [
        r"\bopen\b", r"\bfreestyle\b", r"\bgrand\s*prix\b", r"\bprix\s*st\b",
        r"\bintermediate\b",
    ],
}

# Ontario location indicators
ONTARIO_INDICATORS = [
    "ontario", ", on", "(on)", "on,", "caledon", "palgrave", "ottawa",
    "toronto", "hamilton", "london", "kingston", "barrie", "guelph",
    "kitchener", "waterloo", "niagara", "angelstone", "palgrave",
    "cedar valley", "maplewood", "wesley clover", "thunderbird",
    "gingerwood", "ashton", "brooklin", "uxbridge", "orangeville",
    "mono", "kawartha", "peterborough", "brampton", "mississauga",
    "oakville", "burlington", "stouffville", "newmarket", "innisfil",
    "orillia", "collingwood", "elora", "fergus", "dunrobin",
]


class CompeteEasyScraper:
    """Scraper for the CompeteEasy Equest portal."""

    def __init__(self, delay: float = REQUEST_DELAY):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;"
                "q=0.9,image/webp,*/*;q=0.8"
            ),
            "Accept-Language": "en-US,en;q=0.5",
        })
        self.delay = delay
        self._last_request_time = 0

    def _rate_limit(self):
        """Enforce minimum delay between requests."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)
        self._last_request_time = time.time()

    def _get(self, url: str, **kwargs) -> requests.Response:
        """GET request with rate limiting and retries."""
        self._rate_limit()
        for attempt in range(3):
            try:
                resp = self.session.get(url, timeout=30, **kwargs)
                resp.raise_for_status()
                return resp
            except requests.RequestException as e:
                logger.warning(f"GET {url} attempt {attempt+1} failed: {e}")
                if attempt < 2:
                    time.sleep(2 ** (attempt + 1))
        raise RuntimeError(f"Failed to GET {url} after 3 attempts")

    def _post(self, url: str, data: dict, **kwargs) -> requests.Response:
        """POST request with rate limiting and retries."""
        self._rate_limit()
        for attempt in range(3):
            try:
                resp = self.session.post(url, data=data, timeout=30, **kwargs)
                resp.raise_for_status()
                return resp
            except requests.RequestException as e:
                logger.warning(f"POST {url} attempt {attempt+1} failed: {e}")
                if attempt < 2:
                    time.sleep(2 ** (attempt + 1))
        raise RuntimeError(f"Failed to POST {url} after 3 attempts")

    def _extract_aspnet_fields(self, soup: BeautifulSoup) -> dict:
        """Extract ASP.NET hidden form fields (__VIEWSTATE, etc.)."""
        fields = {}
        for name in [
            "__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION",
            "__EVENTTARGET", "__EVENTARGUMENT", "__LASTFOCUS",
        ]:
            tag = soup.find("input", {"name": name})
            if tag:
                fields[name] = tag.get("value", "")
        return fields

    def _extract_shows_from_page(self, soup: BeautifulSoup) -> list[dict]:
        """Extract show listings from a Shows/Nominate page."""
        shows = []
        # Look for event links with GUIDs
        for link in soup.find_all("a", href=True):
            href = link["href"]
            if "Event.aspx" in href and "e=" in href:
                guid_match = re.search(r"e=([A-Fa-f0-9]{32})", href)
                if guid_match:
                    guid = guid_match.group(1)
                    show_name = link.get_text(strip=True)
                    shows.append({
                        "guid": guid,
                        "name": show_name,
                        "url": urljoin(BASE_URL, href),
                    })
        return shows

    def get_dressage_shows(self, years: int = 5) -> list[dict]:
        """
        Retrieve dressage show listings from the Equest portal.

        Navigates the Shows page and Results page filtering by Dressage
        discipline to find Ontario competitions over the specified years.
        """
        all_shows = []
        cutoff_date = datetime.now() - timedelta(days=years * 365)

        # Strategy 1: Navigate the Shows listing page
        logger.info("Fetching shows listing page...")
        try:
            resp = self._get(f"{SHOWS_URL}?eventlist=2")
            soup = BeautifulSoup(resp.text, "lxml")
            shows = self._extract_shows_from_page(soup)
            logger.info(f"Found {len(shows)} shows on listing page")

            # Try to find and use the discipline filter for Dressage
            aspnet_fields = self._extract_aspnet_fields(soup)

            # Look for discipline dropdown
            discipline_select = soup.find(
                "select", {"id": re.compile(r".*[Dd]iscipline.*", re.I)}
            )
            if not discipline_select:
                # Try broader search
                discipline_select = soup.find(
                    "select", {"name": re.compile(r".*[Dd]iscipline.*", re.I)}
                )

            if discipline_select:
                logger.info("Found discipline dropdown, filtering for Dressage...")
                dressage_option = None
                for opt in discipline_select.find_all("option"):
                    if "dressage" in opt.get_text(strip=True).lower():
                        dressage_option = opt.get("value")
                        break

                if dressage_option:
                    post_data = aspnet_fields.copy()
                    post_data[discipline_select["name"]] = dressage_option
                    post_data["__EVENTTARGET"] = discipline_select.get("name", "")
                    resp = self._post(SHOWS_URL, data=post_data)
                    soup = BeautifulSoup(resp.text, "lxml")
                    shows = self._extract_shows_from_page(soup)
                    logger.info(
                        f"Found {len(shows)} dressage shows after filtering"
                    )

            all_shows.extend(shows)

            # Look for pagination and navigate additional pages
            self._navigate_pagination(soup, SHOWS_URL, all_shows)

        except Exception as e:
            logger.error(f"Error fetching shows listing: {e}")

        # Strategy 2: Use the Results page to find past competitions
        logger.info("Fetching results page for past competitions...")
        try:
            resp = self._get(RESULTS_URL)
            soup = BeautifulSoup(resp.text, "lxml")

            # Look for search-by-show option and discipline filter
            aspnet_fields = self._extract_aspnet_fields(soup)

            # Find all form controls to understand the page structure
            all_selects = soup.find_all("select")
            all_inputs = soup.find_all("input", {"type": ["text", "submit"]})

            logger.info(f"Results page has {len(all_selects)} dropdowns, "
                        f"{len(all_inputs)} text/submit inputs")
            for sel in all_selects:
                sel_name = sel.get("name", sel.get("id", "unknown"))
                options = [
                    opt.get_text(strip=True)
                    for opt in sel.find_all("option")[:10]
                ]
                logger.info(f"  Dropdown '{sel_name}': {options}")

            # Try to set discipline to Dressage and search by Show
            for sel in all_selects:
                sel_name = sel.get("name", "").lower()
                if "discipline" in sel_name or "disc" in sel_name:
                    for opt in sel.find_all("option"):
                        opt_text = opt.get_text(strip=True).lower()
                        if opt_text == "dressage":
                            post_data = aspnet_fields.copy()
                            post_data[sel["name"]] = opt["value"]
                            post_data["__EVENTTARGET"] = sel["name"]
                            resp = self._post(RESULTS_URL, data=post_data)
                            soup = BeautifulSoup(resp.text, "lxml")
                            result_shows = self._extract_shows_from_page(soup)
                            logger.info(
                                f"Found {len(result_shows)} shows from results"
                            )
                            all_shows.extend(result_shows)
                            break

        except Exception as e:
            logger.error(f"Error fetching results page: {e}")

        # Deduplicate by GUID
        seen = set()
        unique_shows = []
        for show in all_shows:
            if show["guid"] not in seen:
                seen.add(show["guid"])
                unique_shows.append(show)

        logger.info(f"Total unique shows found: {len(unique_shows)}")
        return unique_shows

    def _navigate_pagination(self, soup, base_url, shows_list):
        """Follow pagination links to get all pages of results."""
        page = 1
        max_pages = 50  # Safety limit

        while page < max_pages:
            # Look for "Next" or page number links
            next_link = None
            pager = soup.find("div", class_=re.compile(r"pag", re.I))
            if not pager:
                pager = soup.find("table", class_=re.compile(r"pag", re.I))
            if not pager:
                # Try looking for GridView pager
                pager = soup.find("tr", class_=re.compile(r"pag", re.I))

            if pager:
                links = pager.find_all("a", href=True)
                for link in links:
                    text = link.get_text(strip=True).lower()
                    if text in ("next", "›", "»", ">", "..."):
                        next_link = link
                        break
                    # Or look for the next page number
                    try:
                        link_page = int(text)
                        if link_page == page + 1:
                            next_link = link
                            break
                    except ValueError:
                        continue

            if not next_link:
                break

            # ASP.NET postback for pagination
            href = next_link.get("href", "")
            postback_match = re.search(
                r"__doPostBack\('([^']+)','([^']*)'\)", href
            )
            if postback_match:
                event_target = postback_match.group(1)
                event_arg = postback_match.group(2)

                aspnet_fields = self._extract_aspnet_fields(soup)
                post_data = aspnet_fields.copy()
                post_data["__EVENTTARGET"] = event_target
                post_data["__EVENTARGUMENT"] = event_arg

                try:
                    resp = self._post(base_url, data=post_data)
                    soup = BeautifulSoup(resp.text, "lxml")
                    new_shows = self._extract_shows_from_page(soup)
                    shows_list.extend(new_shows)
                    page += 1
                    logger.info(f"Page {page}: found {len(new_shows)} more shows")
                except Exception as e:
                    logger.warning(f"Pagination failed at page {page}: {e}")
                    break
            else:
                break

    def get_event_details(self, guid: str) -> dict:
        """
        Fetch details for a specific event/competition.

        Returns competition level, location, date, and class information.
        """
        url = f"{EVENT_URL}?e={guid}&eventlist=2"
        try:
            resp = self._get(url)
            soup = BeautifulSoup(resp.text, "lxml")

            details = {
                "guid": guid,
                "url": url,
                "name": "",
                "location": "",
                "province": "",
                "date_start": "",
                "date_end": "",
                "competition_level": "Unknown",
                "classes": [],
                "is_ontario": False,
                "is_dressage": False,
                "raw_text": "",
            }

            # Extract event title
            title_tag = soup.find("h1") or soup.find("h2")
            if title_tag:
                details["name"] = title_tag.get_text(strip=True)

            # Get all text content for keyword analysis
            body_text = soup.get_text(" ", strip=True).lower()
            details["raw_text"] = body_text[:5000]

            # Check if it's in Ontario
            for indicator in ONTARIO_INDICATORS:
                if indicator.lower() in body_text:
                    details["is_ontario"] = True
                    details["province"] = "ON"
                    break

            # Check if it's a dressage event
            if "dressage" in body_text:
                details["is_dressage"] = True

            # Determine competition level
            details["competition_level"] = self._classify_competition_level(
                details["name"], body_text
            )

            # Extract dates
            date_patterns = [
                r"(\w+ \d{1,2}(?:\s*[-–]\s*\d{1,2})?,?\s*\d{4})",
                r"(\d{1,2}/\d{1,2}/\d{4})",
                r"(\d{4}-\d{2}-\d{2})",
            ]
            for pattern in date_patterns:
                match = re.search(pattern, body_text)
                if match:
                    details["date_start"] = match.group(1)
                    break

            # Extract classes/sections
            details["classes"] = self._extract_classes(soup)

            # Try to get location from structured fields
            for label_text in ["venue", "location", "address", "where"]:
                label = soup.find(
                    string=re.compile(label_text, re.I)
                )
                if label:
                    parent = label.find_parent()
                    if parent:
                        next_text = parent.find_next_sibling()
                        if next_text:
                            details["location"] = next_text.get_text(strip=True)
                            break

            return details

        except Exception as e:
            logger.error(f"Error fetching event {guid}: {e}")
            return {"guid": guid, "error": str(e)}

    def _classify_competition_level(self, name: str, body_text: str) -> str:
        """Classify competition as Bronze, Silver, or Gold based on text."""
        combined = f"{name} {body_text}".lower()

        # Check in order of specificity (Gold first since it's more specific)
        for level, keywords in COMPETITION_LEVELS.items():
            for keyword in keywords:
                if keyword in combined:
                    return level

        return "Unknown"

    def _extract_classes(self, soup: BeautifulSoup) -> list[dict]:
        """Extract competition classes and their rider status categories."""
        classes = []

        # Look for tables with class information
        tables = soup.find_all("table")
        for table in tables:
            rows = table.find_all("tr")
            for row in rows:
                cells = row.find_all(["td", "th"])
                row_text = " ".join(c.get_text(strip=True) for c in cells)

                if any(
                    kw in row_text.lower()
                    for kw in ["dressage", "test", "level", "training",
                               "first", "second", "third", "fourth",
                               "prix st", "grand prix", "intermediate",
                               "preliminary", "novice"]
                ):
                    rider_status = self._classify_rider_status(row_text)
                    classes.append({
                        "name": row_text[:200],
                        "rider_status": rider_status,
                    })

        # Also look for lists with class information
        for li in soup.find_all("li"):
            li_text = li.get_text(strip=True)
            if any(
                kw in li_text.lower()
                for kw in ["dressage", "test", "level"]
            ):
                rider_status = self._classify_rider_status(li_text)
                classes.append({
                    "name": li_text[:200],
                    "rider_status": rider_status,
                })

        return classes

    def _classify_rider_status(self, text: str) -> str:
        """Classify rider status from class name text."""
        text_lower = text.lower()
        for status, patterns in RIDER_STATUS_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, text_lower):
                    return status
        return "Open"  # Default to Open if no specific category detected

    def get_entry_status(self, guid: str) -> dict:
        """
        Fetch entry status page for a competition to count entries.

        Returns entry counts broken down by rider status.
        """
        url = f"{ENTRY_STATUS_URL}?e={guid}"
        try:
            resp = self._get(url)
            soup = BeautifulSoup(resp.text, "lxml")

            entries = {
                "total_entries": 0,
                "by_rider_status": {
                    "Junior": 0,
                    "Adult Amateur": 0,
                    "Open": 0,
                },
                "riders": [],
            }

            # Look for entry tables
            tables = soup.find_all("table")
            for table in tables:
                rows = table.find_all("tr")
                for row in rows[1:]:  # Skip header
                    cells = row.find_all("td")
                    if len(cells) >= 2:
                        row_text = " ".join(
                            c.get_text(strip=True) for c in cells
                        )

                        # Each row is typically one entry
                        entries["total_entries"] += 1

                        rider_status = self._classify_rider_status(row_text)
                        entries["by_rider_status"][rider_status] += 1

                        rider_info = {
                            "text": row_text[:300],
                            "rider_status": rider_status,
                        }
                        entries["riders"].append(rider_info)

            # Also look for entry count text on the page
            body_text = soup.get_text(" ", strip=True)
            count_match = re.search(
                r"(\d+)\s*(?:entries|riders|horses|competitors)", body_text, re.I
            )
            if count_match and entries["total_entries"] == 0:
                entries["total_entries"] = int(count_match.group(1))

            return entries

        except Exception as e:
            logger.error(f"Error fetching entry status for {guid}: {e}")
            return {"error": str(e)}

    def scrape_all(self, years: int = 5) -> list[dict]:
        """
        Main scraping workflow: find shows, get details, count entries.

        Returns a list of competition records with entry data.
        """
        logger.info(f"Starting scrape for Ontario dressage shows ({years} years)")

        # Step 1: Get all dressage show listings
        shows = self.get_dressage_shows(years=years)

        if not shows:
            logger.warning("No shows found from listing pages.")
            logger.info("Attempting direct event URL approach...")
            shows = self._try_known_event_guids()

        results = []

        # Step 2: Get details for each show
        for i, show in enumerate(shows):
            logger.info(
                f"Processing show {i+1}/{len(shows)}: {show.get('name', show['guid'])}"
            )

            # Get event details
            details = self.get_event_details(show["guid"])

            if details.get("error"):
                logger.warning(f"  Skipping (error): {details['error']}")
                continue

            if not details.get("is_ontario"):
                logger.info(f"  Skipping (not Ontario): {details.get('name')}")
                continue

            if not details.get("is_dressage"):
                logger.info(f"  Skipping (not dressage): {details.get('name')}")
                continue

            # Get entry counts
            entry_data = self.get_entry_status(show["guid"])

            record = {
                "show_name": details.get("name", ""),
                "guid": show["guid"],
                "date": details.get("date_start", ""),
                "location": details.get("location", ""),
                "competition_level": details.get("competition_level", "Unknown"),
                "total_entries": entry_data.get("total_entries", 0),
                "junior_entries": entry_data.get(
                    "by_rider_status", {}
                ).get("Junior", 0),
                "adult_amateur_entries": entry_data.get(
                    "by_rider_status", {}
                ).get("Adult Amateur", 0),
                "open_entries": entry_data.get(
                    "by_rider_status", {}
                ).get("Open", 0),
                "num_classes": len(details.get("classes", [])),
                "url": details.get("url", ""),
            }
            results.append(record)
            logger.info(
                f"  -> {record['competition_level']} | "
                f"Entries: {record['total_entries']} | "
                f"Jr: {record['junior_entries']} "
                f"AA: {record['adult_amateur_entries']} "
                f"Open: {record['open_entries']}"
            )

        logger.info(f"Scraping complete. {len(results)} Ontario dressage shows found.")
        return results

    def _try_known_event_guids(self) -> list[dict]:
        """
        Fallback: try known event GUIDs from web search results.

        These are GUIDs found in search results for Ontario dressage shows
        on CompeteEasy.
        """
        known_guids = [
            # Caledon Dressage at Angelstone July 2023
            ("38625BF431C844F185BF7A79C96763D2",
             "Caledon Dressage at Angelstone - July 2023"),
            # Caledon CDI3* July 2023
            ("3DB0AFC22240447E9CAB89A397D23E44",
             "Caledon CDI3* Dressage at Angelstone - July 2023"),
            # Caledon Dressage in the Park June 2024
            ("66E55C5AA80F46948FA7362F1732993C",
             "Caledon Dressage in the Park - June 2024"),
            # Dressage at Gingerwood June 2024
            ("D67B6775A5204CACB59C5EB59CBE433A",
             "Dressage at Gingerwood June 2024"),
            # Diamond Dressage Series
            ("A46913DB1A204DCFBC20EAF2E0A66759",
             "Diamond Dressage Series"),
            # Dressage Niagara 2023
            ("A91CC094F1F549DCABFD85D0D690B229",
             "Dressage Niagara - 2023 Virtual Series"),
        ]

        shows = []
        for guid, name in known_guids:
            shows.append({"guid": guid, "name": name, "url": ""})

        return shows


def save_results(results: list[dict], output_path: str):
    """Save results to CSV file."""
    if not results:
        logger.warning("No results to save.")
        return

    fieldnames = [
        "show_name", "date", "location", "competition_level",
        "total_entries", "junior_entries", "adult_amateur_entries",
        "open_entries", "num_classes", "url", "guid",
    ]

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    logger.info(f"Results saved to {output_path}")


def generate_summary(results: list[dict]) -> str:
    """Generate a summary analysis of the scraped data."""
    if not results:
        return "No data available for analysis."

    lines = []
    lines.append("=" * 70)
    lines.append("ONTARIO DRESSAGE ENTRY ANALYSIS - CompeteEasy Portal")
    lines.append("=" * 70)
    lines.append("")

    # Overall totals
    total_shows = len(results)
    total_entries = sum(r["total_entries"] for r in results)
    lines.append(f"Total Shows Analyzed: {total_shows}")
    lines.append(f"Total Entries: {total_entries}")
    lines.append("")

    # By Competition Level
    lines.append("-" * 50)
    lines.append("ENTRIES BY COMPETITION LEVEL")
    lines.append("-" * 50)
    for level in ["Bronze", "Silver", "Gold", "Unknown"]:
        level_shows = [r for r in results if r["competition_level"] == level]
        if level_shows:
            count = len(level_shows)
            entries = sum(r["total_entries"] for r in level_shows)
            jr = sum(r["junior_entries"] for r in level_shows)
            aa = sum(r["adult_amateur_entries"] for r in level_shows)
            op = sum(r["open_entries"] for r in level_shows)
            lines.append(f"\n  {level}:")
            lines.append(f"    Shows: {count}")
            lines.append(f"    Total Entries: {entries}")
            lines.append(f"    Junior: {jr}")
            lines.append(f"    Adult Amateur: {aa}")
            lines.append(f"    Open: {op}")

    # By Rider Status
    lines.append("")
    lines.append("-" * 50)
    lines.append("ENTRIES BY RIDER STATUS (ALL LEVELS)")
    lines.append("-" * 50)
    total_jr = sum(r["junior_entries"] for r in results)
    total_aa = sum(r["adult_amateur_entries"] for r in results)
    total_op = sum(r["open_entries"] for r in results)
    lines.append(f"  Junior: {total_jr}")
    lines.append(f"  Adult Amateur: {total_aa}")
    lines.append(f"  Open: {total_op}")

    # Cross-tabulation
    lines.append("")
    lines.append("-" * 50)
    lines.append("CROSS-TABULATION: COMPETITION LEVEL x RIDER STATUS")
    lines.append("-" * 50)
    lines.append(f"{'Level':<12} {'Junior':>10} {'Adult Amateur':>15} {'Open':>10} {'Total':>10}")
    lines.append("-" * 57)
    for level in ["Bronze", "Silver", "Gold"]:
        level_shows = [r for r in results if r["competition_level"] == level]
        if level_shows:
            jr = sum(r["junior_entries"] for r in level_shows)
            aa = sum(r["adult_amateur_entries"] for r in level_shows)
            op = sum(r["open_entries"] for r in level_shows)
            total = jr + aa + op
            lines.append(f"{level:<12} {jr:>10} {aa:>15} {op:>10} {total:>10}")

    lines.append("-" * 57)
    lines.append(
        f"{'TOTAL':<12} {total_jr:>10} {total_aa:>15} {total_op:>10} "
        f"{total_jr + total_aa + total_op:>10}"
    )

    # Year breakdown if dates available
    lines.append("")
    lines.append("-" * 50)
    lines.append("SHOWS BY YEAR")
    lines.append("-" * 50)
    by_year = {}
    for r in results:
        year_match = re.search(r"(20\d{2})", r.get("date", ""))
        year = year_match.group(1) if year_match else "Unknown"
        if year not in by_year:
            by_year[year] = {"shows": 0, "entries": 0}
        by_year[year]["shows"] += 1
        by_year[year]["entries"] += r["total_entries"]

    for year in sorted(by_year.keys()):
        lines.append(
            f"  {year}: {by_year[year]['shows']} shows, "
            f"{by_year[year]['entries']} entries"
        )

    lines.append("")
    lines.append("=" * 70)

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Scrape CompeteEasy for Ontario dressage entry analysis"
    )
    parser.add_argument(
        "--output", "-o",
        default="ontario_dressage_entries.csv",
        help="Output CSV file path (default: ontario_dressage_entries.csv)",
    )
    parser.add_argument(
        "--years", "-y",
        type=int, default=5,
        help="Number of years to look back (default: 5)",
    )
    parser.add_argument(
        "--summary-file", "-s",
        default="analysis_summary.txt",
        help="Output summary text file (default: analysis_summary.txt)",
    )
    parser.add_argument(
        "--json-output", "-j",
        default="ontario_dressage_entries.json",
        help="Output JSON file path (default: ontario_dressage_entries.json)",
    )
    parser.add_argument(
        "--delay",
        type=float, default=REQUEST_DELAY,
        help=f"Delay between requests in seconds (default: {REQUEST_DELAY})",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    scraper = CompeteEasyScraper(delay=args.delay)

    # Run the scrape
    results = scraper.scrape_all(years=args.years)

    # Save outputs
    save_results(results, args.output)

    # Save JSON
    with open(args.json_output, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    logger.info(f"JSON results saved to {args.json_output}")

    # Generate and save summary
    summary = generate_summary(results)
    with open(args.summary_file, "w", encoding="utf-8") as f:
        f.write(summary)
    logger.info(f"Summary saved to {args.summary_file}")

    # Print summary to console
    print("\n" + summary)

    return 0


if __name__ == "__main__":
    sys.exit(main())
