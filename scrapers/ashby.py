"""
Extract jobs from Ashby job boards.
API docs: https://developers.ashbyhq.com/docs/public-job-posting-api
No API key needed - these boards are public.
"""
from scrapers.common import fetch_json, clean_html, to_utc_iso, make_job, run_cli_test

API_URL = "https://api.ashbyhq.com/posting-api/job-board/{slug}"


def fetch_jobs(slug, company_name, config):
    """Return all open jobs for one company, in our standard format."""
    data = fetch_json(API_URL.format(slug=slug), timeout=config.get("request_timeout", 15))
    if not data:
        return []

    jobs = []
    for item in data.get("jobs", []):
        if item.get("isListed") is False:      # hidden job -> skip
            continue

        # Main location + any secondary locations
        locations = [item.get("location") or ""]
        for extra in item.get("secondaryLocations") or []:
            locations.append(extra.get("location") or "")

        address = (item.get("address") or {}).get("postalAddress") or {}
        work_type = item.get("workplaceType") or ("Remote" if item.get("isRemote") else "")

        jobs.append(make_job(
            job_id=item.get("id"),
            title=(item.get("title") or "").strip(),
            company=company_name,
            location="; ".join(loc for loc in locations if loc),
            description=clean_html(item.get("descriptionPlain") or item.get("descriptionHtml")),
            posted_at=to_utc_iso(item.get("publishedAt")),
            source="ashby",
            apply_link=item.get("jobUrl", ""),
            work_type=work_type,
            country_hint=address.get("addressCountry") or "",
        ))
    return jobs


if __name__ == "__main__":
    run_cli_test("ashby", fetch_jobs)