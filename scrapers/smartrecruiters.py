"""
Extract jobs from SmartRecruiters job boards.
API docs: https://developers.smartrecruiters.com/docs/posting-api
No API key needed - these postings are public.
"""
from scrapers.common import (fetch_json, clean_html, to_utc_iso, make_job,
                             run_cli_test, title_matches)

from concurrent.futures import ThreadPoolExecutor

WORKERS = 10   # parallel requests inside one SmartRecruiters company

LIST_URL = "https://api.smartrecruiters.com/v1/companies/{slug}/postings"
DETAIL_URL = "https://api.smartrecruiters.com/v1/companies/{slug}/postings/{posting_id}"
PAGE_SIZE = 100   # SmartRecruiters returns at most 100 jobs per request


def fetch_jobs(slug, company_name, config):
    """Return relevant open jobs for one company, in our standard format."""
    timeout = config.get("request_timeout", 15)
    title_keywords = config.get("title_keywords", [])

    def get_page(offset):
        return fetch_json(LIST_URL.format(slug=slug),
                          params={"limit": PAGE_SIZE, "offset": offset},
                          timeout=timeout) or {}

    # Page 1 tells us how many jobs exist in total
    first = get_page(0)
    total = first.get("totalFound", 0)
    postings = list(first.get("content", []))

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        # Fetch all remaining pages at the same time
        for page in pool.map(get_page, range(PAGE_SIZE, total, PAGE_SIZE)):
            postings.extend(page.get("content", []))

        # Keep only promising titles, then fetch their details at the same time
        relevant = [item for item in postings
                    if not title_keywords or title_matches(item.get("name"), title_keywords)]
        jobs = list(pool.map(lambda item: build_job(slug, company_name, item, timeout), relevant))

    return jobs


def build_job(slug, company_name, item, timeout):
    """Download one job's full details and convert it to our standard format."""
    posting_id = item.get("id")
    detail = fetch_json(DETAIL_URL.format(slug=slug, posting_id=posting_id), timeout=timeout) or {}

    # The description is split into sections; combine the useful ones
    sections = (detail.get("jobAd") or {}).get("sections") or {}
    description = " ".join(
        clean_html((sections.get(name) or {}).get("text"))
        for name in ("jobDescription", "qualifications", "additionalInformation")
    )

    location = item.get("location") or {}
    location_text = location.get("fullLocation") or ", ".join(
        part for part in (location.get("city"), location.get("region"),
                          (location.get("country") or "").upper()) if part
    )

    if location.get("remote"):
        work_type = "Remote"
    elif location.get("hybrid"):
        work_type = "Hybrid"
    else:
        work_type = ""

    return make_job(
        job_id=posting_id,
        title=(item.get("name") or "").strip(),
        company=company_name,
        location=location_text,
        description=description,
        posted_at=to_utc_iso(item.get("releasedDate")),
        source="smartrecruiters",
        apply_link=detail.get("postingUrl") or f"https://jobs.smartrecruiters.com/{slug}/{posting_id}",
        work_type=work_type,
        country_hint=location.get("country") or "",   # e.g. "us", "in"
    )


if __name__ == "__main__":
    run_cli_test("smartrecruiters", fetch_jobs)