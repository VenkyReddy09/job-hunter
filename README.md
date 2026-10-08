# Job Hunter

An automated ETL pipeline that finds **data engineering jobs** every hour from company career boards and job aggregators, filters them to your experience level, and publishes a clean daily report per country.

Built for my own job search across the **USA** and **India**, and designed so anyone can use it by editing two settings files.

## How it works

```
EXTRACT                          TRANSFORM                      LOAD
─────────────────────────        ───────────────────────        ──────────────────────────
Greenhouse  ┐                    1. Title is a data role        data/jobs.csv
Lever       │                    2. Posted in last 7 days         (master, deduplicated)
Ashby       ├─ standard  ──►     3. In a target country   ──►          │
SmartRecr.  │  job format        4. Mentions SQL/Python/Spark          ▼
Workable    │                    5. Experience ≤ 5 years        jobs/2026-10-08/USA.csv
Adzuna      ┘                                                   jobs/2026-10-08/India.md ...
```

- **Extract:** six connectors, one per platform, each returning jobs in the same standard format. Company boards are scraped in parallel with a thread pool.
- **Transform:** five checks run in order, cheapest first. Jobs are enriched with country, work type (Remote/Hybrid/Onsite), years of experience and matched skills.
- **Load:** new jobs are appended to a master CSV, which deduplicates by job ID and across platforms (same company + title + country). Daily per-country files are rebuilt from the master on every run, so the pipeline is idempotent.
- **Schedule:** GitHub Actions runs the pipeline every hour and commits the results.

## Daily output

```
jobs/
  2026-10-08/
    USA.csv     ← open in Excel to sort and filter
    USA.md      ← clean table with Apply links, readable on GitHub
    India.csv
    India.md
```

Each job appears once, in the folder for the day it was first found. Columns: Posted, Title, Company, Location, Work Type, Experience, Skills, Source, Apply link.

## Data sources

| Source | Type | Key needed | Notes |
|---|---|---|---|
| Greenhouse | Company ATS | No | Exact posting time |
| Lever | Company ATS | No | Exact posting time |
| Ashby | Company ATS | No | Exact posting time |
| SmartRecruiters | Company ATS | No | Only data-related titles fetched in detail |
| Workable | Company ATS | No | Posting **date** only (no time) |
| Adzuna | Job aggregator | Free key | Short description snippets only |

All sources are public, documented job APIs. No logins, no scraping of sites that forbid it.

## Run it yourself

```bash
git clone https://github.com/VenkyReddy09/job-hunter.git
cd job-hunter
python -m venv .venv
# Mac/Linux: source .venv/bin/activate    Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Optional: create a `.env` file with a free key from [developer.adzuna.com](https://developer.adzuna.com):
```
ADZUNA_APP_ID=your_id
ADZUNA_APP_KEY=your_key
```

Run:
```bash
python main.py            # normal run
python main.py --adzuna   # force Adzuna to run this time
```

## Customize

Everything is controlled by two files, so you never need to edit the code.

**`config.yaml`** sets countries, job titles, required skills, maximum years of experience, timezone, and how fresh jobs must be. Add a country and you get a new file per day.

**`companies.csv`** lists the companies to monitor:
```
platform,slug,name
greenhouse,stripe,Stripe
lever,palantir,Palantir
```
The slug comes from the company's job board URL, e.g. `boards.greenhouse.io/stripe` gives `stripe`.

Each connector has a test mode:
```bash
python -m scrapers.greenhouse          # check every Greenhouse company in companies.csv
python -m scrapers.greenhouse stripe   # show sample jobs from one company
```

## Project structure

```
scrapers/          one file per platform + common.py (shared helpers, standard job format)
filters.py         the five checks and enrichment
writer.py          master CSV, deduplication, daily country files
main.py            runs the whole pipeline
config.yaml        search settings
companies.csv      companies to monitor
```

## Known limitations

- Experience is read from text patterns ("3+ years", "at least three years"). The smallest number found is used, so borderline jobs stay visible rather than hidden.
- Companies on Workday and other ATS platforms without open APIs are not covered directly; Adzuna partly fills that gap.
- Adzuna provides short snippets, so experience is often "Not specified" for those jobs.

## Roadmap

- [ ] Grow `companies.csv` to hundreds of companies
- [ ] Tailor a base resume to each job using a free local LLM (Ollama)
- [ ] Simple web dashboard

## License

MIT, free to use and modify.

## Author

Lakshmi Venkateswara Reddy Nevuri ([GitHub](https://github.com/VenkyReddy09))