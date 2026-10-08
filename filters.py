"""
TRANSFORM step: decide which scraped jobs are relevant, and enrich them with
country, work type, experience and matched skills.

A job must pass every check, in this order (cheapest first):
  1. Title looks like a data job (and isn't too senior)
  2. Posted recently
  3. Located in one of your countries
  4. Mentions the required skills
  5. Asks for <= max years of experience (or doesn't say)
"""
import re
from datetime import datetime, timedelta, timezone

# Sources that only give a short description snippet.
# The skills check is skipped for these (the skills may be in the part we can't see).
SNIPPET_SOURCES = {"adzuna"}

# Location text that means "remote, no country given"
REMOTE_ONLY = {"remote", "anywhere", "worldwide", "global", "fully remote",
               "remote - anywhere", "remote, anywhere"}

# ---------- Experience patterns ----------
NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
NUM = r"\b(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten)"
YEARS = r"\s*(?:years|yrs)"

EXPERIENCE_PATTERNS = [
    re.compile(NUM + r"\s*\+?\s*(?:-|–|to)\s*" + NUM + r"\s*\+?" + YEARS, re.I),  # "3-5 years"
    re.compile(NUM + r"\s*\+" + YEARS, re.I),                                     # "3+ years"
    re.compile(r"(?:minimum(?:\s+of)?|at\s+least)\s+" + NUM + YEARS, re.I),       # "at least three years"
    re.compile(NUM + YEARS + r"\s+(?:of\s+)?(?:[\w-]+\s+){0,4}?experience", re.I),  # "4 years of data experience"
]


def word_pattern(keyword):
    """
    Build a regex that matches a keyword as a whole word.
    'SQL' matches "SQL, Python" but not "NoSQL"; 'intern' does not match "internal".
    """
    start = r"\b" if keyword[0].isalnum() else ""
    end = r"\b" if keyword[-1].isalnum() else ""
    return start + re.escape(keyword) + end


def has_any(text, keywords):
    """True if any keyword appears in text as a whole word (not case-sensitive)."""
    return any(re.search(word_pattern(k), text, re.I) for k in keywords)


# ---------- Check 1: title ----------
def title_ok(title, config):
    return (has_any(title, config["title_keywords"])
            and not has_any(title, config.get("exclude_title_keywords", [])))


# ---------- Check 2: freshness ----------
def is_recent(posted_at, max_days):
    if not posted_at or not max_days:      # unknown date -> keep it
        return True
    posted = datetime.strptime(posted_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - posted <= timedelta(days=max_days)


# ---------- Check 3: country ----------
def find_countries(job, config):
    """Return every configured country this job belongs to (can be more than one)."""
    found = []
    location = job["location"]
    hint = (job.get("country_hint") or "").strip().lower()

    for name, settings in config["countries"].items():
        keywords = settings.get("location_keywords", [])
        # Everything that can identify this country: its name, its code, and its keywords
        known = {name.lower(), (settings.get("adzuna_code") or "").lower()}
        known |= {k.lower() for k in keywords}

        if hint and hint in known:           # the platform told us the country directly
            found.append(name)
        elif has_any(location, keywords):    # otherwise read the location text
            found.append(name)

    if not found and location.strip().lower() in REMOTE_ONLY:
        default = config.get("remote_without_country_goes_to")
        if default:
            found.append(default)
    return found


# ---------- Check 4: skills ----------
def find_skills(text, skills):
    return [skill for skill in skills if re.search(word_pattern(skill), text, re.I)]


# ---------- Check 5: experience ----------
def to_number(text):
    return int(text) if text.isdigit() else NUMBER_WORDS.get(text.lower())


def extract_min_years(text):
    """
    Find every "X years" requirement in the text and return the SMALLEST.
    We use the smallest on purpose: it keeps borderline jobs rather than hiding them.
    Returns None if no requirement is found.
    """
    found = []
    for pattern in EXPERIENCE_PATTERNS:
        for match in pattern.finditer(text):
            found.append(to_number(match.group(1)))
    found = [n for n in found if n is not None and n <= 20]   # ignore noise like "100 years"
    return min(found) if found else None


# ---------- Enrichment: work type ----------
def detect_work_type(job):
    given = (job.get("work_type") or "").lower()     # what the platform said, if anything
    if "hybrid" in given:
        return "Hybrid"
    if "remote" in given:
        return "Remote"
    if "onsite" in given or "on-site" in given or "office" in given:
        return "Onsite"

    place = f"{job['title']} {job['location']}".lower()
    if "hybrid" in place:
        return "Hybrid"
    if "remote" in place:
        return "Remote"
    if "hybrid" in job["description"].lower():
        return "Hybrid"
    return "Not specified"


# ---------- The main function ----------
def filter_jobs(jobs, config):
    """Run every job through the 5 checks. Returns (kept_jobs, stats)."""
    stats = {"scraped": len(jobs), "removed_company": 0, "removed_title": 0, "removed_old": 0,
             "removed_country": 0, "removed_skills": 0, "removed_experience": 0}
    max_years = config.get("max_years_experience", 5)
    keep_unknown = config.get("keep_if_years_not_mentioned", True)
    kept = []
    blocked = {name.strip().lower() for name in config.get("exclude_companies", [])}

    for job in jobs:
        # 0. Blocked company
        if job["company"].strip().lower() in blocked:
            stats["removed_company"] += 1
            continue

        # 1. Title
        if not title_ok(job["title"], config):
            stats["removed_title"] += 1
            continue

        # 2. Freshness
        if not is_recent(job["posted_at"], config.get("max_job_age_days")):
            stats["removed_old"] += 1
            continue

        # 3. Country
        countries = find_countries(job, config)
        if not countries:
            stats["removed_country"] += 1
            continue

        # 4. Skills
        text = f"{job['title']} {job['description']}"
        required = find_skills(text, config["skills"])
        if job["source"] not in SNIPPET_SOURCES and len(required) < config.get("min_skills_match", 1):
            stats["removed_skills"] += 1
            continue

        # 5. Experience
        years = extract_min_years(job["description"])
        if years is None and not keep_unknown:
            stats["removed_experience"] += 1
            continue
        if years is not None and years > max_years:
            stats["removed_experience"] += 1
            continue

        # Passed everything -> enrich and keep.
        # {**job, ...} copies the job and adds new fields (the original stays unchanged).
        bonus = find_skills(text, config.get("bonus_skills", []))
        kept.append({
            **job,
            "countries": countries,
            "work_type": detect_work_type(job),
            "experience": f"{years}+ yrs" if years is not None else "Not specified",
            "skills_matched": ", ".join(required + bonus),
        })

    stats["kept"] = len(kept)
    return kept, stats


def print_stats(stats):
    print(f"Scraped:               {stats['scraped']}")
    print(f"  - blocked company:   {stats['removed_company']}")
    print(f"  - wrong title:       {stats['removed_title']}")
    print(f"  - too old:           {stats['removed_old']}")
    print(f"  - other country:     {stats['removed_country']}")
    print(f"  - missing skills:    {stats['removed_skills']}")
    print(f"  - too much exp.:     {stats['removed_experience']}")
    print(f"Kept:                  {stats['kept']}")


# Test mode: python -m filters
# Scrapes a few companies + Adzuna, then shows what survives the filters.
if __name__ == "__main__":
    from scrapers.common import load_config
    from scrapers import greenhouse, lever, ashby, adzuna

    config = load_config()
    jobs = (greenhouse.fetch_jobs("databricks", "Databricks", config)
            + ashby.fetch_jobs("ramp", "Ramp", config)
            + lever.fetch_jobs("palantir", "Palantir", config)
            + adzuna.search_jobs(config))

    kept, stats = filter_jobs(jobs, config)
    print_stats(stats)
    print()
    for job in kept[:15]:
        print(f"{job['title'][:40]:<40} | {job['company'][:14]:<14} | "
              f"{', '.join(job['countries']):<10} | {job['work_type']:<13} | "
              f"{job['experience']:<13} | {job['skills_matched']}")