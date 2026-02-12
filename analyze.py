#!/usr/bin/env python3
"""
Analyze scraped Ontario dressage entry data.

Reads scraped data from JSON/CSV and produces summary tables and charts
broken down by:
- Competition status: Bronze, Silver, Gold
- Rider status: Junior, Adult Amateur, Open
- Year (last 5 years)

Usage:
    python3 analyze.py [--input ontario_dressage_entries.json]
"""

import argparse
import json
import re
import sys
from pathlib import Path

try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False


def load_data(input_path: str) -> list[dict]:
    """Load data from JSON or CSV."""
    path = Path(input_path)
    if path.suffix == ".json":
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    elif path.suffix == ".csv":
        if not HAS_PANDAS:
            raise ImportError("pandas is required to read CSV files")
        df = pd.read_csv(path)
        return df.to_dict("records")
    else:
        raise ValueError(f"Unsupported file format: {path.suffix}")


def extract_year(date_str: str) -> str:
    """Extract year from a date string."""
    match = re.search(r"(20\d{2})", str(date_str))
    return match.group(1) if match else "Unknown"


def print_section(title: str, width: int = 70):
    """Print a section header."""
    print()
    print("=" * width)
    print(f"  {title}")
    print("=" * width)


def analyze_with_pandas(data: list[dict]):
    """Analyze data using pandas for richer output."""
    df = pd.DataFrame(data)
    df["year"] = df["date"].apply(extract_year)

    print_section("ONTARIO DRESSAGE ENTRY ANALYSIS")
    print(f"\n  Data source: CompeteEasy Equest Portal")
    print(f"  Total competitions analyzed: {len(df)}")
    print(f"  Total entries: {df['total_entries'].sum()}")

    # Summary by Competition Level
    print_section("ENTRIES BY COMPETITION STATUS (Bronze / Silver / Gold)")

    level_summary = df.groupby("competition_level").agg(
        shows=("show_name", "count"),
        total_entries=("total_entries", "sum"),
        junior=("junior_entries", "sum"),
        adult_amateur=("adult_amateur_entries", "sum"),
        open=("open_entries", "sum"),
    ).reindex(["Bronze", "Silver", "Gold", "Unknown"])

    level_summary = level_summary.dropna(how="all")

    print(f"\n{'Level':<12} {'Shows':>8} {'Total':>10} "
          f"{'Junior':>10} {'Adult Am.':>12} {'Open':>10}")
    print("-" * 62)
    for level, row in level_summary.iterrows():
        print(f"{level:<12} {int(row['shows']):>8} "
              f"{int(row['total_entries']):>10} "
              f"{int(row['junior']):>10} "
              f"{int(row['adult_amateur']):>12} "
              f"{int(row['open']):>10}")
    print("-" * 62)
    print(f"{'TOTAL':<12} {int(level_summary['shows'].sum()):>8} "
          f"{int(level_summary['total_entries'].sum()):>10} "
          f"{int(level_summary['junior'].sum()):>10} "
          f"{int(level_summary['adult_amateur'].sum()):>12} "
          f"{int(level_summary['open'].sum()):>10}")

    # Summary by Rider Status
    print_section("ENTRIES BY RIDER STATUS (Junior / Adult Amateur / Open)")

    total_jr = df["junior_entries"].sum()
    total_aa = df["adult_amateur_entries"].sum()
    total_op = df["open_entries"].sum()
    total_all = total_jr + total_aa + total_op

    print(f"\n  {'Status':<20} {'Entries':>10} {'% of Total':>12}")
    print("  " + "-" * 44)
    if total_all > 0:
        print(f"  {'Junior':<20} {int(total_jr):>10} "
              f"{total_jr/total_all*100:>11.1f}%")
        print(f"  {'Adult Amateur':<20} {int(total_aa):>10} "
              f"{total_aa/total_all*100:>11.1f}%")
        print(f"  {'Open':<20} {int(total_op):>10} "
              f"{total_op/total_all*100:>11.1f}%")
    else:
        print(f"  {'Junior':<20} {int(total_jr):>10}")
        print(f"  {'Adult Amateur':<20} {int(total_aa):>10}")
        print(f"  {'Open':<20} {int(total_op):>10}")
    print("  " + "-" * 44)
    print(f"  {'TOTAL':<20} {int(total_all):>10}")

    # Year-over-year breakdown
    print_section("YEAR-OVER-YEAR BREAKDOWN")

    year_summary = df.groupby("year").agg(
        shows=("show_name", "count"),
        total_entries=("total_entries", "sum"),
        junior=("junior_entries", "sum"),
        adult_amateur=("adult_amateur_entries", "sum"),
        open=("open_entries", "sum"),
    ).sort_index()

    print(f"\n{'Year':<8} {'Shows':>6} {'Entries':>8} "
          f"{'Junior':>8} {'Adult Am':>10} {'Open':>8}")
    print("-" * 50)
    for year, row in year_summary.iterrows():
        print(f"{year:<8} {int(row['shows']):>6} "
              f"{int(row['total_entries']):>8} "
              f"{int(row['junior']):>8} "
              f"{int(row['adult_amateur']):>10} "
              f"{int(row['open']):>8}")

    # Cross-tabulation: Year x Level
    print_section("SHOWS BY YEAR AND COMPETITION LEVEL")

    cross = pd.crosstab(
        df["year"], df["competition_level"],
        margins=True, margins_name="Total",
    )
    # Reorder columns
    cols = [c for c in ["Bronze", "Silver", "Gold", "Unknown", "Total"]
            if c in cross.columns]
    cross = cross[cols]

    print(f"\n{cross.to_string()}")

    # Cross-tabulation: Year x entries by level
    print_section("TOTAL ENTRIES BY YEAR AND COMPETITION LEVEL")

    for level in ["Bronze", "Silver", "Gold"]:
        level_df = df[df["competition_level"] == level]
        if not level_df.empty:
            year_entries = level_df.groupby("year")["total_entries"].sum()
            print(f"\n  {level}:")
            for year, entries in year_entries.items():
                print(f"    {year}: {int(entries)} entries")

    # Per-show detail listing
    print_section("INDIVIDUAL SHOW DETAILS")

    for _, row in df.sort_values(["year", "show_name"]).iterrows():
        print(f"\n  {row['show_name']}")
        print(f"    Date: {row['date']}  |  Level: {row['competition_level']}")
        print(f"    Total: {int(row['total_entries'])}  |  "
              f"Jr: {int(row['junior_entries'])}  |  "
              f"AA: {int(row['adult_amateur_entries'])}  |  "
              f"Open: {int(row['open_entries'])}")

    print()


def analyze_basic(data: list[dict]):
    """Basic analysis without pandas."""
    print_section("ONTARIO DRESSAGE ENTRY ANALYSIS")
    print(f"\n  Total competitions analyzed: {len(data)}")
    print(f"  Total entries: {sum(r['total_entries'] for r in data)}")

    # By competition level
    print_section("ENTRIES BY COMPETITION STATUS")

    for level in ["Bronze", "Silver", "Gold", "Unknown"]:
        level_data = [r for r in data if r["competition_level"] == level]
        if level_data:
            print(f"\n  {level}:")
            print(f"    Shows: {len(level_data)}")
            print(f"    Total entries: {sum(r['total_entries'] for r in level_data)}")
            print(f"    Junior: {sum(r['junior_entries'] for r in level_data)}")
            print(f"    Adult Amateur: {sum(r['adult_amateur_entries'] for r in level_data)}")
            print(f"    Open: {sum(r['open_entries'] for r in level_data)}")

    # By rider status
    print_section("ENTRIES BY RIDER STATUS")

    total_jr = sum(r["junior_entries"] for r in data)
    total_aa = sum(r["adult_amateur_entries"] for r in data)
    total_op = sum(r["open_entries"] for r in data)

    print(f"\n  Junior:        {total_jr}")
    print(f"  Adult Amateur: {total_aa}")
    print(f"  Open:          {total_op}")
    print(f"  TOTAL:         {total_jr + total_aa + total_op}")

    # By year
    print_section("BY YEAR")

    by_year = {}
    for r in data:
        year = extract_year(r.get("date", ""))
        if year not in by_year:
            by_year[year] = []
        by_year[year].append(r)

    for year in sorted(by_year.keys()):
        records = by_year[year]
        print(f"\n  {year}:")
        print(f"    Shows: {len(records)}")
        print(f"    Entries: {sum(r['total_entries'] for r in records)}")
        print(f"    Junior: {sum(r['junior_entries'] for r in records)}")
        print(f"    Adult Amateur: {sum(r['adult_amateur_entries'] for r in records)}")
        print(f"    Open: {sum(r['open_entries'] for r in records)}")

    print()


def main():
    parser = argparse.ArgumentParser(
        description="Analyze Ontario dressage entry data"
    )
    parser.add_argument(
        "--input", "-i",
        default="ontario_dressage_entries.json",
        help="Input data file (JSON or CSV)",
    )
    args = parser.parse_args()

    if not Path(args.input).exists():
        print(f"ERROR: Input file not found: {args.input}")
        print("Run the scraper first: python3 scrape_competeeasy.py")
        return 1

    data = load_data(args.input)

    if not data:
        print("No data found in input file.")
        return 1

    if HAS_PANDAS:
        analyze_with_pandas(data)
    else:
        analyze_basic(data)

    return 0


if __name__ == "__main__":
    sys.exit(main())
