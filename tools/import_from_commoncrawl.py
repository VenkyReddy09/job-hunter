"""
Bulk company importer: discovers thousands of company job boards from
Common Crawl (a free, public archive of the web) and adds the useful ones
to companies.csv.

Usage (run from the main job-hunter folder):
  python -m tools.import_from_commoncrawl                 # discover + check + add
  python -m tools.import_from_commoncrawl --max-pages 10  # quicker, smaller discovery
  python -m tools.import_from_commoncrawl --refresh       # discover again (adds to the saved list)
  python -m tools.import_from_commoncrawl --refresh --only lever,smartrecruiters
                                                          # rediscover just some platforms
  python -m tools.import_from_commoncrawl --any-country   # also keep companies hiring only elsewhere

How it works:
  PHASE 1 - DISCOVER (slow, saved to tools/discovered_slugs.csv so it runs once)
    Ask Common Crawl's index: "which pages under boards.greenhouse.io/,
    jobs.lever.co/, jobs.ashbyhq.com/, jobs.smartrecruiters.com/ did you crawl?"
    The first part of each page's path is the company slug:
      https://jobs.lever.co/palantir/6ed76ce8-...   ->  lever, palantir
  PHASE 2 - CHECK (parallel)
    For every new slug, read the company's live board. Keep it only if:
      - the board exists and has open jobs, and
      - at least one job is in a country from config.yaml (skip with --any-country)
    So we keep every company that hires in USA/India, whatever roles it has today.
  PHASE 3 - ADD
    Append the kept companies to companies.csv. Existing rows are never changed.

Workable is not included: it rate-limits bulk lookups (HTTP 429).
This is a one-time (or occasional) setup tool, not part of the hourly pipeline.
"""
import argparse
import csv
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import parse_qs, unquote, urlparse

import requests

from filters import find_countries
from scrapers.common import load_config

COMPANIES_FILE = "companies.csv"
DISCOVERED_FILE = "tools/discovered_slugs.csv"
CRAWL_LIST = "https://index.commoncrawl.org/collinfo.json"
HEADERS = {"User-Agent": "job-hunter (open-source job search tool)"}
WORKERS = 20      # parallel board checks in phase 2
PAGE_DELAY = 1    # seconds between Common Crawl requests (be polite to a free service)

# Web addresses where each platform hosts company job boards
DOMAINS = {
    "greenhouse": ["boards.greenhouse.io", "job-boards.greenhouse.io"],
    "lever": ["jobs.lever.co"],
    "ashby": ["jobs.ashbyhq.com"],
    "smartrecruiters": ["jobs.smartrecruiters.com"],
}

# Path parts that are not company slugs
NOT_SLUGS = {"embed", "jobs", "api", "v1", "j", "static", "assets", "favicon.ico",
             "robots.txt", "sitemap.xml", "oneclick-ui", "sr-jobs", "en", "careers"}
SLUG_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")


# =====================================================================
# PHASE 1: discover slugs from Common Crawl
# =====================================================================
def get_with_retries(url, params=None, timeout=90, attempts=6):
    """Common Crawl's free index is often busy. Retry with growing waits (10s, 20s, ... 50s)."""
    for attempt in range(1, attempts + 1):
        try:
            response = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
            if response.status_code == 200:
                return response
            if response.status_code == 404:          # no pages for this domain
                return None
        except requests.RequestException:
            pass
        if attempt < attempts:
            time.sleep(10 * attempt)
    print(f"  ! Gave up on {url} after {attempts} attempts")
    return None


def latest_index():
    """The newest crawl's search address, e.g. https://index.commoncrawl.org/CC-MAIN-2026-39-index"""
    response = get_with_retries(CRAWL_LIST)
    if response is None:
        raise SystemExit("Could not reach Common Crawl. Try again later.")
    newest = response.json()[0]
    print(f"Using Common Crawl index {newest['id']}")
    return newest["cdx-api"]


def slug_from_url(platform, url):
    """'https://jobs.lever.co/palantir/123' -> 'palantir'. Returns None if no slug."""
    parsed = urlparse(url)
    parts = [p for p in unquote(parsed.path).split("/") if p]
    if platform == "greenhouse" and parts[:1] == ["embed"]:
        # Old style: boards.greenhouse.io/embed/job_board?for=stripe
        slug = (parse_qs(parsed.query).get("for") or [None])[0]
    else:
        slug = parts[0] if parts else None
    if not slug or slug.lower() in NOT_SLUGS or not SLUG_PATTERN.match(slug):
        return None
    return slug


def discover(index_url, max_pages, platforms):
    """Return {(platform, slug)} found in Common Crawl."""
    found = {}
    for platform, domains in DOMAINS.items():
        if platform not in platforms:
            continue
        for domain in domains:
            params = {"url": f"{domain}/*", "output": "json", "fl": "url"}
            info = get_with_retries(index_url, {**params, "showNumPages": "true"})
            if info is None:
                continue
            try:
                data = info.json()
                pages = data["pages"] if isinstance(data, dict) else int(data)
            except (ValueError, KeyError, TypeError):
                pages = 1
            pages = min(pages, max_pages)
            print(f"  {domain}: reading {pages} page(s)...")

            before = len(found)
            for page in range(pages):
                response = get_with_retries(index_url, {**params, "page": page})
                time.sleep(PAGE_DELAY)
                if response is None:
                    continue
                for line in response.text.splitlines():
                    try:
                        url = json.loads(line).get("url", "")
                    except ValueError:
                        continue
                    slug = slug_from_url(platform, url)
                    if slug:
                        # Same slug in different upper/lower case counts once
                        found.setdefault((platform, slug.lower()), slug)
            print(f"    -> {len(found) - before} new slugs")
    return {(platform, slug) for (platform, _), slug in found.items()}


def save_discovered(pairs):
    with open(DISCOVERED_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["platform", "slug"])
        writer.writerows(sorted(pairs))


def load_discovered():
    with open(DISCOVERED_FILE, newline="", encoding="utf-8") as f:
        return {(row["platform"], row["slug"]) for row in csv.DictReader(f)}


# =====================================================================
# PHASE 2: check each board live
# Each reader returns (company display name, [(location, country_hint), ...]),
# or None if the board doesn't exist.
# =====================================================================
def get_json(url, params=None):
    try:
        response = requests.get(url, params=params, headers=HEADERS, timeout=15)
        return response.json() if response.status_code == 200 else None
    except (requests.RequestException, ValueError):
        return None


def read_greenhouse(slug):
    data = get_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")
    if not isinstance(data, dict):
        return None
    jobs = data.get("jobs", [])
    name = (jobs[0].get("company_name") if jobs else None) or pretty(slug)
    return name, [((j.get("location") or {}).get("name") or "", "") for j in jobs]


def read_lever(slug):
    data = get_json(f"https://api.lever.co/v0/postings/{slug}", {"mode": "json", "limit": 100})
    if not isinstance(data, list):
        return None
    places = []
    for j in data:
        cat = j.get("categories") or {}
        location = "; ".join(cat.get("allLocations") or [cat.get("location") or ""])
        places.append((location, j.get("country") or ""))
    return pretty(slug), places


def read_ashby(slug):
    data = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
    if not isinstance(data, dict):
        return None
    places = []
    for j in data.get("jobs", []):
        locations = [j.get("location") or ""]
        locations += [(x.get("location") or "") for x in j.get("secondaryLocations") or []]
        country = ((j.get("address") or {}).get("postalAddress") or {}).get("addressCountry") or ""
        places.append(("; ".join(l for l in locations if l), country))
    return pretty(slug), places


def read_smartrecruiters(slug):
    data = get_json(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings", {"limit": 100})
    if not isinstance(data, dict):
        return None
    postings = data.get("content", [])
    name = ((postings[0].get("company") or {}).get("name") if postings else None) or pretty(slug)
    places = [((p.get("location") or {}).get("fullLocation") or "",
               (p.get("location") or {}).get("country") or "") for p in postings]
    return name, places


READERS = {
    "greenhouse": read_greenhouse,
    "lever": read_lever,
    "ashby": read_ashby,
    "smartrecruiters": read_smartrecruiters,
}


def pretty(slug):
    """'urban-company' -> 'Urban Company'"""
    return " ".join(w.capitalize() for w in re.split(r"[-_.]+", slug) if w)


def hires_in_my_countries(places, config):
    """True if any job is located in one of the countries in config.yaml."""
    return any(find_countries({"location": loc, "country_hint": hint}, config)
               for loc, hint in places)


# =====================================================================
# Main
# =====================================================================
def main():
    parser = argparse.ArgumentParser(description="Import company job boards from Common Crawl.")
    parser.add_argument("--max-pages", type=int, default=40,
                        help="max index pages per domain (more = more companies, slower)")
    parser.add_argument("--refresh", action="store_true",
                        help="discover again; new slugs are ADDED to the saved list")
    parser.add_argument("--only", default=",".join(DOMAINS),
                        help="platforms to discover, e.g. lever,smartrecruiters")
    parser.add_argument("--any-country", action="store_true",
                        help="keep companies even if none of their jobs are in your countries")
    args = parser.parse_args()
    config = load_config()

    # ---------- PHASE 1 ----------
    if os.path.exists(DISCOVERED_FILE) and not args.refresh:
        discovered = load_discovered()
        print(f"[1/3] DISCOVER: using saved {DISCOVERED_FILE} ({len(discovered)} slugs)")
    else:
        print("[1/3] DISCOVER: reading Common Crawl (slow; can take 20-40 minutes)...")
        platforms = [p.strip() for p in args.only.split(",") if p.strip()]
        discovered = discover(latest_index(), args.max_pages, platforms)
        if os.path.exists(DISCOVERED_FILE):          # keep slugs found in earlier runs
            discovered |= load_discovered()
        save_discovered(discovered)
        print(f"  Saved {len(discovered)} slugs to {DISCOVERED_FILE}")

    # Skip companies already in companies.csv
    with open(COMPANIES_FILE, newline="", encoding="utf-8") as f:
        known = {(r["platform"], r["slug"].strip().lower()) for r in csv.DictReader(f)}
    to_check = sorted(p for p in discovered if (p[0], p[1].lower()) not in known)

    # ---------- PHASE 2 ----------
    print(f"\n[2/3] CHECK: {len(to_check)} new boards ({WORKERS} at a time)...")
    dead, elsewhere, keep = 0, 0, []

    def check(pair):
        platform, slug = pair
        return platform, slug, READERS[platform](slug)

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = [pool.submit(check, pair) for pair in to_check]
        for done, future in enumerate(as_completed(futures), start=1):
            platform, slug, result = future.result()
            if not result or not result[1]:
                dead += 1                          # board gone, or no open jobs
            elif not args.any_country and not hires_in_my_countries(result[1], config):
                elsewhere += 1                     # only hires in other countries
            else:
                keep.append((platform, slug, result[0]))
            if done % 500 == 0:
                print(f"  checked {done}/{len(to_check)}...")

    # ---------- PHASE 3 ----------
    with open(COMPANIES_FILE, "rb") as f:
        content = f.read()
    with open(COMPANIES_FILE, "a", newline="", encoding="utf-8") as f:
        if content and not content.endswith(b"\n"):
            f.write("\n")
        csv.writer(f).writerows(sorted(keep))

    print("\n[3/3] RESULT")
    print(f"  Discovered:                 {len(discovered)}")
    print(f"  Already in companies.csv:   {len(discovered) - len(to_check)}")
    print(f"  Dead or no open jobs:       {dead}")
    if not args.any_country:
        print(f"  No jobs in your countries:  {elsewhere}")
    print(f"  ADDED to companies.csv:     {len(keep)}")
    for platform in READERS:
        print(f"    {platform:<16} {sum(1 for p, _, _ in keep if p == platform)}")


if __name__ == "__main__":
    main()
