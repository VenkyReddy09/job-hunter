"""
Extract jobs from Greenhouse job boards.
API docs: https://developers.greenhouse.io/job-board.html
No API key needed - these boards are public.

Two-step fetch, so we download far less data:
  1. Download the job LIST without descriptions (small and fast).
  2. Download the full description only for jobs that are recent AND have a
     relevant title. Every other job is still returned (with an empty
     description), so the pipeline's stats stay complete; the filters drop them.
"""
from concurrent.futures import ThreadPoolExecutor

from scrapers.common import (fetch_json, clean_html, to_utc_iso, make_job, run_cli_test,
                             title_matches, posted_within)

LIST_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
DETAIL_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{job_id}"
WORKERS = 5   # parallel detail downloads inside one company


def fetch_jobs(slug, company_name, config):
    """Return all open jobs for one company, in our standard format."""
    timeout = config.get("request_timeout", 15)

    # ---------- Step 1: the light list (no descriptions) ----------
    data = fetch_json(LIST_URL.format(slug=slug), timeout=timeout)
    if not data:
        return []

    jobs = []
    for item in data.get("jobs", []):
        jobs.append(make_job(
            job_id=item.get("id"),
            title=(item.get("title") or "").strip(),
            company=company_name,
            location=((item.get("location") or {}).get("name") or "").strip(),
            description="",                  # filled in step 2, only for relevant jobs
            # first_published = original posting time; fall back to updated_at if missing
            posted_at=to_utc_iso(item.get("first_published") or item.get("updated_at")),
            source="greenhouse",
            apply_link=item.get("absolute_url", ""),
        ))

    # ---------- Step 2: descriptions only for recent, relevant jobs ----------
    keywords = config.get("title_keywords", [])
    max_days = config.get("max_job_age_days")
    relevant = [job for job in jobs
                if title_matches(job["title"], keywords)
                and posted_within(job["posted_at"], max_days)]

    def add_description(job):
        raw_id = job["job_id"].split("-", 1)[1]          # "greenhouse-123" -> "123"
        detail = fetch_json(DETAIL_URL.format(slug=slug, job_id=raw_id), timeout=timeout) or {}
        job["description"] = clean_html(detail.get("content"))

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        list(pool.map(add_description, relevant))

    return jobs


if __name__ == "__main__":
    run_cli_test("greenhouse", fetch_jobs)
