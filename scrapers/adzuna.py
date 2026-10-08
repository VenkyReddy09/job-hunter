"""
Search Adzuna, a job search engine (aggregator) covering many countries.
API docs: https://developer.adzuna.com/docs/search
Needs a FREE app_id and app_key, stored in .env (never in code).
"""
import os

from dotenv import load_dotenv

from scrapers.common import fetch_json, clean_html, to_utc_iso, make_job

API_URL = "https://api.adzuna.com/v1/api/jobs/{country_code}/search/{page}"

# Read .env into environment variables.
# On GitHub Actions there is no .env file; secrets are already environment variables.
load_dotenv()


def search_jobs(config):
    """Search Adzuna for every search term in every country. Returns unique jobs."""
    app_id = os.getenv("ADZUNA_APP_ID")
    app_key = os.getenv("ADZUNA_APP_KEY")
    if not app_id or not app_key:
        print("  ! Adzuna keys missing - skipping Adzuna. Add them to your .env file.")
        return []

    settings = config.get("adzuna", {})
    timeout = config.get("request_timeout", 15)
    per_page = settings.get("results_per_page", 50)

    # A dictionary keyed by job_id removes duplicates automatically:
    # the same job found by two search terms is stored only once.
    jobs_by_id = {}

    for country_name, country in config["countries"].items():
        code = country.get("adzuna_code")
        if not code:
            continue

        for term in settings.get("search_terms", []):
            for page in range(1, settings.get("max_pages", 1) + 1):
                data = fetch_json(
                    API_URL.format(country_code=code, page=page),
                    params={
                        "app_id": app_id,
                        "app_key": app_key,
                        "title_only": term,
                        "max_days_old": settings.get("max_days_old", 1),
                        "results_per_page": per_page,
                        "sort_by": "date",
                        "content-type": "application/json",
                    },
                    timeout=timeout,
                )
                results = (data or {}).get("results", [])
                for item in results:
                    job = convert(item, country_name)
                    jobs_by_id[job["job_id"]] = job

                if len(results) < per_page:   # fewer than a full page = no more pages
                    break

    return list(jobs_by_id.values())


def convert(item, country_name):
    """Convert one Adzuna result into our standard job format."""
    return make_job(
        job_id=item.get("id"),
        title=clean_html(item.get("title")),            # Adzuna titles can contain <strong> tags
        company=(item.get("company") or {}).get("display_name") or "Unknown",
        location=(item.get("location") or {}).get("display_name") or "",
        description=clean_html(item.get("description")),  # short snippet only
        posted_at=to_utc_iso(item.get("created")),
        source="adzuna",
        apply_link=item.get("redirect_url", ""),
        country_hint=country_name,   # we searched this country, so we KNOW the country
    )


# Test mode: python -m scrapers.adzuna
if __name__ == "__main__":
    from scrapers.common import load_config

    config = load_config()
    jobs = search_jobs(config)
    print(f"Found {len(jobs)} unique Adzuna jobs\n")

    for country_name in config["countries"]:
        count = sum(1 for job in jobs if job["country_hint"] == country_name)
        print(f"{country_name:<10} {count:>4} jobs")

    for job in jobs[:3]:
        print("-" * 60)
        print("Title:   ", job["title"])
        print("Company: ", job["company"])
        print("Location:", job["location"])
        print("Posted:  ", job["posted_at"])
        print("Link:    ", job["apply_link"])
        print("Desc:    ", job["description"][:150], "...")