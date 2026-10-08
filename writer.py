"""
LOAD step: save jobs to the master CSV (our "database") and build the daily
per-country files you open each night.
"""
import csv
import os
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# Columns stored in data/jobs.csv (descriptions are left out to keep the file small)
MASTER_COLUMNS = ["job_id", "title", "company", "countries", "location", "work_type",
                  "experience", "skills_matched", "posted_at", "found_at", "source", "apply_link"]

# Columns shown in the daily country files
DISPLAY_COLUMNS = ["Posted", "Title", "Company", "Location", "Work Type",
                   "Experience", "Skills", "Source", "Apply Link"]

# Company-name endings ignored when comparing ("Databricks, Inc." == "Databricks")
COMPANY_SUFFIXES = re.compile(r"\b(inc|llc|ltd|limited|corp|corporation|co|pvt|private|plc|gmbh)\b")

def normalize(text):
    """Lowercase, replace punctuation with spaces, collapse spaces."""
    text = re.sub(r"[^a-z0-9 ]", " ", (text or "").lower())
    return " ".join(text.split())


def dedupe_key(company, title, countries):
    """Identify 'the same job' across platforms: company + title + countries."""
    company = " ".join(COMPANY_SUFFIXES.sub(" ", normalize(company)).split())
    return f"{company}|{normalize(title)}|{countries}"

# ---------- Part 1: the master CSV ----------
def load_master(path):
    """Read every job we've ever saved. Returns [] if the file doesn't exist or is empty."""
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def save_new_jobs(jobs, path):
    """
    Append only jobs we have never seen before. Two duplicate checks:
      1. Same job_id                      -> same posting seen in an earlier run
      2. Same company + title + countries -> same job already found on a DIFFERENT platform
    Returns the new rows.
    """
    master = load_master(path)
    existing_ids = {row["job_id"] for row in master}

    # For each company+title+countries key, remember which platforms already have it
    sources_by_key = {}
    for row in master:
        key = dedupe_key(row["company"], row["title"], row["countries"])
        sources_by_key.setdefault(key, set()).add(row["source"])

    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    new_rows = []
    cross_platform_dupes = 0

    # Company boards first, Adzuna last: if a job is on both, keep the direct link
    for job in sorted(jobs, key=lambda j: j["source"] == "adzuna"):
        if job["job_id"] in existing_ids:
            continue                                   # check 1: seen in an earlier run

        countries = ";".join(job["countries"])         # ["USA", "India"] -> "USA;India"
        key = dedupe_key(job["company"], job["title"], countries)
        seen_on = sources_by_key.get(key, set())
        if seen_on and job["source"] not in seen_on:
            cross_platform_dupes += 1                  # check 2: found on another platform
            continue

        existing_ids.add(job["job_id"])
        sources_by_key.setdefault(key, set()).add(job["source"])
        row = {col: job.get(col, "") for col in MASTER_COLUMNS}
        row["countries"] = countries
        row["found_at"] = now_utc
        new_rows.append(row)

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    write_header = not os.path.exists(path) or os.path.getsize(path) == 0
    with open(path, "a", newline="", encoding="utf-8") as f:   # "a" = append, never overwrite
        writer = csv.DictWriter(f, fieldnames=MASTER_COLUMNS)
        if write_header:
            writer.writeheader()
        writer.writerows(new_rows)

    if cross_platform_dupes:
        print(f"  Skipped {cross_platform_dupes} jobs already found on another platform")
    return new_rows


# ---------- Part 2: the daily country files ----------
def to_local(utc_text, tz):
    """'2026-10-07T18:30:00Z' -> datetime in your timezone. None if empty."""
    if not utc_text:
        return None
    return (datetime.strptime(utc_text, "%Y-%m-%dT%H:%M:%SZ")
            .replace(tzinfo=timezone.utc).astimezone(tz))


def display_row(row, tz):
    """Convert one master row into the friendly columns shown in daily files."""
    if row["source"] == "workable" and row["posted_at"]:
        posted = row["posted_at"][:10] + " (date only)"   # Workable gives no exact time
    else:
        local = to_local(row["posted_at"], tz)
        posted = local.strftime("%b %d, %I:%M %p") if local else "Unknown"

    return {
        "Posted": posted,
        "Title": row["title"],
        "Company": row["company"],
        "Location": row["location"],
        "Work Type": row["work_type"],
        "Experience": row["experience"],
        "Skills": row["skills_matched"],
        "Source": row["source"],
        "Apply Link": row["apply_link"],
    }


def build_daily_files(config):
    """Rebuild today's per-country CSV and Markdown files from the master CSV."""
    tz = ZoneInfo(config.get("timezone", "UTC"))
    today = datetime.now(tz).date()
    folder = os.path.join(config.get("output_folder", "jobs"), today.isoformat())
    os.makedirs(folder, exist_ok=True)

    # Jobs FOUND today (in your timezone), newest postings first
    todays = [row for row in load_master(config["master_file"])
              if row["found_at"] and to_local(row["found_at"], tz).date() == today]
    todays.sort(key=lambda row: row["posted_at"], reverse=True)

    for country in config["countries"]:     # one pair of files per configured country
        rows = [display_row(row, tz) for row in todays
                if country in row["countries"].split(";")]
        write_country_csv(os.path.join(folder, f"{country}.csv"), rows)
        write_country_md(os.path.join(folder, f"{country}.md"), country,
                         today.isoformat(), config.get("timezone", "UTC"), rows)
        print(f"  {country:<10} {len(rows):>4} jobs today -> {folder}")
    return folder


def write_country_csv(path, rows):
    # utf-8-sig makes Excel on Windows show special characters (é, –, ’) correctly
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=DISPLAY_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def md_safe(text):
    """'|' would break a Markdown table, so replace it."""
    return str(text).replace("|", "/").replace("\n", " ")


def write_country_md(path, country, date_text, tz_name, rows):
    lines = [f"# {country}: data jobs found on {date_text}", "",
             f"**{len(rows)} jobs** · newest first · times in {tz_name}", ""]

    if not rows:
        lines.append("_No new jobs yet. The scraper runs every hour._")
    else:
        headers = [c for c in DISPLAY_COLUMNS if c != "Apply Link"] + ["Apply"]
        lines.append("| " + " | ".join(headers) + " |")
        lines.append("|" + "---|" * len(headers))
        for row in rows:
            cells = [md_safe(row[c]) for c in DISPLAY_COLUMNS if c != "Apply Link"]
            cells.append(f"[Apply]({row['Apply Link']})")
            lines.append("| " + " | ".join(cells) + " |")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# Test mode: python -m writer
# Runs a small real pipeline (2 sources) so you can see the output files.
if __name__ == "__main__":
    from scrapers.common import load_config
    from scrapers import greenhouse, adzuna
    from filters import filter_jobs

    config = load_config()
    jobs = greenhouse.fetch_jobs("databricks", "Databricks", config) + adzuna.search_jobs(config)
    kept, _ = filter_jobs(jobs, config)

    new_rows = save_new_jobs(kept, config["master_file"])
    print(f"Saved {len(new_rows)} new jobs to {config['master_file']}")
    build_daily_files(config)