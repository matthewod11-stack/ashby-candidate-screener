"""Stage 5: HTML report generation — self-contained dashboard via Jinja2.

Reads synthesis output and generates a single HTML file with ranking table,
candidate detail cards, skills heatmap, and interview questions.
"""

from __future__ import annotations

import json
import random
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from models import SynthesizedCandidate, load_json
from stages._shared import shortlist_rows


def _safe_url(url: str | None) -> str:
    """Allow only https:// URLs through to templates.

    Why: candidate-controlled fields are rendered as href attributes; an
    attacker resume can supply `javascript:...` and HTML autoescape alone
    will not block it.
    """
    if not url or not isinstance(url, str):
        return ""
    return url if url.startswith("https://") else ""


# Dimension list used only by the mock data generator below; production
# reports MUST get scoring_dimensions from the role config.
_MOCK_SCORING_DIMENSIONS = [
    "python_fastapi",
    "sso_saml_oidc",
    "distributed_systems",
    "postgres_sql",
    "aws_cloud",
    "docker_k8s",
    "stripe_billing",
    "technical_leadership",
    "startup_fit",
    "authenticity_signal",
]


def run_report(config: dict, synthesis_path: Path) -> Path:
    """Generate HTML report from synthesis results.

    Args:
        config: Parsed criteria.yaml + env vars.
        synthesis_path: Path to stage4_synthesis.json.

    Returns:
        Path to generated HTML report file.
    """
    print("Stage 5 (Report): generating HTML dashboard ...")

    # 1. Load synthesis results
    raw = load_json(synthesis_path)
    candidates_raw = raw if isinstance(raw, list) else raw.get("candidates", [])

    # Parse into dataclass objects, then convert back to dicts for Jinja2
    candidates = [SynthesizedCandidate.from_dict(c) for c in candidates_raw]
    candidates_dicts = [_candidate_to_template_dict(c) for c in candidates]

    # Sort by final_rank (should already be sorted, but enforce)
    candidates_dicts.sort(key=lambda c: c["final_rank"])

    # Filter by report config
    report_cfg = config.get("report", {})
    exclude_tiers = report_cfg.get("exclude_tiers", [])
    min_score = report_cfg.get("min_score", 0)
    if exclude_tiers or min_score > 0:
        before = len(candidates_dicts)
        candidates_dicts = [
            c for c in candidates_dicts
            if c.get("final_tier", "") not in exclude_tiers
            and c.get("final_score", 0) >= min_score
        ]
        filtered = before - len(candidates_dicts)
        if filtered:
            print(f"  Filtered {filtered} candidates (min_score={min_score}, exclude_tiers={exclude_tiers})")

    # 2. Load Jinja2 template
    project_root = Path(__file__).resolve().parent.parent
    template_dir = project_root / "templates"
    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template("report.html")

    # 3. Build template context
    today = config.get("today", date.today().isoformat())
    role_title = config.get("role", {}).get("title", "Unknown Role")
    cutoff = config.get("role", {}).get("application_date_cutoff", "")
    date_range = f"{cutoff} - {today}" if cutoff else today

    new_count = sum(1 for c in candidates_dicts if c["is_new"])
    returning_count = len(candidates_dicts) - new_count

    raw_scoring_dims = config.get("scoring_dimensions")
    if not raw_scoring_dims:
        raise ValueError(
            f"Role {role_title!r} is missing `scoring_dimensions` in config. "
            "The report cannot render the skills heatmap without it."
        )

    context = {
        "role_title": role_title,
        "generated_date": today,
        "date_range": date_range,
        "total_screened": config.get("total_screened", len(candidates_dicts) * 20),
        "total_qualified": len(candidates_dicts),
        "new_count": new_count,
        "returning_count": returning_count,
        "candidates": candidates_dicts,
        "scoring_dimensions": [
            d["name"] if isinstance(d, dict) else d for d in raw_scoring_dims
        ],
        "profile_label": config.get("ats", {}).get("profile_label", "View Profile"),
        "company_name": config.get("company", {}).get("name", "Hiring"),
    }

    # 4. Render
    html = template.render(**context)

    # 5. Write output
    data_dir = config.get("data_dir", project_root / "data")
    output_dir = Path(data_dir) / "reports"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"report_{today}.html"
    output_path.write_text(html, encoding="utf-8")

    # 6. Report
    print(f"Report generated: {output_path} ({len(candidates_dicts)} candidates)")
    return output_path


def generate_combined_report(results: list[tuple[dict, Path]]) -> Path:
    """Generate a single HTML report with tabs for each role.

    Args:
        results: List of (role_config, synthesis_path) tuples.

    Returns:
        Path to combined HTML report.
    """
    roles_data = []
    for config, synth_path in results:
        if not synth_path.exists():
            continue
        raw = load_json(synth_path)
        candidates_raw = raw if isinstance(raw, list) else raw.get("candidates", [])
        candidates = [SynthesizedCandidate.from_dict(c) for c in candidates_raw]
        candidates_dicts = [_candidate_to_template_dict(c) for c in candidates]
        candidates_dicts.sort(key=lambda c: c["final_rank"])

        # Filter by report config for this role
        report_cfg = config.get("report", {})
        exclude_tiers = report_cfg.get("exclude_tiers", [])
        min_score = report_cfg.get("min_score", 0)
        if exclude_tiers or min_score > 0:
            before = len(candidates_dicts)
            candidates_dicts = [
                c for c in candidates_dicts
                if c.get("final_tier", "") not in exclude_tiers
                and c.get("final_score", 0) >= min_score
            ]
            filtered = before - len(candidates_dicts)
            if filtered:
                role_name = config.get("role", {}).get("title", "Unknown")
                print(f"  [{role_name}] Filtered {filtered} candidates")

        role_title = config.get("role", {}).get("title", "Unknown")
        slug = config.get("role_slug", config.get("role", {}).get("slug", "unknown"))
        raw_dims = config.get("scoring_dimensions", [])
        dims = [d["name"] if isinstance(d, dict) else d for d in raw_dims]

        roles_data.append({
            "title": role_title,
            "slug": slug,
            "candidates": candidates_dicts,
            "scoring_dimensions": dims,
            "total_screened": config.get("total_screened", 0),
            "total_qualified": len(candidates_dicts),
            "new_count": sum(1 for c in candidates_dicts if c.get("is_new")),
            "returning_count": sum(1 for c in candidates_dicts if not c.get("is_new")),
        })

    # Consolidated cross-role priority list ("who to meet this week")
    priority_shortlist: list[dict] = []
    for cfg, sp in results:
        if Path(sp).exists():
            rtitle = cfg.get("role", {}).get("title", "Unknown")
            priority_shortlist.extend(shortlist_rows(load_json(sp), rtitle))
    priority_shortlist.sort(key=lambda r: r["score"], reverse=True)

    today = date.today().isoformat()

    project_root = Path(__file__).resolve().parent.parent
    template_dir = project_root / "templates"
    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template("report.html")

    # Pull engagement-level chrome (company name, profile label) from the first
    # role config; multi-role runs share one engagement.
    first_config = results[0][0] if results else {}
    company_name = first_config.get("company", {}).get("name", "Hiring")
    profile_label = first_config.get("ats", {}).get("profile_label", "View Profile")

    html = template.render(
        roles=roles_data,
        generated_date=today,
        multi_role=True,
        company_name=company_name,
        profile_label=profile_label,
        priority_shortlist=priority_shortlist,
    )

    output_dir = project_root / "data" / "reports"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"report_{today}.html"
    output_path.write_text(html, encoding="utf-8")
    print(f"Combined report: {output_path} ({len(roles_data)} roles)")
    return output_path


def _candidate_to_template_dict(sc: SynthesizedCandidate) -> dict:
    """Convert a SynthesizedCandidate to a nested dict suitable for Jinja2.

    Uses dataclass asdict but returns the result directly — Jinja2 can
    traverse nested dicts with dot notation via its attribute lookup.
    """
    d = asdict(sc)

    # Wrap the nested dicts so Jinja2 dot access works (e.g., c.evaluated.candidate.name)
    d["evaluated"] = _DotDict(d["evaluated"])
    d["evaluated"]["candidate"] = _DotDict(d["evaluated"]["candidate"])
    d["evaluated"]["skills_matrix"] = d["evaluated"].get("skills_matrix", {})

    cand = d["evaluated"]["candidate"]
    cand["profile_url"] = _safe_url(cand.get("profile_url"))

    return _DotDict(d)


class _DotDict(dict):
    """Dict subclass that supports attribute-style access for Jinja2 templates."""

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(f"No attribute '{key}'")

    def __setattr__(self, key, value):
        self[key] = value


# ── Mock data generator for testing ────────────────────────────────────


def generate_mock_synthesis(output_path: Path | None = None) -> Path:
    """Generate a stage4_synthesis.json with 15 fake candidates for testing.

    Distribution: 4 Tier 1, 4 Tier 2, 4 Tier 3, 3 Tier 4.

    Args:
        output_path: Where to write. Defaults to data/results/stage4_synthesis.json.

    Returns:
        Path to the written file.
    """
    if output_path is None:
        output_path = Path(__file__).resolve().parent.parent / "data" / "results" / "stage4_synthesis.json"

    random.seed(42)  # deterministic for reproducibility

    tier_config = [
        ("Tier 1 \u2013 Top", 4, (88, 98), "Strong Hire"),
        ("Tier 2 \u2013 Strong", 4, (75, 87), "Hire"),
        ("Tier 3 \u2013 Moderate", 4, (60, 74), "Lean Hire"),
        ("Tier 4 \u2013 Weak", 3, (40, 59), "Maybe"),
    ]

    names = [
        # Tier 1
        ("Jiwei Guo", "Software Engineer, Meta (3.5 yr)", "Sr SWE, LinkedIn (4 yr)", "MIT, CS", "11+"),
        ("Priya Nair", "Staff Engineer, Stripe (2 yr)", "Sr SWE, Airbnb (5 yr)", "Stanford, CS", "12+"),
        ("Marcus Chen", "Tech Lead, Figma (3 yr)", "Sr SWE, Google (4 yr)", "CMU, CS", "10+"),
        ("Elena Vasquez", "Staff SWE, Datadog (2.5 yr)", "Sr Backend, Uber (3.5 yr)", "Berkeley, EECS", "9+"),
        # Tier 2
        ("David Kim", "Sr SWE, Notion (2 yr)", "SWE, Palantir (4 yr)", "Caltech, CS", "8+"),
        ("Aisha Rahman", "Backend Lead, Scale AI (1.5 yr)", "SWE, Microsoft (5 yr)", "Georgia Tech, CS", "9+"),
        ("James O'Brien", "Sr Engineer, Vercel (2 yr)", "SWE, Cloudflare (3 yr)", "UWaterloo, SE", "7+"),
        ("Yuki Tanaka", "Platform Engineer, Retool (2 yr)", "SWE, AWS (4 yr)", "U Tokyo, CS", "8+"),
        # Tier 3
        ("Sarah Mitchell", "SWE, Twilio (3 yr)", "Jr SWE, Oracle (2 yr)", "Michigan, CS", "6+"),
        ("Raj Patel", "Backend SWE, Toast (2 yr)", "SWE, Infosys (3 yr)", "IIT Bombay, CS", "7+"),
        ("Tomas Eriksson", "SWE, Klarna (2.5 yr)", "SWE, Spotify (2 yr)", "KTH, CS", "6+"),
        ("Lisa Wang", "SWE, MongoDB (1.5 yr)", "SWE, IBM (4 yr)", "Cornell, CS", "7+"),
        # Tier 4
        ("Alex Petrov", "Jr SWE, Startup (1 yr)", "Intern, Google (6 mo)", "State Univ, CS", "3+"),
        ("Nina Schmidt", "SWE, Consultancy (3 yr)", "Jr Dev, SAP (2 yr)", "TU Munich, IS", "5+"),
        ("Kevin Brown", "Backend Dev, Agency (2 yr)", "Support Eng, Zendesk (1 yr)", "Community College", "4+"),
    ]

    strengths_pool = [
        "Built SSO/SAML/OIDC for 50K+ users. Deep distributed systems experience.",
        "Architected multi-tenant billing with Stripe. Strong Postgres optimization.",
        "Led platform migration to Kubernetes. Excellent technical communication.",
        "Designed event-driven microservices. Published conference talks on FastAPI.",
        "Built real-time data pipelines at scale. Strong mentorship track record.",
        "Owned auth infrastructure end-to-end. Deep AWS networking knowledge.",
        "Led API design for public developer platform. Strong testing culture.",
        "Built CI/CD from scratch. Deep Postgres replication expertise.",
        "Solid Python fundamentals. Good Stripe integration experience.",
        "Reliable execution on well-defined tasks. Improving quickly.",
        "Good communication skills. Some leadership experience in small teams.",
        "Broad technology exposure. Willingness to learn new domains.",
        "Self-taught with strong hustle. Quick learner on new stacks.",
        "Good foundational CS knowledge. Collaborative work style.",
        "Decent breadth of experience. Shows initiative on side projects.",
    ]

    concerns_pool = [
        "Title at Meta was SWE not Staff — may need to validate scope.",
        "No direct startup experience. Compensation expectations may be high.",
        "Primary expertise in frontend, backend depth is more recent.",
        "Long tenure at one company — adaptability to fast-moving startup unknown.",
        "SSO experience limited to consuming, not building from scratch.",
        "No Stripe/billing experience. Would need ramp-up time.",
        "Limited distributed systems work. Mostly monolith background.",
        "Experience mostly at large companies with established infrastructure.",
        "Gaps in system design depth. May need significant mentorship.",
        "Experience level below target. Would need fast growth trajectory.",
        "Resume language closely mirrors job posting — authenticity concerns.",
        "Limited open-source contributions or public technical presence.",
        "Career trajectory unclear. Multiple short stints without progression.",
        "Technical depth appears shallow across multiple domains.",
        "No evidence of ownership or leadership beyond task execution.",
    ]

    culture_pool = [
        "AI-native company experience. Thrives in ambiguity.",
        "Strong startup DNA from Series A-C companies. Builder mindset.",
        "Open-source contributor. Active in developer community.",
        "Previous founder experience. Understands urgency.",
        "Primarily big-company background but shows entrepreneurial interests.",
        "Mix of startup and enterprise. Values clear communication.",
        "Enterprise-focused career. May need adjustment to startup pace.",
        "Academic-leaning. Strong technical but less product-oriented.",
    ]

    interview_questions_pool = [
        {"focus": "SSO depth", "question": "Walk through a specific SAML integration you built end-to-end. What were the hardest edge cases?"},
        {"focus": "System design", "question": "Design an event-driven notification system handling 100K events/sec with exactly-once delivery."},
        {"focus": "Technical leadership", "question": "Describe a time you had to make a controversial technical decision. How did you build consensus?"},
        {"focus": "Startup fit", "question": "Tell me about a time you shipped something with significant unknowns. How did you de-risk it?"},
        {"focus": "Billing systems", "question": "How would you design a usage-based billing system with Stripe that handles metering at scale?"},
        {"focus": "Database design", "question": "Walk me through how you would migrate a high-traffic Postgres table with zero downtime."},
        {"focus": "Distributed systems", "question": "Explain how you would implement distributed locking for a multi-region deployment."},
        {"focus": "FastAPI expertise", "question": "How do you handle dependency injection and middleware in large FastAPI applications?"},
    ]

    candidates = []
    rank = 1

    for tier_name, count, score_range, recommendation in tier_config:
        for i in range(count):
            idx = rank - 1
            name, current_role, prior_role, education, years_exp = names[idx]

            score = random.randint(score_range[0], score_range[1])

            # Generate skills matrix — tier 1/2 have higher averages
            tier_num = int(tier_name.split()[1])
            base_range = {1: (7, 10), 2: (6, 9), 3: (4, 7), 4: (2, 6)}[tier_num]
            skills = {}
            for dim in _MOCK_SCORING_DIMENSIONS:
                lo, hi = base_range
                val = random.randint(lo, hi)
                skills[dim] = min(10, max(1, val))

            is_new = random.random() < 0.35
            first_seen = (date.today() - timedelta(days=random.randint(0, 21))).isoformat()

            # Top-tier candidates get interview questions
            iq = None
            if tier_num <= 2:
                iq = random.sample(interview_questions_pool, k=random.randint(2, 4))

            flags = "None"
            if tier_num >= 3 and random.random() < 0.4:
                flags = random.choice([
                    "Resume language closely mirrors job posting",
                    "Timeline inconsistency between roles",
                    "Claims don't match company size at stated dates",
                ])

            candidate_data = {
                "evaluated": {
                    "candidate": {
                        "candidate_id": f"cand_{rank:03d}",
                        "name": name,
                        "email": f"{name.lower().replace(' ', '.')}@email.com",
                        "profile_url": f"https://app.ashbyhq.com/candidates/{rank:03d}abc",
                        "application_date": (date.today() - timedelta(days=random.randint(5, 25))).isoformat(),
                        "source": "Applied",
                        "status": "Active",
                    },
                    "overall_score": score,
                    "tier": tier_name,
                    "recommendation": recommendation,
                    "current_role": current_role,
                    "prior_role": prior_role,
                    "education": education,
                    "years_experience": years_exp,
                    "key_strengths": strengths_pool[idx],
                    "key_concerns": concerns_pool[idx],
                    "culture_fit": culture_pool[idx % len(culture_pool)],
                    "flags": flags,
                    "skills_matrix": skills,
                },
                "final_rank": rank,
                "final_score": score,
                "final_tier": tier_name,
                "is_new": is_new,
                "first_seen_date": first_seen,
                "interview_questions": iq,
            }

            candidates.append(candidate_data)
            rank += 1

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(candidates, f, indent=2)

    print(f"Mock synthesis data written: {output_path} ({len(candidates)} candidates)")
    return output_path


# ── CLI entry point for standalone testing ─────────────────────────────

if __name__ == "__main__":
    import sys

    import yaml

    project_root = Path(__file__).resolve().parent.parent

    # Generate mock data
    mock_path = generate_mock_synthesis()

    # Load config
    config_path = project_root / "config" / "criteria.yaml"
    with open(config_path) as f:
        config = yaml.safe_load(f)
    config["today"] = date.today().isoformat()

    # Run report
    report_path = run_report(config, mock_path)
    print(f"\nOpen in browser: file://{report_path.resolve()}")
