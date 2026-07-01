# Doctor Review Parser

This project builds a fault-tolerant parsing pipeline for doctor profiles and reviews.
It uses Django ORM for persistence, supports sync and async fetching, saves raw HTML,
and exports flat CSV or JSON files ready for downstream BI tools.

## Output Schema

The final export contains one row per review:

1. `clinic`
2. `doctor`
3. `source_site`
4. `doctor_profile_url`
5. `review_text`
6. `review_published_at`
7. `review_rating`

Doctors without reviews are still exported as a single row with empty review fields.
The source name in export is taken from the `SourceSite` entity.

## Project Structure

```text
parser_classified/
|-- apps/reviews/
|   |-- management/commands/run_pipeline.py
|   |-- models.py
|   |-- urls.py
|   |-- views.py
|   `-- services/
|       |-- browser.py
|       |-- exporters.py
|       |-- input_loader.py
|       |-- matching.py
|       |-- orchestrator.py
|       `-- validators.py
|-- config/pipeline.yaml
|-- data/
|   |-- exports/
|   `-- raw/
|-- parsers/
|   |-- base.py
|   |-- docdoc.py
|   |-- doctu.py
|   |-- napopravku.py
|   `-- prodoctorov.py
|-- parser_project/settings.py
|-- requirements.txt
`-- tests/
```

## Data Flow

1. Read CSV inputs from `/Users/imac/Desktop/trae/docs`.
2. Detect source site from file name.
3. Normalize doctor names and merge the same doctor across platforms.
4. Normalize profile URLs and deduplicate source links before persistence.
5. Create or update `SourceSite`, `Clinic`, `Doctor`, `DoctorSource`, and `Review` records in SQLite.
6. Skip duplicate reviews already present in DB by `external_id` or content fingerprint.
7. Fetch profile pages with retries, backoff, and blocked-page detection.
8. Switch blocked sources to `Selenium` with local Chrome when browser fallback is enabled.
9. Optionally wait for a local visible-browser confirmation flow for blocked sources such as `DocDoc` and `Doctu`.
10. Save raw HTML snapshots to `data/raw`.
11. Export flat files to `data/exports`.

## Setup

```bash
cd /Users/imac/Desktop/trae/parser_classified
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python manage.py makemigrations reviews
python manage.py migrate
```

Current package set targets `Django 4.2 LTS`.
If several Python versions are installed locally, prefer the interpreter you want to use for deployment when creating `.venv`.
For browser fallback you also need local Google Chrome or Chromium installed.

## Run

Bootstrap DB and export current grouped doctor list without fetching pages:

```bash
python manage.py run_pipeline --skip-fetch
```

Full sync fetch:

```bash
python manage.py run_pipeline --mode sync
```

Full async fetch:

```bash
python manage.py run_pipeline --mode async
```

Visible browser assisted run for blocked sources:

```bash
python manage.py run_pipeline --mode sync --browser-assisted
```

Run web UI:

```bash
python manage.py runserver
```

Then open `http://127.0.0.1:8000/`.

Browser behavior is configured in `config/pipeline.yaml`:

- `browser.enabled`: turns Selenium fallback on or off
- `browser.headless`: run Chrome in headless mode
- `browser.assisted_mode`: enables visible local Chrome flow
- `sources.<site>.use_browser_fallback`: enable browser mode for a specific platform
- `sources.<site>.manual_browser_assist`: wait for the user to pass the interstitial page in local Chrome

## HTMX UI

The project now includes a separate HTMX-based operator panel for the parser.

Available actions in the UI:

- start `sync` or `async` pipeline runs
- run `skip-fetch` export-only jobs
- enable `browser-assisted` mode for blocked sources
- watch the active job status update automatically
- watch detailed live progress: current doctor, site, URL, counters, and recent event log
- inspect recent runs and source health
- inspect a separate blocked queue prepared for semi-automatic follow-up
- download the latest CSV or JSON export

Semi-automatic blocked flow:

1. Run the main pass over the full source list.
2. Sources that end in `blocked` and have `manual_browser_assist=true` appear in the blocked queue.
3. Start the dedicated blocked follow-up run from the HTMX panel.
4. The follow-up run processes only that queue in browser-assisted mode.

UI implementation:

- page routing: `apps/reviews/urls.py`
- controller views: `apps/reviews/views.py`
- async job persistence: `PipelineJob` in `apps/reviews/models.py`
- background runner: `apps/reviews/services/pipeline_jobs.py`
- HTMX templates: `apps/reviews/templates/reviews/`

## Admin Panel

The admin panel supports manual management of entities in the form:

1. `doctor` with `last_name`, `first_name`, `middle_name`
2. one or more source links for that doctor
3. site selection for each link

Useful admin screens:

- `SourceSite`: manage websites and per-site browser strategy.
- `Doctor`: manage doctor cards in the form `last_name / first_name / middle_name` and attach multiple source links inline.
- `DoctorSource`: inspect crawl status, retry state, error text, and last review count.
- `Review`: inspect saved reviews and verify deduplication.

Create an admin user:

```bash
python manage.py createsuperuser
python manage.py runserver
```

## Scheduling

Example cron entry:

```bash
0 3 * * * cd /Users/imac/Desktop/trae/parser_classified && /Users/imac/Desktop/trae/parser_classified/.venv/bin/python manage.py run_pipeline --mode sync
```

## Current Source Status

- `prodoctorov`: confirmed live HTML, reviews are detectable by the parser.
- `napopravku`: `Selenium + Chrome` opens the profile page successfully in browser fallback mode.
- `doctu`: normal HTTP returns an anti-bot interstitial page; use `--browser-assisted` to open local Chrome and continue after manual confirmation.
- `docdoc`: returns `403` on HTTP; use `--browser-assisted` to open local Chrome and continue after manual confirmation.

## Notes

- Parser strategy first tries JSON-LD review objects, then falls back to review microdata.
- Raw HTML is stored incrementally to reduce data loss during partial failures.
- Export is flattened for Looker Studio compatibility.
- Source links are normalized and deduplicated in both the input loader and the database maintenance phase.
- Existing duplicate reviews are not inserted twice because the pipeline checks `external_id` and a stable review fingerprint before save.
- Browser fallback uses local Chrome via `Selenium`, so it does not require proxy, cookies, or an external scraping browser service.
- The project does not automate CAPTCHA or anti-bot bypass. For blocked pages it supports a local user-assisted browser flow instead.
