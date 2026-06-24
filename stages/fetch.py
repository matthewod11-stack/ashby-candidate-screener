"""Stage 1: Fetch candidates from Ashby API and download resume PDFs.

Connects to Ashby, finds the target job, paginates through applications,
downloads resume PDFs, and caches them locally.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import httpx

from models import RawCandidate, save_json

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ASHBY_BASE_URL = "https://api.ashbyhq.com"
MAX_PER_PAGE = 100
MAX_RETRIES = 3
INITIAL_BACKOFF = 1.0  # seconds
DEFAULT_FETCH_CONCURRENCY = 10  # conservative; Ashby rate limits ~allow this


# ---------------------------------------------------------------------------
# F004 — Ashby API Client
# ---------------------------------------------------------------------------


def _auth() -> httpx.BasicAuth:
    """Return HTTP Basic Auth using the ASHBY_API_KEY env var."""
    api_key = os.environ.get("ASHBY_API_KEY")
    if not api_key:
        raise RuntimeError("ASHBY_API_KEY environment variable is required")
    return httpx.BasicAuth(username=api_key, password="")


async def _post_with_retry(
    client: httpx.AsyncClient,
    endpoint: str,
    payload: dict,
) -> dict:
    """POST to an Ashby endpoint with exponential backoff on 429s.

    Returns the parsed JSON response body.
    Raises on non-retryable HTTP errors.
    """
    url = f"{ASHBY_BASE_URL}/{endpoint}"
    backoff = INITIAL_BACKOFF

    for attempt in range(1, MAX_RETRIES + 1):
        resp = await client.post(url, json=payload)

        if resp.status_code == 429:
            retry_after = float(resp.headers.get("Retry-After", backoff))
            if attempt == MAX_RETRIES:
                resp.raise_for_status()
            print(f"  Rate limited on {endpoint}, retrying in {retry_after:.1f}s "
                  f"(attempt {attempt}/{MAX_RETRIES})")
            await asyncio.sleep(retry_after)
            backoff *= 2
            continue

        if resp.status_code in (502, 503, 504):
            if attempt == MAX_RETRIES:
                resp.raise_for_status()
            print(f"  Server error {resp.status_code} on {endpoint}, retrying in {backoff:.1f}s "
                  f"(attempt {attempt}/{MAX_RETRIES})")
            await asyncio.sleep(backoff)
            backoff *= 2
            continue

        resp.raise_for_status()
        return resp.json()

    # Should never reach here, but satisfy type checkers
    raise RuntimeError(f"Exhausted retries for {endpoint}")  # pragma: no cover


async def list_jobs(
    client: httpx.AsyncClient,
    title_filter: str | None = None,
) -> list[dict]:
    """POST /job.list -- return all jobs, optionally filtered by title substring."""
    data = await _post_with_retry(client, "job.list", {})
    jobs = data.get("results", [])

    if title_filter:
        needle = title_filter.lower()
        jobs = [j for j in jobs if needle in j.get("title", "").lower()]

    return jobs


async def list_applications(
    client: httpx.AsyncClient,
    job_id: str,
    cursor: str | None = None,
) -> dict:
    """POST /application.list -- one page of applications for a job.

    Returns the raw response dict containing 'results' and 'nextCursor'.
    """
    payload: dict = {"jobId": job_id, "limit": MAX_PER_PAGE}
    if cursor:
        payload["cursor"] = cursor

    return await _post_with_retry(client, "application.list", payload)


async def get_candidate_info(
    client: httpx.AsyncClient,
    candidate_id: str,
) -> dict:
    """POST /candidate.info -- full candidate details including fileHandles."""
    data = await _post_with_retry(client, "candidate.info", {"id": candidate_id})
    return data.get("results", data)


async def get_file_info(
    client: httpx.AsyncClient,
    file_handle: str,
) -> dict:
    """POST /file.info -- returns presigned download URL for a resume file."""
    data = await _post_with_retry(client, "file.info", {"fileHandle": file_handle})
    return data.get("results", data)


# ---------------------------------------------------------------------------
# F005 — Candidate Fetcher (orchestration)
# ---------------------------------------------------------------------------


async def _download_pdf(
    download_url: str,
    dest: Path,
) -> None:
    """Download a file from a presigned S3 URL and save to dest.

    Uses a fresh unauthenticated client — presigned URLs must not carry
    extra auth headers or S3 returns 400.
    """
    async with httpx.AsyncClient(timeout=60.0) as dl_client:
        resp = await dl_client.get(download_url, follow_redirects=True)
        resp.raise_for_status()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(resp.content)


async def _fetch_all_applications(
    client: httpx.AsyncClient,
    job_id: str,
    date_cutoff: str | None,
) -> list[dict]:
    """Paginate through ALL applications for a job.

    Inclusion rules:
    - All candidates from date_cutoff onward (any status — Active, Archived, Lead)
    - Active candidates from before date_cutoff (still in pipeline)
    """
    all_apps: list[dict] = []
    skipped = 0
    cursor: str | None = None
    page = 0

    while True:
        page += 1
        resp = await list_applications(client, job_id, cursor=cursor)
        results = resp.get("results", [])

        for app in results:
            app_date = app.get("createdAt", "")
            app_status = app.get("status", "")

            if date_cutoff and app_date < date_cutoff:
                # Pre-cutoff: only include Active candidates (still in pipeline)
                if app_status == "Active":
                    all_apps.append(app)
                else:
                    skipped += 1
            else:
                # Post-cutoff: include ALL candidates regardless of status
                all_apps.append(app)

        next_cursor = resp.get("nextCursor")
        print(f"  Page {page}: {len(results)} applications "
              f"({len(all_apps)} kept so far)")

        if not next_cursor:
            break
        cursor = next_cursor

    if skipped:
        print(f"  Skipped {skipped} pre-{date_cutoff} non-active candidates")

    return all_apps


async def _process_candidate(
    client: httpx.AsyncClient,
    app: dict,
    pdf_dir: Path,
    index: int,
    total: int,
) -> tuple[RawCandidate, bool]:
    """Process a single application: fetch candidate info, download resume.

    Returns (RawCandidate, has_resume).
    """
    sparse_candidate = app.get("candidate", {})
    candidate_id = sparse_candidate.get("id", "")

    # Fetch full candidate details
    cand = await get_candidate_info(client, candidate_id)

    name = cand.get("name", "Unknown")
    email_list = cand.get("emailAddresses", [])
    email = email_list[0].get("value", "") if email_list else ""
    application_date = app.get("createdAt", "")
    source = app.get("source", {})
    source_name = source.get("title") if isinstance(source, dict) else str(source) if source else None
    status = app.get("status", None)
    stage_info = app.get("currentInterviewStage", {})
    ashby_stage = stage_info.get("title") if isinstance(stage_info, dict) else str(stage_info) if stage_info else None

    # Location data
    location = cand.get("location") or {}
    location_summary = location.get("locationSummary") if isinstance(location, dict) else None
    location_country = None
    if isinstance(location, dict):
        for comp in location.get("locationComponents", []):
            if comp.get("type") == "Country":
                location_country = comp.get("name")
                break

    phone_list = cand.get("phoneNumbers", [])
    phone_number = phone_list[0].get("value", "") if phone_list else None
    tz = cand.get("timezone")

    # Profile URL from candidate.info or construct one against the Ashby UI.
    profile_url = cand.get(
        "profileUrl",
        f"https://app.ashbyhq.com/candidates/{candidate_id}",
    )

    # Resume: check fileHandles from candidate.info
    file_handles = cand.get("fileHandles", [])
    file_handle: str | None = None
    resume_path: str | None = None
    has_resume = False

    if file_handles:
        fh_obj = file_handles[0]  # take the first file
        file_handle = fh_obj.get("handle", "")
        pdf_path = pdf_dir / f"{candidate_id}.pdf"

        # Cache: skip download if already on disk
        if pdf_path.exists():
            resume_path = str(pdf_path)
            has_resume = True
        else:
            try:
                file_info = await get_file_info(client, file_handle)
                download_url = file_info.get("url", "")
                if download_url:
                    await _download_pdf(download_url, pdf_path)
                    resume_path = str(pdf_path)
                    has_resume = True
                else:
                    print(f"  [{index}/{total}] WARNING: No download URL for "
                          f"{name} ({candidate_id})")
            except Exception as exc:
                print(f"  [{index}/{total}] WARNING: Failed to download resume "
                      f"for {name} ({candidate_id}): {exc}")
    else:
        print(f"  [{index}/{total}] No resume for {name} ({candidate_id})")

    rc = RawCandidate(
        candidate_id=candidate_id,
        name=name,
        email=email,
        application_date=application_date,
        profile_url=profile_url,
        source=source_name,
        status=status,
        ashby_stage=ashby_stage,
        resume_path=resume_path,
        file_handle=file_handle,
        phone_number=phone_number,
        location_summary=location_summary,
        location_country=location_country,
        timezone=tz,
    )
    return rc, has_resume


async def _async_run_fetch(config: dict) -> Path:
    """Async core of run_fetch."""

    role_title = config["role"]["title"]
    date_cutoff = config["role"].get("application_date_cutoff")
    pdf_dir = Path(config.get("shared_pdf_dir", "data/pdfs"))
    data_dir = Path(config.get("data_dir", "data"))
    output_path = data_dir / "results" / "stage1_candidates.json"
    pdf_dir.mkdir(parents=True, exist_ok=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    auth = _auth()

    async with httpx.AsyncClient(auth=auth, timeout=60.0) as client:

        # 1. Find job by title
        print(f"Stage 1 (Fetch): Looking for job '{role_title}'...")
        jobs = await list_jobs(client, title_filter=role_title)
        if not jobs:
            raise RuntimeError(
                f"No job found matching title '{role_title}'. "
                "Check config/criteria.yaml role.title."
            )
        job = jobs[0]
        job_id = job["id"]
        print(f"  Found job: {job['title']} (id={job_id})")

        # 2. Paginate all applications
        print("  Fetching applications...")
        applications = await _fetch_all_applications(client, job_id, date_cutoff)
        print(f"  Total applications after date filter: {len(applications)}")

        # 3. Process each application in parallel (capped by semaphore).
        # Each task makes up to 2 Ashby API calls + 1 S3 PDF download. The
        # existing _post_with_retry handles 429s via exponential backoff so
        # the system degrades gracefully if Ashby pushes back on concurrency.
        candidates: list[RawCandidate] = []
        with_resume = 0
        without_resume = 0
        total = len(applications)
        concurrency = int(config.get("fetch", {}).get("concurrency", DEFAULT_FETCH_CONCURRENCY))
        sem = asyncio.Semaphore(concurrency)
        completed = [0]  # mutable counter shared across tasks (asyncio is single-threaded)

        print(f"  Processing {total} candidates (concurrency={concurrency})...")

        async def _bounded(idx: int, app: dict) -> tuple[RawCandidate, bool]:
            async with sem:
                result = await _process_candidate(client, app, pdf_dir, idx, total)
                completed[0] += 1
                if completed[0] == 1 or completed[0] == total or completed[0] % 50 == 0:
                    print(f"  Progress: {completed[0]}/{total} candidates processed")
                return result

        results = await asyncio.gather(
            *(_bounded(i + 1, app) for i, app in enumerate(applications)),
            return_exceptions=True,
        )

        failures = 0
        for i, result in enumerate(results, 1):
            if isinstance(result, Exception):
                failures += 1
                print(f"  ERROR: candidate fetch failed at index {i}: "
                      f"{result.__class__.__name__}: {result}")
                continue
            rc, has_resume = result
            candidates.append(rc)
            if has_resume:
                with_resume += 1
            else:
                without_resume += 1

        if failures:
            print(f"  WARNING: {failures} candidates failed to fetch (continuing with {len(candidates)} successes)")

        # 4. Stage 1 output
        print(f"\n  Fetched {len(candidates)} candidates, "
              f"{with_resume} with resumes, "
              f"{without_resume} skipped (no resume)")

        save_json([c.__dict__ for c in candidates], output_path)
        print(f"  Output saved to {output_path}")

    return output_path


# ---------------------------------------------------------------------------
# F006 — Public entry point (sync wrapper)
# ---------------------------------------------------------------------------


def run_fetch(config: dict) -> Path:
    """Fetch candidates from Ashby and download resumes.

    Args:
        config: Parsed criteria.yaml + env vars.

    Returns:
        Path to stage1_candidates.json output file.
    """
    return asyncio.run(_async_run_fetch(config))
