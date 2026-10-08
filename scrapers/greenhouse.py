"""
Extract jobs from Greenhouse job boards.
API docs: https://developers.greenhouse.io/job-board.html
No API key needed - these boards are public.
"""
from scrapers.common import fetch_json, clean_html, to_utc_iso, make_job, run_cli_test

API_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"


def fetch_jobs(slug, company_name, config):
    """Return all open jobs for one company, in our standard format."""
    data = fetch_json(API_URL.format(slug=slug), params={"content": "true"},
                      timeout=config.get("request_timeout", 15))
    if not data:
        return []

    jobs = []
    for item in data.get("jobs", []):
        jobs.append(make_job(
            job_id=item.get("id"),
            title=(item.get("title") or "").strip(),
            company=company_name,
            location=((item.get("location") or {}).get("name") or "").strip(),
            description=clean_html(item.get("content")),
            # first_published = original posting time; fall back to updated_at if missing
            posted_at=to_utc_iso(item.get("first_published") or item.get("updated_at")),
            source="greenhouse",
            apply_link=item.get("absolute_url", ""),
        ))
    return jobs


if __name__ == "__main__":
    run_cli_test("greenhouse", fetch_jobs)