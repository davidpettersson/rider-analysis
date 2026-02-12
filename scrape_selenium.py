#!/usr/bin/env python3
"""
Selenium-based scraper for CompeteEasy Equest portal.

This scraper uses a headless browser to interact with the ASP.NET WebForms
portal, handling JavaScript postbacks, ViewState management, and dynamic
content loading automatically.

This is the recommended approach as CompeteEasy uses ASP.NET WebForms which
relies heavily on __doPostBack() calls for navigation and filtering.

Usage:
    python3 scrape_selenium.py [--output results.csv] [--years 5] [--headed]

Prerequisites:
    pip install selenium beautifulsoup4 pandas
    # Chrome/Chromium and chromedriver must be installed
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

from bs4 import BeautifulSoup

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait, Select
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.common.exceptions import (
        TimeoutException, NoSuchElementException, StaleElementReferenceException,
    )
except ImportError:
    print("ERROR: selenium is required. Install with: pip install selenium")
    sys.exit(1)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

BASE_URL = "https://www.competeeasy.com"
RESULTS_URL = f"{BASE_URL}/Equest/Results.aspx"
SHOWS_URL = f"{BASE_URL}/Equest/Nominate.aspx"
EVENT_URL_TEMPLATE = f"{BASE_URL}/Equest/Event.aspx?e={{guid}}&eventlist=2"
ENTRY_STATUS_URL_TEMPLATE = f"{BASE_URL}/Equest/EntryStatus.aspx?e={{guid}}"

# Minimum delay between page loads (seconds)
PAGE_DELAY = 2.0

# Ontario location indicators
ONTARIO_INDICATORS = [
    "ontario", ", on", " on ", "(on)", " on,",
    "caledon", "palgrave", "ottawa", "toronto", "hamilton",
    "london", "kingston", "barrie", "guelph", "kitchener",
    "waterloo", "niagara", "angelstone", "cedar valley",
    "wesley clover", "gingerwood", "ashton", "brooklin",
    "uxbridge", "orangeville", "mono", "kawartha",
    "peterborough", "brampton", "mississauga", "oakville",
    "burlington", "stouffville", "newmarket", "innisfil",
    "orillia", "collingwood", "elora", "fergus", "dunrobin",
    "kemptville", "smiths falls", "perth", "cornwall",
    "beaverton", "cannington", "lindsay", "cobourg",
    "belleville", "trenton", "sudbury", "thunder bay",
    "north bay", "muskoka", "haliburton", "parry sound",
]

# Competition level classification
COMPETITION_LEVELS = {
    "Gold": [
        "gold", "cdi", "cdi1*", "cdi2*", "cdi3*", "cdi4*", "cdi5*",
        "platinum", "national", "cdiy", "cdij", "cdip",
    ],
    "Silver": ["silver", "provincial"],
    "Bronze": ["bronze", "schooling", "local"],
}

# Rider status patterns
RIDER_STATUS_PATTERNS = {
    "Junior": [
        r"\bjunior\b", r"\bjr\b", r"\byoung\s*rider\b", r"\byr\b",
        r"\bjr/yr\b", r"\bjr\.?\s*/\s*yr\b", r"\byouth\b",
        r"\bchildren\b", r"\bpony\s*rider\b",
    ],
    "Adult Amateur": [
        r"\badult\s*amateur\b", r"\baa\b", r"\bamateur\b",
        r"\bad\.?\s*am\.?\b", r"\badult\s*am\b",
    ],
    "Open": [
        r"\bopen\b",
    ],
}


class SeleniumScraper:
    """Selenium-based scraper for CompeteEasy Equest portal."""

    def __init__(self, headless: bool = True, delay: float = PAGE_DELAY):
        self.delay = delay
        self.headless = headless
        self.driver = None

    def _init_driver(self):
        """Initialize the Selenium WebDriver."""
        options = Options()
        if self.headless:
            options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        options.add_argument(
            "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
        # Suppress logging
        options.add_argument("--log-level=3")
        options.add_experimental_option("excludeSwitches", ["enable-logging"])

        self.driver = webdriver.Chrome(options=options)
        self.driver.implicitly_wait(10)
        logger.info("WebDriver initialized")

    def _wait_for_page_load(self, timeout: int = 15):
        """Wait for page to finish loading."""
        WebDriverWait(self.driver, timeout).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )
        time.sleep(self.delay)

    def _get_soup(self) -> BeautifulSoup:
        """Get BeautifulSoup object from current page."""
        return BeautifulSoup(self.driver.page_source, "lxml")

    def close(self):
        """Close the WebDriver."""
        if self.driver:
            self.driver.quit()
            self.driver = None

    def scrape_shows_listing(self) -> list[dict]:
        """
        Navigate the Shows listing page to find dressage events.

        Returns a list of show dicts with guid, name, and URL.
        """
        shows = []

        logger.info("Navigating to Shows listing page...")
        self.driver.get(f"{SHOWS_URL}?eventlist=2")
        self._wait_for_page_load()

        soup = self._get_soup()

        # Try to find and select "Dressage" in discipline dropdown
        try:
            discipline_selects = self.driver.find_elements(
                By.CSS_SELECTOR, "select"
            )
            for sel_elem in discipline_selects:
                try:
                    select = Select(sel_elem)
                    options_text = [
                        opt.text.strip().lower()
                        for opt in select.options
                    ]
                    if "dressage" in options_text:
                        logger.info("Found discipline dropdown, selecting 'Dressage'")
                        select.select_by_visible_text("Dressage")
                        self._wait_for_page_load()
                        break
                except (StaleElementReferenceException, NoSuchElementException):
                    continue
        except Exception as e:
            logger.warning(f"Could not filter by discipline: {e}")

        # Collect shows from the current and subsequent pages
        page = 1
        while page <= 100:
            soup = self._get_soup()
            page_shows = self._extract_show_links(soup)
            shows.extend(page_shows)
            logger.info(f"Page {page}: found {len(page_shows)} shows")

            # Try to go to next page
            if not self._click_next_page():
                break
            page += 1

        logger.info(f"Total shows from listing: {len(shows)}")
        return shows

    def scrape_results_page(self) -> list[dict]:
        """
        Navigate the Results page to find past dressage events.

        The Results page allows searching by Rider, Horse, or Show
        with discipline filtering.
        """
        shows = []

        logger.info("Navigating to Results page...")
        self.driver.get(RESULTS_URL)
        self._wait_for_page_load()

        soup = self._get_soup()

        # Log all form controls for debugging
        selects = self.driver.find_elements(By.CSS_SELECTOR, "select")
        logger.info(f"Found {len(selects)} dropdown(s) on Results page")

        for sel_elem in selects:
            try:
                sel_id = sel_elem.get_attribute("id") or "unknown"
                sel_name = sel_elem.get_attribute("name") or "unknown"
                select = Select(sel_elem)
                opt_texts = [opt.text.strip() for opt in select.options[:15]]
                logger.info(f"  Dropdown '{sel_id}' ({sel_name}): {opt_texts}")
            except StaleElementReferenceException:
                continue

        # Try selecting "Dressage" discipline
        for sel_elem in selects:
            try:
                select = Select(sel_elem)
                options_text = [opt.text.strip().lower() for opt in select.options]
                if "dressage" in options_text:
                    logger.info("Selecting 'Dressage' discipline on Results page")
                    select.select_by_visible_text("Dressage")
                    self._wait_for_page_load()
                    break
            except (StaleElementReferenceException, NoSuchElementException):
                continue

        # Try selecting "Show" in the search-by dropdown
        selects = self.driver.find_elements(By.CSS_SELECTOR, "select")
        for sel_elem in selects:
            try:
                select = Select(sel_elem)
                options_text = [opt.text.strip().lower() for opt in select.options]
                if "show" in options_text:
                    logger.info("Selecting 'Show' in search-by dropdown")
                    select.select_by_visible_text("Show")
                    self._wait_for_page_load()
                    break
            except (StaleElementReferenceException, NoSuchElementException):
                continue

        # Try clicking search/submit
        try:
            submit_btns = self.driver.find_elements(
                By.CSS_SELECTOR,
                "input[type='submit'], button[type='submit'], "
                "input[type='button'][value*='Search'], "
                "a[href*='Search'], input[value*='Go']",
            )
            if submit_btns:
                logger.info("Clicking search/submit button")
                submit_btns[0].click()
                self._wait_for_page_load()
        except Exception as e:
            logger.warning(f"Could not click search button: {e}")

        # Collect results
        page = 1
        while page <= 100:
            soup = self._get_soup()
            page_shows = self._extract_show_links(soup)
            shows.extend(page_shows)
            logger.info(f"Results page {page}: found {len(page_shows)} shows")

            if not self._click_next_page():
                break
            page += 1

        logger.info(f"Total shows from results: {len(shows)}")
        return shows

    def _extract_show_links(self, soup: BeautifulSoup) -> list[dict]:
        """Extract show links with GUIDs from a page."""
        shows = []
        seen_guids = set()

        for link in soup.find_all("a", href=True):
            href = link["href"]
            if "Event.aspx" in href and "e=" in href:
                guid_match = re.search(r"e=([A-Fa-f0-9]{32})", href)
                if guid_match:
                    guid = guid_match.group(1)
                    if guid not in seen_guids:
                        seen_guids.add(guid)
                        show_name = link.get_text(strip=True)
                        full_url = href if href.startswith("http") else f"{BASE_URL}{href}"
                        shows.append({
                            "guid": guid,
                            "name": show_name,
                            "url": full_url,
                        })

        return shows

    def _click_next_page(self) -> bool:
        """Try to click the 'Next' page link. Returns True if successful."""
        try:
            # Look for pagination elements
            pager_links = self.driver.find_elements(
                By.CSS_SELECTOR,
                "a[href*='__doPostBack'][href*='Page'], "
                ".pagination a, .pager a, "
                "a[href*='Page$Next']",
            )

            for link in pager_links:
                text = link.text.strip().lower()
                if text in ("next", "›", "»", ">", "..."):
                    link.click()
                    self._wait_for_page_load()
                    return True

            # Also try looking for numbered page links
            current_page = None
            page_links = self.driver.find_elements(
                By.CSS_SELECTOR, ".pagination span, .pager span"
            )
            for span in page_links:
                try:
                    current_page = int(span.text.strip())
                except ValueError:
                    continue

            if current_page:
                for link in pager_links:
                    try:
                        if int(link.text.strip()) == current_page + 1:
                            link.click()
                            self._wait_for_page_load()
                            return True
                    except ValueError:
                        continue

        except Exception as e:
            logger.debug(f"No next page found: {e}")

        return False

    def get_event_details(self, guid: str) -> dict:
        """Navigate to an event page and extract details."""
        url = EVENT_URL_TEMPLATE.format(guid=guid)

        try:
            self.driver.get(url)
            self._wait_for_page_load()
            soup = self._get_soup()
        except Exception as e:
            logger.error(f"Failed to load event {guid}: {e}")
            return {"guid": guid, "error": str(e)}

        details = {
            "guid": guid,
            "url": url,
            "name": "",
            "location": "",
            "province": "",
            "date_start": "",
            "competition_level": "Unknown",
            "classes": [],
            "is_ontario": False,
            "is_dressage": False,
        }

        # Extract title
        title = soup.find("h1") or soup.find("h2")
        if title:
            details["name"] = title.get_text(strip=True)

        # Full page text for analysis
        body_text = soup.get_text(" ", strip=True).lower()

        # Ontario check
        for indicator in ONTARIO_INDICATORS:
            if indicator.lower() in body_text:
                details["is_ontario"] = True
                details["province"] = "ON"
                break

        # Dressage check
        if "dressage" in body_text:
            details["is_dressage"] = True

        # Competition level
        name_and_body = f"{details['name']} {body_text}".lower()
        for level, keywords in COMPETITION_LEVELS.items():
            for kw in keywords:
                if kw in name_and_body:
                    details["competition_level"] = level
                    break
            if details["competition_level"] != "Unknown":
                break

        # Date extraction
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

        # Extract classes
        details["classes"] = self._extract_classes_from_page(soup)

        return details

    def get_entry_status(self, guid: str) -> dict:
        """Navigate to entry status page and count entries."""
        url = ENTRY_STATUS_URL_TEMPLATE.format(guid=guid)

        try:
            self.driver.get(url)
            self._wait_for_page_load()
            soup = self._get_soup()
        except Exception as e:
            logger.error(f"Failed to load entry status for {guid}: {e}")
            return {"total_entries": 0, "by_rider_status": {
                "Junior": 0, "Adult Amateur": 0, "Open": 0,
            }}

        entries = {
            "total_entries": 0,
            "by_rider_status": {
                "Junior": 0,
                "Adult Amateur": 0,
                "Open": 0,
            },
        }

        # Count entries from tables
        tables = soup.find_all("table")
        for table in tables:
            rows = table.find_all("tr")
            if len(rows) < 2:
                continue

            # Check if this looks like an entries table
            header = rows[0].get_text(" ", strip=True).lower()
            if any(kw in header for kw in [
                "rider", "horse", "entry", "name", "class", "competitor",
            ]):
                for row in rows[1:]:
                    cells = row.find_all("td")
                    if len(cells) >= 2:
                        row_text = " ".join(
                            c.get_text(strip=True) for c in cells
                        )
                        if row_text.strip():
                            entries["total_entries"] += 1
                            status = self._classify_rider_status(row_text)
                            entries["by_rider_status"][status] += 1

        # Fallback: look for entry count in page text
        if entries["total_entries"] == 0:
            page_text = soup.get_text(" ", strip=True)
            count_match = re.search(
                r"(\d+)\s*(?:entries|riders|horses|competitors)",
                page_text, re.I,
            )
            if count_match:
                entries["total_entries"] = int(count_match.group(1))

        return entries

    def _extract_classes_from_page(self, soup: BeautifulSoup) -> list[dict]:
        """Extract class information from an event page."""
        classes = []
        dressage_keywords = [
            "dressage", "test", "training level", "first level",
            "second level", "third level", "fourth level",
            "prix st. georges", "grand prix", "intermediate",
            "preliminary", "novice", "freestyle",
        ]

        # Check tables for class listings
        for table in soup.find_all("table"):
            for row in table.find_all("tr"):
                row_text = row.get_text(" ", strip=True)
                if any(kw in row_text.lower() for kw in dressage_keywords):
                    status = self._classify_rider_status(row_text)
                    classes.append({
                        "name": row_text[:200],
                        "rider_status": status,
                    })

        # Check list items
        for li in soup.find_all("li"):
            li_text = li.get_text(strip=True)
            if any(kw in li_text.lower() for kw in dressage_keywords):
                status = self._classify_rider_status(li_text)
                classes.append({
                    "name": li_text[:200],
                    "rider_status": status,
                })

        return classes

    def _classify_rider_status(self, text: str) -> str:
        """Classify rider status from text."""
        text_lower = text.lower()
        for status, patterns in RIDER_STATUS_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, text_lower):
                    return status
        return "Open"

    def scrape_all(self, years: int = 5) -> list[dict]:
        """Main scraping workflow."""
        logger.info(
            f"Starting Selenium scrape for Ontario dressage shows ({years} years)"
        )

        self._init_driver()

        try:
            # Collect shows from both listing and results pages
            all_shows = []

            try:
                listing_shows = self.scrape_shows_listing()
                all_shows.extend(listing_shows)
            except Exception as e:
                logger.error(f"Error scraping shows listing: {e}")

            try:
                results_shows = self.scrape_results_page()
                all_shows.extend(results_shows)
            except Exception as e:
                logger.error(f"Error scraping results page: {e}")

            # Deduplicate
            seen = set()
            unique_shows = []
            for show in all_shows:
                if show["guid"] not in seen:
                    seen.add(show["guid"])
                    unique_shows.append(show)

            logger.info(f"Total unique shows to process: {len(unique_shows)}")

            # Process each show
            results = []
            for i, show in enumerate(unique_shows):
                logger.info(
                    f"[{i+1}/{len(unique_shows)}] Processing: "
                    f"{show.get('name', show['guid'][:16])}"
                )

                details = self.get_event_details(show["guid"])

                if details.get("error"):
                    logger.warning(f"  Skipping (error): {details['error']}")
                    continue

                if not details.get("is_ontario"):
                    logger.debug(
                        f"  Skipping (not Ontario): {details.get('name')}"
                    )
                    continue

                if not details.get("is_dressage"):
                    logger.debug(
                        f"  Skipping (not dressage): {details.get('name')}"
                    )
                    continue

                entry_data = self.get_entry_status(show["guid"])

                record = {
                    "show_name": details.get("name", ""),
                    "guid": show["guid"],
                    "date": details.get("date_start", ""),
                    "location": details.get("location", ""),
                    "competition_level": details.get(
                        "competition_level", "Unknown"
                    ),
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

            logger.info(
                f"Scraping complete. {len(results)} Ontario dressage shows."
            )
            return results

        finally:
            self.close()


def save_results(results: list[dict], output_path: str):
    """Save results to CSV."""
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
    """Generate summary analysis report."""
    if not results:
        return "No data available for analysis."

    lines = []
    lines.append("=" * 70)
    lines.append("ONTARIO DRESSAGE ENTRY ANALYSIS - CompeteEasy Portal")
    lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("=" * 70)
    lines.append("")

    total_shows = len(results)
    total_entries = sum(r["total_entries"] for r in results)
    total_jr = sum(r["junior_entries"] for r in results)
    total_aa = sum(r["adult_amateur_entries"] for r in results)
    total_op = sum(r["open_entries"] for r in results)

    lines.append(f"Total Shows Analyzed: {total_shows}")
    lines.append(f"Total Entries: {total_entries}")
    lines.append("")

    # By Competition Level
    lines.append("-" * 60)
    lines.append("ENTRIES BY COMPETITION STATUS")
    lines.append("-" * 60)
    lines.append(
        f"{'Level':<12} {'Shows':>8} {'Total':>8} "
        f"{'Junior':>8} {'AA':>8} {'Open':>8}"
    )
    lines.append("-" * 60)
    for level in ["Bronze", "Silver", "Gold", "Unknown"]:
        level_shows = [r for r in results if r["competition_level"] == level]
        if level_shows:
            n = len(level_shows)
            t = sum(r["total_entries"] for r in level_shows)
            jr = sum(r["junior_entries"] for r in level_shows)
            aa = sum(r["adult_amateur_entries"] for r in level_shows)
            op = sum(r["open_entries"] for r in level_shows)
            lines.append(
                f"{level:<12} {n:>8} {t:>8} {jr:>8} {aa:>8} {op:>8}"
            )
    lines.append("-" * 60)
    lines.append(
        f"{'TOTAL':<12} {total_shows:>8} {total_entries:>8} "
        f"{total_jr:>8} {total_aa:>8} {total_op:>8}"
    )

    # By Rider Status
    lines.append("")
    lines.append("-" * 60)
    lines.append("ENTRIES BY RIDER STATUS")
    lines.append("-" * 60)
    lines.append(f"  Junior:        {total_jr:>8}")
    lines.append(f"  Adult Amateur: {total_aa:>8}")
    lines.append(f"  Open:          {total_op:>8}")
    lines.append(f"  TOTAL:         {total_jr + total_aa + total_op:>8}")

    # Year breakdown
    lines.append("")
    lines.append("-" * 60)
    lines.append("BREAKDOWN BY YEAR")
    lines.append("-" * 60)
    by_year = {}
    for r in results:
        year_match = re.search(r"(20\d{2})", r.get("date", ""))
        year = year_match.group(1) if year_match else "Unknown"
        if year not in by_year:
            by_year[year] = {
                "shows": 0, "entries": 0,
                "jr": 0, "aa": 0, "open": 0,
                "bronze": 0, "silver": 0, "gold": 0,
            }
        by_year[year]["shows"] += 1
        by_year[year]["entries"] += r["total_entries"]
        by_year[year]["jr"] += r["junior_entries"]
        by_year[year]["aa"] += r["adult_amateur_entries"]
        by_year[year]["open"] += r["open_entries"]
        level = r["competition_level"].lower()
        if level in ("bronze", "silver", "gold"):
            by_year[year][level] += 1

    lines.append(
        f"{'Year':<8} {'Shows':>6} {'Entries':>8} "
        f"{'Jr':>6} {'AA':>6} {'Open':>6} "
        f"{'Br':>4} {'Sv':>4} {'Gd':>4}"
    )
    lines.append("-" * 60)
    for year in sorted(by_year.keys()):
        d = by_year[year]
        lines.append(
            f"{year:<8} {d['shows']:>6} {d['entries']:>8} "
            f"{d['jr']:>6} {d['aa']:>6} {d['open']:>6} "
            f"{d['bronze']:>4} {d['silver']:>4} {d['gold']:>4}"
        )

    lines.append("")
    lines.append("=" * 70)
    lines.append("Br=Bronze, Sv=Silver, Gd=Gold, AA=Adult Amateur, Jr=Junior")
    lines.append("=" * 70)

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Selenium-based scraper for CompeteEasy Ontario dressage entries"
        )
    )
    parser.add_argument(
        "--output", "-o",
        default="ontario_dressage_entries.csv",
        help="Output CSV file (default: ontario_dressage_entries.csv)",
    )
    parser.add_argument(
        "--years", "-y",
        type=int, default=5,
        help="Years to look back (default: 5)",
    )
    parser.add_argument(
        "--summary-file", "-s",
        default="analysis_summary.txt",
        help="Summary output file (default: analysis_summary.txt)",
    )
    parser.add_argument(
        "--json-output", "-j",
        default="ontario_dressage_entries.json",
        help="JSON output file (default: ontario_dressage_entries.json)",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Run browser in headed mode (visible window)",
    )
    parser.add_argument(
        "--delay",
        type=float, default=PAGE_DELAY,
        help=f"Delay between pages in seconds (default: {PAGE_DELAY})",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    scraper = SeleniumScraper(
        headless=not args.headed,
        delay=args.delay,
    )

    results = scraper.scrape_all(years=args.years)

    # Save CSV
    save_results(results, args.output)

    # Save JSON
    with open(args.json_output, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    logger.info(f"JSON saved to {args.json_output}")

    # Generate and save summary
    summary = generate_summary(results)
    with open(args.summary_file, "w", encoding="utf-8") as f:
        f.write(summary)
    logger.info(f"Summary saved to {args.summary_file}")

    print("\n" + summary)

    return 0


if __name__ == "__main__":
    sys.exit(main())
