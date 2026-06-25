"""Ashby Candidate Ranking Pipeline — Orchestrator.

Runs stages 1-5 sequentially. Each stage reads from the previous stage's
output file.

Usage:
    python run.py                          # Default role (staff-backend-engineer)
    python run.py --role bdr               # Run for a specific role
    python run.py --all                    # Run for all configured roles
    python run.py --resume                 # Skip stages whose output already exists
    python run.py --role bdr --resume      # Combine flags
"""

import argparse
import json
import re
import sys
import time
import traceback
from collections import Counter
from datetime import date
from pathlib import Path

_ROLE_SLUG_RE = re.compile(r"[a-z0-9-]+")

import yaml
from dotenv import load_dotenv

from stages.fetch import run_fetch
from stages.sources import resolve_source
from stages.triage import run_triage
from stages.deep_eval import run_deep_eval
from stages.synthesize import run_synthesize
from stages.report import run_report
from stages.slack_summary import generate_slack_summary
from state import (
    load_state,
    save_state,
    filter_new_candidates,
    get_cached_evals,
    update_state,
    state_summary,
)
from models import load_json, save_json
from usage import UsageTracker


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------


def load_role_config(slug: str, source: str | None = None, input_dir: str | None = None) -> dict:
    """Load config for a specific role from config/roles/{slug}.yaml."""
    if not _ROLE_SLUG_RE.fullmatch(slug):
        raise SystemExit(
            f"Invalid --role slug: {slug!r}. Use lowercase letters, digits, and hyphens only."
        )
    load_dotenv()
    config_path = Path(f"config/roles/{slug}.yaml")
    if not config_path.exists():
        print(f"Error: Role config not found: {config_path}")
        sys.exit(1)
    with open(config_path) as f:
        config = yaml.safe_load(f)
    config["today"] = date.today().isoformat()
    config["role_slug"] = slug
    config["data_dir"] = Path(f"data/roles/{slug}")
    config["shared_pdf_dir"] = Path("data/pdfs")
    # Load JD text into config for prompt builders
    jd_path = Path("config/roles") / config["role"].get("jd_file", f"{slug}.md")
    if jd_path.exists():
        config["jd_text"] = jd_path.read_text()
    if source:
        config["source"] = source
    if input_dir:
        config["input_dir"] = input_dir
    return config


def list_available_roles() -> list[str]:
    """List all available role slugs from config/roles/*.yaml."""
    roles_dir = Path("config/roles")
    return sorted(p.stem for p in roles_dir.glob("*.yaml"))


def _stage_outputs(data_dir: Path) -> dict[int, Path]:
    """Return stage output paths relative to a role's data directory."""
    return {
        1: data_dir / "results" / "stage1_candidates.json",
        2: data_dir / "results" / "stage2_triage.json",
        3: data_dir / "results" / "stage3_deep_evals.json",
        4: data_dir / "results" / "stage4_synthesis.json",
    }


def _fmt_duration(seconds: float) -> str:
    """Format seconds into a human-readable string."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    secs = seconds % 60
    return f"{minutes}m {secs:.0f}s"


def _validate_json_output(path: Path, stage_name: str) -> list | dict:
    """Validate that a JSON output file exists and is parseable."""
    if not path.exists():
        raise FileNotFoundError(f"{stage_name} output not found: {path}")
    data = json.loads(path.read_text())
    if not data:
        raise ValueError(f"{stage_name} output is empty: {path}")
    return data


def _should_skip(stage_num: int, resume: bool, stage_outputs: dict[int, Path]) -> bool:
    """Check if a stage should be skipped (output exists and --resume set)."""
    if not resume:
        return False
    output = stage_outputs.get(stage_num)
    if output and output.exists():
        return True
    return False


# ---------------------------------------------------------------------------
# Single-role pipeline
# ---------------------------------------------------------------------------


def run_role(config: dict, resume: bool = False) -> tuple[Path, Path]:
    """Run the full pipeline (stages 1-5 + state) for a single role.

    Args:
        config: Role config dict from load_role_config().
        resume: If True, skip stages whose output already exists.

    Returns:
        Tuple of (synthesis_path, report_path).
    """
    data_dir = config["data_dir"]
    stage_outputs = _stage_outputs(data_dir)

    role = config.get("role", {}).get("title", "Unknown Role")
    today = config["today"]
    print(f"\nRole: {role}")
    print(f"Date: {today}")
    if resume:
        print("Mode: --resume (skipping completed stages)")
    print()

    # Ensure data directories exist
    (data_dir / "results").mkdir(parents=True, exist_ok=True)
    (data_dir / "reports").mkdir(parents=True, exist_ok=True)
    config["shared_pdf_dir"] = Path("data/pdfs")
    config["shared_pdf_dir"].mkdir(parents=True, exist_ok=True)

    pipeline_start = time.time()
    stage_stats = {}

    # Per-run usage tracker; injected into config so stages record API calls.
    tracker = UsageTracker()
    config["_usage_tracker"] = tracker

    # --- Load state --------------------------------------------------------
    state = load_state(data_dir)
    print(f"  {state_summary(state)}\n")

    # --- Stage 1: Fetch ---------------------------------------------------
    print("=" * 50)
    print(f"STAGE 1: Fetch candidates (source: {config.get('source', 'ashby')})")
    print("=" * 50)

    if _should_skip(1, resume, stage_outputs):
        candidates_path = stage_outputs[1]
        all_candidates = _validate_json_output(candidates_path, "Stage 1")
        stage_stats["fetched"] = len(all_candidates)
        print(f"  Skipped (output exists): {len(all_candidates)} candidates\n")
    else:
        try:
            t0 = time.time()
            candidates_path = resolve_source(config)(config)
            elapsed = time.time() - t0
            all_candidates = _validate_json_output(candidates_path, "Stage 1")
            stage_stats["fetched"] = len(all_candidates)
            print(f"  -> {len(all_candidates)} candidates fetched ({_fmt_duration(elapsed)})\n")
        except Exception:
            traceback.print_exc()
            print("\nPipeline aborted at Stage 1 (Fetch)")
            sys.exit(1)

    # --- State: filter new vs cached candidates ----------------------------
    new_candidates = filter_new_candidates(all_candidates, state)
    cached_evals = get_cached_evals(state)
    stage_stats["new_candidates"] = len(new_candidates)
    stage_stats["cached_candidates"] = len(cached_evals)

    if not new_candidates and not cached_evals:
        print("  No new candidates and no cached evaluations. Nothing to do.")
        print("  Pipeline complete (no output).")
        # Return empty paths -- caller should handle gracefully
        return (stage_outputs[4], data_dir / "reports" / f"report_{today}.html")

    # Write new-only candidates to a temp file for Stages 2-3
    new_candidates_path = data_dir / "results" / "stage1_new_candidates.json"
    if new_candidates:
        save_json(new_candidates, new_candidates_path)
    else:
        print("  No new candidates to triage -- using cached results only.\n")

    # --- Stage 2: Triage (new candidates only) ----------------------------
    print("=" * 50)
    print("STAGE 2: Sonnet triage (new candidates only)")
    print("=" * 50)

    if not new_candidates:
        survivors_path = stage_outputs[2]  # will use cached evals directly
        stage_stats["triaged"] = 0
        stage_stats["triage_passed"] = 0
        print(f"  Skipped (no new candidates)\n")
    elif _should_skip(2, resume, stage_outputs):
        survivors_path = stage_outputs[2]
        data = _validate_json_output(survivors_path, "Stage 2")
        items = data if isinstance(data, list) else []
        passed = sum(1 for c in items if c.get("passed", False))
        stage_stats["triaged"] = len(items)
        stage_stats["triage_passed"] = passed
        print(f"  Skipped (output exists): {passed} passed / {len(items)} screened\n")
    else:
        try:
            t0 = time.time()
            survivors_path = run_triage(config, new_candidates_path)
            elapsed = time.time() - t0
            data = _validate_json_output(survivors_path, "Stage 2")
            items = data if isinstance(data, list) else []
            passed = sum(1 for c in items if c.get("passed", False))
            stage_stats["triaged"] = len(items)
            stage_stats["triage_passed"] = passed
            print(f"  -> {passed} passed / {len(items)} screened ({_fmt_duration(elapsed)})\n")
        except Exception:
            traceback.print_exc()
            print("\nPipeline aborted at Stage 2 (Triage)")
            sys.exit(1)

    # --- Stage 3: Deep Evaluation (new survivors only) --------------------
    print("=" * 50)
    print("STAGE 3: Opus deep evaluation (new survivors only)")
    print("=" * 50)

    new_evals = []
    if not new_candidates or stage_stats.get("triage_passed", 0) == 0:
        evals_path = stage_outputs[3]
        stage_stats["deep_evaluated_new"] = 0
        print(f"  Skipped (no new survivors)\n")
    elif _should_skip(3, resume, stage_outputs):
        evals_path = stage_outputs[3]
        data = _validate_json_output(evals_path, "Stage 3")
        new_evals = data if isinstance(data, list) else []
        stage_stats["deep_evaluated_new"] = len(new_evals)
        print(f"  Skipped (output exists): {len(new_evals)} candidates evaluated\n")
    else:
        try:
            t0 = time.time()
            evals_path = run_deep_eval(config, survivors_path)
            elapsed = time.time() - t0
            data = _validate_json_output(evals_path, "Stage 3")
            new_evals = data if isinstance(data, list) else []
            stage_stats["deep_evaluated_new"] = len(new_evals)
            print(f"  -> {len(new_evals)} candidates evaluated ({_fmt_duration(elapsed)})\n")
        except Exception:
            traceback.print_exc()
            print("\nPipeline aborted at Stage 3 (Deep Evaluation)")
            sys.exit(1)

    # --- Merge new evals with cached evals --------------------------------
    merged_evals = new_evals + cached_evals
    stage_stats["deep_evaluated_total"] = len(merged_evals)
    if cached_evals:
        print(f"  Merged: {len(new_evals)} new + {len(cached_evals)} cached = {len(merged_evals)} total for synthesis\n")

    # Write merged evals for Stage 4
    merged_evals_path = data_dir / "results" / "stage3_merged_evals.json"
    if merged_evals:
        save_json(merged_evals, merged_evals_path)
    else:
        print("  No evaluated candidates to synthesize. Pipeline complete (no output).")
        return (stage_outputs[4], data_dir / "reports" / f"report_{today}.html")

    # --- Stage 4: Synthesis (all evaluated candidates) --------------------
    print("=" * 50)
    print("STAGE 4: Opus synthesis (all evaluated candidates)")
    print("=" * 50)

    if _should_skip(4, resume, stage_outputs):
        synthesis_path = stage_outputs[4]
        data = _validate_json_output(synthesis_path, "Stage 4")
        candidates_list = data if isinstance(data, list) else data.get("candidates", [])
        stage_stats["qualified"] = len(candidates_list)
        tier_counts = Counter(c.get("final_tier", "Unknown") for c in candidates_list)
        stage_stats["tiers"] = dict(tier_counts)
        print(f"  Skipped (output exists): {len(candidates_list)} qualified\n")
    else:
        try:
            t0 = time.time()
            synthesis_path = run_synthesize(config, merged_evals_path)
            elapsed = time.time() - t0
            data = _validate_json_output(synthesis_path, "Stage 4")
            candidates_list = data if isinstance(data, list) else data.get("candidates", [])
            stage_stats["qualified"] = len(candidates_list)
            tier_counts = Counter(c.get("final_tier", "Unknown") for c in candidates_list)
            stage_stats["tiers"] = dict(tier_counts)
            print(f"  -> {len(candidates_list)} qualified ({_fmt_duration(elapsed)})\n")
        except Exception:
            traceback.print_exc()
            print("\nPipeline aborted at Stage 4 (Synthesis)")
            sys.exit(1)

    # --- Update and save state --------------------------------------------
    update_state(state, candidates_list, new_evals, today)
    save_state(state, data_dir)
    print(f"  {state_summary(state)}\n")

    # --- Stage 5: Report ---------------------------------------------------
    print("=" * 50)
    print("STAGE 5: HTML report generation")
    print("=" * 50)

    # Inject total_screened into config so the report template can use it
    config["total_screened"] = stage_stats.get("fetched", 0)

    try:
        t0 = time.time()
        report_path = run_report(config, synthesis_path)
        elapsed = time.time() - t0
        if not report_path.exists():
            raise FileNotFoundError(f"Report not generated: {report_path}")
        stage_stats["report_path"] = str(report_path)
        print(f"  -> {report_path} ({_fmt_duration(elapsed)})\n")
    except Exception:
        traceback.print_exc()
        print("\nPipeline aborted at Stage 5 (Report)")
        sys.exit(1)

    # --- Slack summary (post-pipeline) ------------------------------------
    try:
        slack_summary_path = generate_slack_summary(synthesis_path, report_path, config)
    except Exception:
        traceback.print_exc()
        print("  WARNING: Slack summary generation failed (non-fatal)\n")
        slack_summary_path = None

    # --- Summary -----------------------------------------------------------
    total_time = time.time() - pipeline_start

    print("=" * 60)
    print(f"Pipeline Summary -- {role}")
    print("=" * 60)
    print(f"  Candidates fetched:      {stage_stats.get('fetched', '?')}")
    print(f"    New this run:          {stage_stats.get('new_candidates', '?')}")
    print(f"    Cached from prior:     {stage_stats.get('cached_candidates', '?')}")
    triaged = stage_stats.get('triaged', 0)
    triage_passed = stage_stats.get('triage_passed', 0)
    if triaged > 0:
        pct = int(100 * triage_passed / triaged)
        print(f"  Passed triage (new):     {triage_passed} / {triaged}  ({pct}%)")
    print(f"  Deep evaluated (new):    {stage_stats.get('deep_evaluated_new', '?')}")
    print(f"  Total for synthesis:     {stage_stats.get('deep_evaluated_total', '?')}")
    print(f"  Qualified (in report):   {stage_stats.get('qualified', '?')}")

    tiers = stage_stats.get("tiers", {})
    for tier_name in sorted(tiers.keys()):
        print(f"    {tier_name}: {tiers[tier_name]}")

    print(f"\n  Report: {stage_stats.get('report_path', '?')}")
    print(f"  Total time: {_fmt_duration(total_time)}")

    # Cost summary (per-stage + total) and append to data/roles/<slug>/usage.json
    print()
    print(tracker.format_summary())
    try:
        usage_path = tracker.write(data_dir)
        print(f"  Usage log: {usage_path}")
    except OSError as e:
        print(f"  WARNING: could not write usage log ({e})")
    print()

    return (synthesis_path, report_path)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Ashby Candidate Ranking Pipeline")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip stages whose output files already exist",
    )
    parser.add_argument(
        "--role",
        type=str,
        help="Run for a specific role slug (e.g., 'bdr', 'ai-strategist')",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run for all configured roles",
    )
    parser.add_argument(
        "--source",
        type=str,
        choices=["ashby", "local"],
        help="Input source for Stage 1 (default: ashby)",
    )
    parser.add_argument(
        "--input-dir",
        type=str,
        help="For --source local: folder containing candidates.csv + résumés",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Zero-key demo: render a sample report from bundled synthetic data",
    )
    args = parser.parse_args()

    if args.demo:
        from stages.demo import run_demo
        slug = args.role or "staff-backend-engineer"
        report_path = run_demo(slug=slug)
        print(f"\nDemo report generated: {report_path}")
        print(f"Open in browser: file://{report_path.resolve()}")
        return

    if args.all:
        slugs = list_available_roles()
        if not slugs:
            print("Error: No role configs found in config/roles/")
            sys.exit(1)
        print(f"Running pipeline for {len(slugs)} roles: {', '.join(slugs)}")
    elif args.role:
        slugs = [args.role]
    else:
        slugs = ["staff-backend-engineer"]  # backward compat default

    results = []
    for slug in slugs:
        print(f"\n{'=' * 60}")
        print(f"ROLE: {slug}")
        print(f"{'=' * 60}")

        config = load_role_config(slug, source=args.source, input_dir=args.input_dir)
        if config.get("source") == "local" and not config.get("input_dir"):
            print("Error: --source local requires --input-dir")
            sys.exit(1)
        synth_path, report_path = run_role(config, resume=args.resume)
        results.append((config, synth_path))

    # After all roles: generate combined report if multiple roles
    if len(results) > 1:
        try:
            from stages.report import generate_combined_report
            combined_path = generate_combined_report(results)
            print(f"\nCombined report: {combined_path}")
        except ImportError:
            print("\n  NOTE: generate_combined_report not yet implemented -- skipping combined report")
        except Exception:
            traceback.print_exc()
            print("\n  WARNING: Combined report generation failed (non-fatal)")


if __name__ == "__main__":
    main()
