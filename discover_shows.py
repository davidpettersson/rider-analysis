#!/usr/bin/env python3
"""Discovery script to find missing Ontario shows on CompeteEasy."""

import sys
sys.path.insert(0, ".")
from scrape_ontario_dressage import get_all_dressage_shows

MISSING_KEYWORDS = [
    "glanbrook",
    "lda",
    "qslb",
    "quantum",
    "queenswood",
    "stevens creek",
    "westar",
    "canyon creek",
]

def main():
    all_shows = get_all_dressage_shows()
    print(f"\nSearching {len(all_shows)} shows for missing Ontario keywords...\n")

    for kw in MISSING_KEYWORDS:
        matches = [s for s in all_shows if kw in s["name"].lower()]
        if matches:
            print(f"'{kw}' — {len(matches)} match(es):")
            for m in matches:
                print(f"  [{m['id']}] {m['name']}")
        else:
            print(f"'{kw}' — NO MATCHES")
        print()

if __name__ == "__main__":
    main()
