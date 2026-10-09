"""
Company finder: discovers which job platform each company uses and adds every
company it finds to companies.csv (even if it has no data roles open today,
because it might post one tomorrow).

Usage (run from the main job-hunter folder):
  python -m tools.find_companies                  # uses tools/candidates.txt
  python -m tools.find_companies my_list.txt      # use your own list of names

How it works:
  1. For every company name, guess a few possible slugs
     ("Urban Company" -> "urbancompany", "urban-company", "UrbanCompany").
     A line like "Weights and Biases | wandb" adds your own slug guess.
  2. Ask each platform (Greenhouse, Lever, Ashby, SmartRecruiters, Workable)
     whether a job board exists with that slug, and read its job titles.
  3. If several boards answer, keep the best one (most data jobs, then most jobs).
  4. Append every found company to companies.csv. Existing rows are never changed.
  5. Report how many data roles each company has open today (for information only).

This is a one-time (or occasional) setup tool, not part of the hourly pipeline.
"""
import argparse
import csv
import re
from concurrent.futures import ThreadPoolExecutor

import requests

from filters import has_any
from scrapers.common import load_config

COMPANIES_FILE = "companies.csv"
DEFAULT_CANDIDATES = "tools/candidates.txt"
WORKERS = 20          # parallel lookups
TIMEOUT = 10
HEADERS = {"User-Agent": "job-hunter (open-source job search tool)"}


def get_json(url, params=None):
    """Quiet request: returns JSON, or None for any error (most guesses will 404)."""
    try:
        response = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
        if response.status_code != 200:
            return None
        return response.json()
    except (requests.RequestException, ValueError):
        return None


# ---------- One checker per platform: returns the board's job titles ([] = not found) ----------
def check_greenhouse(slug):
    data = get_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")
    return [j.get("title") or "" for j in data.get("jobs", [])] if isinstance(data, dict) else []


def check_lever(slug):
    data = get_json(f"https://api.lever.co/v0/postings/{slug}", {"mode": "json"})
    return [j.get("text") or "" for j in data] if isinstance(data, list) else []


def check_ashby(slug):
    data = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
    return [j.get("title") or "" for j in data.get("jobs", [])] if isinstance(data, dict) else []


def check_smartrecruiters(slug):
    # Unknown slugs return an empty list instead of an error. First 100 jobs are enough here.
    data = get_json(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings", {"limit": 100})
    return [j.get("name") or "" for j in data.get("content", [])] if isinstance(data, dict) else []


def check_workable(slug):
    data = get_json(f"https://apply.workable.com/api/v1/widget/accounts/{slug}")
    return [j.get("title") or "" for j in data.get("jobs", [])] if isinstance(data, dict) else []


CHECKERS = {
    "greenhouse": check_greenhouse,
    "lever": check_lever,
    "ashby": check_ashby,
    "smartrecruiters": check_smartrecruiters,
    "workable": check_workable,
}


def guess_slugs(name, extra=()):
    """'Urban Company' -> ['urbancompany', 'urban-company', 'UrbanCompany'] (+ any extra guesses)"""
    cleaned = re.sub(r"[^A-Za-z0-9 ]", "", name).strip()   # drop & . ' etc.
    words = cleaned.split()
    guesses = list(extra) + ["".join(words).lower(), "-".join(words).lower(), "".join(words)]
    return list(dict.fromkeys(g for g in guesses if g))     # remove duplicates, keep order


def load_candidates(path):
    """Read names. 'Name | slug1, slug2' adds extra slug guesses. Returns {name: [extra slugs]}."""
    candidates = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            name, _, slugs = line.partition("|")
            name = name.strip()
            extra = [s.strip() for s in slugs.split(",") if s.strip()]
            candidates.setdefault(name, []).extend(extra)
    return candidates


def load_existing(path):
    """Existing companies.csv rows, so we never add a company twice."""
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    known_slugs = {(r["platform"], r["slug"].strip().lower()) for r in rows}
    known_names = {r["name"].strip().lower() for r in rows}
    return known_slugs, known_names


def main():
    parser = argparse.ArgumentParser(description="Find companies and their job platforms.")
    parser.add_argument("candidates", nargs="?", default=DEFAULT_CANDIDATES)
    args = parser.parse_args()

    config = load_config()
    title_keywords = config["title_keywords"]
    known_slugs, known_names = load_existing(COMPANIES_FILE)
    candidates = {name: extra for name, extra in load_candidates(args.candidates).items()
                  if name.lower() not in known_names}

    # ---------- 1. Check every (company, slug guess, platform) combination in parallel ----------
    tasks = [(name, platform, slug)
             for name, extra in candidates.items()
             for slug in guess_slugs(name, extra)
             for platform in CHECKERS]
    print(f"Checking {len(candidates)} companies ({len(tasks)} lookups, {WORKERS} at a time)...")
    print("This is a one-time run and can take 10-15 minutes.\n")

    def run(task):
        name, platform, slug = task
        return name, platform, slug, CHECKERS[platform](slug)

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = list(pool.map(run, tasks))

    # ---------- 2. For each company, keep its best board ----------
    # Ranked by data jobs first, then total jobs, so an active data-hiring board wins ties.
    def count_data(titles):
        return sum(1 for t in titles if has_any(t, title_keywords))

    best = {}
    for name, platform, slug, titles in results:
        rank = (count_data(titles), len(titles))
        if titles and (name not in best or rank > best[name][3]):
            best[name] = (platform, slug, titles, rank)

    # ---------- 3. Add every company we found ----------
    new_rows = [(platform, slug, name) for name, (platform, slug, _, _) in best.items()
                if (platform, slug.lower()) not in known_slugs]

    # If the file's last line has no line break, add one so new rows don't join it
    with open(COMPANIES_FILE, "rb") as f:
        content = f.read()
    with open(COMPANIES_FILE, "a", newline="", encoding="utf-8") as f:
        if content and not content.endswith(b"\n"):
            f.write("\n")
        csv.writer(f).writerows(new_rows)

    # ---------- 4. Report ----------
    with_data = sum(1 for _, _, _, (data, _) in best.values() if data)
    print(f"Found boards for {len(best)} of {len(candidates)} companies.")
    print(f"Added {len(new_rows)} companies to {COMPANIES_FILE}.")
    print(f"  {with_data} have data roles open today; the rest are watched for future openings.\n")

    for platform in CHECKERS:
        count = sum(1 for p, _, _ in new_rows if p == platform)
        print(f"  {platform:<16} {count}")

    print("\nAdded (open jobs, data jobs today, example title to sanity-check the match):")
    ordered = sorted(best.items(), key=lambda item: item[1][3], reverse=True)
    for name, (platform, slug, titles, (data, total)) in ordered:
        example = next((t for t in titles if has_any(t, title_keywords)), titles[0])
        print(f"  {name[:24]:<24} {platform:<15} {total:>5} jobs {data:>4} data   e.g. {example[:45]}")

    # Short slugs ("ro", "flex", "open") may belong to a different company with the same name
    risky = [f"{name} ({platform}/{slug})" for name, (platform, slug, _, _) in best.items()
             if len(slug) <= 5]
    if risky:
        print(f"\nDouble-check these short slugs ({len(risky)}); they could be a different company:")
        print("  " + ", ".join(risky))

    not_found = [n for n in candidates if n not in best]
    if not_found:
        print(f"\nNo board found ({len(not_found)}). Most likely Workday or another ATS,")
        print("or a slug we couldn't guess. Adzuna still covers many of these:")
        print("  " + ", ".join(not_found))


if __name__ == "__main__":
    main()