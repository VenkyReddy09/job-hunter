"""
Extract jobs from Lever job boards.
API docs: https://github.com/lever/postings-api
No API key needed - these boards are public.
"""
from scrapers.common import fetch_json, clean_html, to_utc_iso, make_job, run_cli_test

API_URL = "https://api.lever.co/v0/postings/{slug}"


def fetch_jobs(slug, company_name, config):
    """Return all open jobs for one company, in our standard format."""
    data = fetch_json(API_URL.format(slug=slug), params={"mode": "json"},
                      timeout=config.get("request_timeout", 15))
    # Lever returns a LIST of jobs. Anything else means an error (e.g. wrong slug).
    if not isinstance(data, list):
        return []

    jobs = []
    for item in data:
        categories = item.get("categories") or {}
        locations = categories.get("allLocations") or [categories.get("location") or ""]

        # Main description + the "lists" sections (requirements live there) + extra info
        parts = [item.get("descriptionPlain") or ""]
        for section in item.get("lists") or []:
            parts.append(section.get("text") or "")
            parts.append(clean_html(section.get("content")))
        parts.append(item.get("additionalPlain") or "")

        jobs.append(make_job(
            job_id=item.get("id"),
            title=(item.get("text") or "").strip(),
            company=company_name,
            location="; ".join(loc for loc in locations if loc),
            description=clean_html(" ".join(parts)),
            posted_at=to_utc_iso(item.get("createdAt")),   # milliseconds -> UTC
            source="lever",
            apply_link=item.get("hostedUrl", ""),
            work_type=item.get("workplaceType") or "",     # onsite / hybrid / remote
            country_hint=item.get("country") or "",        # e.g. "US", "IN"
        ))
    return jobs


if __name__ == "__main__":
    run_cli_test("lever", fetch_jobs)