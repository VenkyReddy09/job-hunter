"""
Job Hunter - runs the whole pipeline:
  EXTRACT   -> every company in companies.csv (in parallel) + Adzuna
  TRANSFORM -> filters.py
  LOAD      -> writer.py

Usage:
  python main.py            normal run (Adzuna only runs every Nth hour)
  python main.py --adzuna   force Adzuna to run now
"""
import argparse
import csv
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from scrapers import greenhouse, lever, ashby, smartrecruiters, workable, adzuna
from scrapers.common import load_config
from filters import filter_jobs, print_stats
from writer import save_new_jobs, build_daily_files

# The "plug-in" table: platform name in companies.csv -> its scraper function.
# Adding a new platform = write the scraper + add one line here.
SCRAPERS = {
    "greenhouse": greenhouse.fetch_jobs,
    "lever": lever.fetch_jobs,
    "ashby": ashby.fetch_jobs,
    "smartrecruiters": smartrecruiters.fetch_jobs,
    "workable": workable.fetch_jobs,
}


def load_companies(path="companies.csv"):
    with open(path, newline="", encoding="utf-8") as f:
        return [row for row in csv.DictReader(f) if row.get("platform")]

def scrape_company(company, config):
    """Scrape one company. Returns (name, jobs, seconds). Never raises."""
    name = company["name"].strip()
    start = time.time()
    fetch = SCRAPERS.get(company["platform"].strip().lower())
    if fetch is None:
        print(f"  ! Unknown platform '{company['platform']}' for {name} - skipped")
        return name, [], 0.0
    try:
        jobs = fetch(company["slug"].strip(), name, config)
    except Exception as error:
        print(f"  ! {name} failed: {type(error).__name__}: {error}")
        jobs = []
    return name, jobs, time.time() - start


def scrape_all_companies(companies, config):
    """Scrape every company, several at the same time, and report the slowest ones."""
    jobs, timings = [], []
    workers = config.get("parallel_workers", 20)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(scrape_company, company, config) for company in companies]
        for future in as_completed(futures):     # collect results as each one finishes
            name, company_jobs, seconds = future.result()
            jobs.extend(company_jobs)
            timings.append((seconds, name))

    slowest = sorted(timings, reverse=True)[:3]
    print("  Slowest: " + ", ".join(f"{name} {seconds:.1f}s" for seconds, name in slowest))
    return jobs

def adzuna_should_run(config, forced):
    """Adzuna runs only every Nth hour (UTC) to stay within its free limits."""
    if forced:
        return True
    every = config.get("adzuna", {}).get("run_every_hours", 1)
    return datetime.now(timezone.utc).hour % every == 0


def print_by_source(label, jobs):
    counts = Counter(job["source"] for job in jobs)
    print(f"  {label}: " + ", ".join(f"{src} {n}" for src, n in sorted(counts.items())))


def main():
    parser = argparse.ArgumentParser(description="Find data engineering jobs.")
    parser.add_argument("--adzuna", action="store_true",
                        help="run Adzuna now, ignoring run_every_hours")
    args = parser.parse_args()

    start = time.time()
    config = load_config()
    companies = load_companies()

    # ---------- EXTRACT ----------
    print(f"[1/3] EXTRACT: checking {len(companies)} companies "
          f"({config.get('parallel_workers', 20)} at a time)...")
    jobs = scrape_all_companies(companies, config)

    if adzuna_should_run(config, args.adzuna):
        print("  Searching Adzuna...")
        jobs += adzuna.search_jobs(config)
    else:
        print("  Adzuna skipped this hour (use --adzuna to force)")
    print_by_source("Scraped", jobs)

    # ---------- TRANSFORM ----------
    print("\n[2/3] TRANSFORM: filtering...")
    kept, stats = filter_jobs(jobs, config)
    print_stats(stats)
    print_by_source("Kept", kept)

    # ---------- LOAD ----------
    print("\n[3/3] LOAD: saving...")
    new_rows = save_new_jobs(kept, config["master_file"])
    print(f"  {len(new_rows)} NEW jobs added to {config['master_file']}")
    build_daily_files(config)

    print(f"\nDone in {time.time() - start:.1f} seconds.")


if __name__ == "__main__":
    main()