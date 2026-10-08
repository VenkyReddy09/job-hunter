"""
Shared helpers used by every scraper.
Writing them once here means all scrapers behave the same way.
"""
import csv
import html
import re
import sys
from datetime import datetime, timezone

import requests
import yaml

# Identify ourselves politely to the APIs.
HEADERS = {"User-Agent": "job-hunter (open-source job search tool)"}


def load_config(path="config.yaml"):
    """Read config.yaml into a Python dictionary."""
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)

def fetch_json(url, params=None, timeout=15):
    """
    Download a URL and return its JSON data.
    If anything goes wrong (bad slug, network error), return None
    instead of crashing, so one broken company never stops the whole run.
    Error messages never include params, so secret keys never appear in logs.
    """
    try:
        response = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
        response.raise_for_status()      # turns 404/500 errors into exceptions
        return response.json()
    except requests.HTTPError as error:
        print(f"  ! {url} returned HTTP {error.response.status_code}")
        return None
    except (requests.RequestException, ValueError) as error:
        print(f"  ! Could not fetch {url}: {type(error).__name__}")
        return None

def clean_html(text):
    """Turn messy HTML into plain readable text."""
    if not text:
        return ""
    text = html.unescape(text)              # &lt;p&gt;  ->  <p>
    text = re.sub(r"<[^>]+>", " ", text)    # remove all <tags>
    text = html.unescape(text)              # &nbsp; and friends -> normal characters
    text = text.replace("\\n", " ")         # literal "\n" text -> space
    return re.sub(r"\s+", " ", text).strip()  # collapse extra spaces/newlines


def to_utc_iso(value):
    """
    Convert any date format into one standard UTC format: 2026-09-03T17:32:53Z
    Handles text dates ('2026-09-03T13:32:53-04:00'), date-only text ('2026-09-03'),
    and millisecond numbers (1725384773000), which Lever uses.
    """
    if value in (None, ""):
        return ""
    try:
        if isinstance(value, (int, float)):
            dt = datetime.fromtimestamp(value / 1000, tz=timezone.utc)
        else:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, OSError):
        return ""


def title_matches(title, keywords):
    """True if the job title contains any keyword (not case-sensitive)."""
    title = (title or "").lower()
    return any(keyword.lower() in title for keyword in keywords)


def make_job(job_id, title, company, location, description, posted_at, source,
             apply_link, work_type="", country_hint=""):
    """
    The STANDARD job format. Every scraper returns jobs in exactly this shape,
    so the filters and writer never need to know which platform a job came from.
    work_type and country_hint are optional: filled only when the platform provides them.
    """
    return {
        "job_id": f"{source}-{job_id}",   # e.g. greenhouse-8172508 (unique across platforms)
        "title": title,
        "company": company,
        "location": location,
        "description": description,
        "posted_at": posted_at,
        "source": source,
        "apply_link": apply_link,
        "work_type": work_type,
        "country_hint": country_hint,
    }


def run_cli_test(platform, fetch_jobs):
    """
    Shared test mode for every scraper:
      python -m scrapers.<platform>          -> check every company of this platform in companies.csv
      python -m scrapers.<platform> <slug>   -> show sample jobs from one company
    """
    config = load_config()

    if len(sys.argv) > 1:
        slug = sys.argv[1]
        jobs = fetch_jobs(slug, slug, config)
        print(f"Found {len(jobs)} jobs for '{slug}'\n")
        for job in jobs[:3]:
            print("-" * 60)
            print("Title:    ", job["title"])
            print("Location: ", job["location"])
            print("Work type:", job["work_type"] or "(not provided)")
            print("Country:  ", job["country_hint"] or "(not provided)")
            print("Posted:   ", job["posted_at"])
            print("Link:     ", job["apply_link"])
            print("Desc:     ", job["description"][:150], "...")
        return

    print(f"Checking {platform} companies in companies.csv...\n")
    found_any = False
    with open("companies.csv", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["platform"] == platform:
                found_any = True
                count = len(fetch_jobs(row["slug"], row["name"], config))
                status = "OK" if count else "CHECK SLUG"
                print(f"{row['name']:<15} {count:>5} jobs   {status}")
    if not found_any:
        print(f"No {platform} companies in companies.csv yet.")