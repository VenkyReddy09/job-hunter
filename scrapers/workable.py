"""
Extract jobs from Workable job boards (the public careers-page widget feed).
No API key needed - these boards are public.
Note: Workable only provides the posting DATE, not the exact time.
"""
from scrapers.common import fetch_json, clean_html, to_utc_iso, make_job, run_cli_test

API_URL = "https://apply.workable.com/api/v1/widget/accounts/{slug}"


def fetch_jobs(slug, company_name, config):
    """Return all open jobs for one company, in our standard format."""
    data = fetch_json(API_URL.format(slug=slug), params={"details": "true"},
                      timeout=config.get("request_timeout", 15))
    if not data:
        return []

    jobs = []
    for item in data.get("jobs", []):
        # Build location text from the visible locations list
        locations = []
        for loc in item.get("locations") or []:
            if loc.get("hidden"):
                continue
            locations.append(", ".join(
                part for part in (loc.get("city"), loc.get("region"), loc.get("country")) if part
            ))
        if not locations:   # fall back to the job's main location fields
            locations.append(", ".join(
                part for part in (item.get("city"), item.get("state"), item.get("country")) if part
            ))

        jobs.append(make_job(
            job_id=item.get("shortcode"),
            title=(item.get("title") or "").strip(),
            company=company_name,
            location="; ".join(loc for loc in locations if loc),
            description=clean_html(item.get("description")),
            posted_at=to_utc_iso(item.get("published_on") or item.get("created_at")),
            source="workable",
            apply_link=item.get("url") or item.get("shortlink") or "",
            work_type="Remote" if item.get("telecommuting") else "",
            country_hint=item.get("country") or "",
        ))
    return jobs


if __name__ == "__main__":
    run_cli_test("workable", fetch_jobs)