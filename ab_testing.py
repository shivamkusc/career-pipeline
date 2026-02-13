"""
ab_testing.py — A/B Testing Framework
Generate multiple strategic variants of cover letter + email + LinkedIn,
track outcomes, and analyze statistical significance.
"""

import json
import math
from typing import Optional, Dict, List, Any
from collections import defaultdict

from ai_engine import call_claude, parse_json_response


# ─────────────────────────────────────────────────────────
# Strategy definitions
# ─────────────────────────────────────────────────────────

STRATEGIES = {
    "technical_depth": {
        "name": "Technical Depth",
        "description": "Focus on technical skills, specific tools/frameworks, problem-solving approach",
        "best_for": "IC engineering roles, technical startups",
        "prompt_modifier": """Focus heavily on technical skills and tools. Lead with specific
technologies, architectures, and problem-solving approaches. Show depth of understanding.
Use specific technical vocabulary from the JD. Mention concrete implementations.""",
    },
    "business_impact": {
        "name": "Business Impact",
        "description": "Focus on metrics, revenue/cost impact, cross-functional leadership",
        "best_for": "Senior roles, PM positions, business-facing teams",
        "prompt_modifier": """Lead with business outcomes and measurable impact. Quantify everything:
revenue generated, costs saved, users impacted, efficiency improved. Show cross-functional
leadership and strategic thinking. Connect technical work to business value.""",
    },
    "culture_fit": {
        "name": "Culture Fit",
        "description": "Focus on company values alignment, team collaboration, growth mindset",
        "best_for": "Startups, mission-driven orgs, culture-focused companies",
        "prompt_modifier": """Emphasize alignment with company values and mission. Show team
collaboration, mentorship, and growth mindset. Reference specific company culture elements
from the JD or research. Demonstrate enthusiasm for the company's mission.""",
    },
    "narrative_arc": {
        "name": "Narrative Arc",
        "description": "Tell career story with clear progression and future vision",
        "best_for": "Career transitions, explaining gaps, passion-driven roles",
        "prompt_modifier": """Tell a compelling career story. Show clear progression and
motivation for each transition. Connect past experiences as building blocks toward
this specific role. Paint a vision for the future. Make it personal and memorable.""",
    },
    "quantitative_proof": {
        "name": "Quantitative Proof",
        "description": "Heavy emphasis on numbers, before/after metrics, data-driven decisions",
        "best_for": "Data science, analytics, operations roles",
        "prompt_modifier": """Lead with numbers and data. Every claim should have a metric.
Show before/after comparisons, percentage improvements, and scale (users, transactions,
data volume). Demonstrate analytical rigor and data-driven decision making.""",
    },
}


def _auto_select_strategies(role_title: str, hard_skills: List[str],
                            key_responsibilities: List[str]) -> List[str]:
    """Auto-select 3 most relevant strategies based on job analysis."""
    scores = defaultdict(int)
    title_lower = role_title.lower()
    skills_str = " ".join(hard_skills).lower()
    resp_str = " ".join(key_responsibilities).lower()

    # Technical depth: engineering, developer, architect
    if any(kw in title_lower for kw in ["engineer", "developer", "architect", "swe"]):
        scores["technical_depth"] += 3
    if any(kw in skills_str for kw in ["python", "java", "aws", "kubernetes", "react"]):
        scores["technical_depth"] += 2

    # Business impact: manager, senior, lead, director
    if any(kw in title_lower for kw in ["manager", "senior", "lead", "director", "vp"]):
        scores["business_impact"] += 3
    if any(kw in resp_str for kw in ["revenue", "strategy", "stakeholder", "roadmap"]):
        scores["business_impact"] += 2

    # Culture fit: startup indicators, mission-driven
    if any(kw in resp_str for kw in ["culture", "values", "mission", "team", "collaborate"]):
        scores["culture_fit"] += 3

    # Narrative arc: good for transitions, unique backgrounds
    scores["narrative_arc"] += 1  # Always a reasonable choice

    # Quantitative proof: data, analytics, operations
    if any(kw in title_lower for kw in ["data", "analyst", "analytics", "ml ", "machine learning"]):
        scores["quantitative_proof"] += 3
    if any(kw in resp_str for kw in ["metrics", "kpi", "analysis", "performance", "optimization"]):
        scores["quantitative_proof"] += 2

    # Sort by score, take top 3
    sorted_strategies = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [s[0] for s in sorted_strategies[:3]]


# ─────────────────────────────────────────────────────────
# Variant generation
# ─────────────────────────────────────────────────────────

def generate_variants(
    analysis_data: Dict,
    tailored_resume: str,
    num_variants: int = 3,
    user_style: Optional[str] = None,
    strategy_names: Optional[List[str]] = None,
) -> List[Dict]:
    """
    Generate multiple strategic variants of cover letter + email + LinkedIn.

    Args:
        analysis_data: Dict from JobAnalysis (company, role, skills, etc.)
        tailored_resume: The tailored LaTeX resume
        num_variants: Number of variants to generate (default 3)
        user_style: Style sample text (optional)
        strategy_names: Specific strategies to use (auto-selected if None)

    Returns:
        List of variant dicts with name, description, cover_letter, email, linkedin, etc.
    """
    company = analysis_data.get("company_name", "Unknown")
    role = analysis_data.get("role_title", "Unknown")
    hard_skills = analysis_data.get("hard_skills", [])
    key_resp = analysis_data.get("key_responsibilities", [])
    differentiators = analysis_data.get("my_differentiators", [])
    research = analysis_data.get("research_notes", "")

    if not strategy_names:
        strategy_names = _auto_select_strategies(role, hard_skills, key_resp)

    strategy_names = strategy_names[:num_variants]
    variants = []

    style_section = ""
    if user_style:
        style_section = f"\nMY WRITING STYLE (match voice, not content):\n{user_style[:1500]}\n"

    for strat_name in strategy_names:
        strat = STRATEGIES.get(strat_name, STRATEGIES["technical_depth"])

        prompt = f"""Generate application materials using the "{strat['name']}" strategy.

STRATEGY: {strat['description']}
{strat['prompt_modifier']}

COMPANY: {company}
ROLE: {role}
KEY SKILLS: {', '.join(hard_skills[:10])}
TOP RESPONSIBILITIES: {json.dumps(key_resp[:5])}
MY DIFFERENTIATORS: {json.dumps(differentiators[:5])}
RESEARCH NOTES: {research}
{style_section}
MY RESUME (factual reference only):
{tailored_resume[:3000]}

IMPORTANT: Each variant should use DIFFERENT achievement examples from the resume
and emphasize DIFFERENT skills. Maintain factual accuracy.

Rules:
- Allow 1-2 natural grammar imperfections
- No long dash characters
- Never invent facts

Generate:

===COVER_LETTER_START===
(180-260 words, using the {strat['name']} approach)
===COVER_LETTER_END===

===EMAIL_START===
(90-140 words cold outreach email with Subject: line)
===EMAIL_END===

===LINKEDIN_START===
(Under 300 characters LinkedIn connection message)
===LINKEDIN_END===

===DIFFERENCES_START===
(3-4 bullet points explaining what makes this variant unique, one per line)
===DIFFERENCES_END==="""

        raw = call_claude(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.6,
            max_tokens=3000,
        )

        variant = {
            "name": strat_name,
            "display_name": strat["name"],
            "description": strat["description"],
            "cover_letter": "",
            "cold_email": "",
            "linkedin_message": "",
            "key_differences": [],
            "strategy_prompt": strat["prompt_modifier"][:500],
        }

        for key, (start, end) in {
            "cover_letter": ("===COVER_LETTER_START===", "===COVER_LETTER_END==="),
            "cold_email": ("===EMAIL_START===", "===EMAIL_END==="),
            "linkedin_message": ("===LINKEDIN_START===", "===LINKEDIN_END==="),
            "differences_raw": ("===DIFFERENCES_START===", "===DIFFERENCES_END==="),
        }.items():
            if start in raw and end in raw:
                content = raw.split(start)[1].split(end)[0].strip()
                if key == "differences_raw":
                    variant["key_differences"] = [
                        line.strip().lstrip("- ") for line in content.split("\n") if line.strip()
                    ]
                else:
                    variant[key] = content

        variants.append(variant)

    return variants


# ─────────────────────────────────────────────────────────
# Outcome tracking
# ─────────────────────────────────────────────────────────

def track_variant_outcome(db, variant_id: int, outcome: str,
                          response_time_hours: Optional[int] = None):
    """Record which variant was used and its outcome."""
    from tracker import update_variant

    updates = {
        "outcome": outcome,
        "response_received": outcome != "no_response",
    }
    if response_time_hours is not None:
        updates["response_time_hours"] = response_time_hours

    return update_variant(db, variant_id, **updates)


# ─────────────────────────────────────────────────────────
# Statistical analysis
# ─────────────────────────────────────────────────────────

# Weighted scoring for outcomes
OUTCOME_WEIGHTS = {
    "no_response": 0,
    "rejection": 1,
    "screening": 3,
    "interview": 7,
    "offer": 10,
}


def analyze_variant_performance(db, min_sample_size: int = 20) -> Dict[str, Any]:
    """
    Calculate statistical significance of variant performance.

    Uses Chi-square test for response vs. no response.
    """
    from tracker import get_all_variants_with_outcomes

    all_variants = get_all_variants_with_outcomes(db)

    if not all_variants:
        return {
            "total_tested": 0,
            "sufficient_data": False,
            "results": [],
            "winner": None,
            "confidence": "low",
            "p_value": 1.0,
            "recommendation": "Not enough data yet. Keep testing!",
        }

    # Group by variant name
    by_name = defaultdict(list)
    for v in all_variants:
        by_name[v.variant_name].append(v)

    total_tested = len(all_variants)
    sufficient = total_tested >= min_sample_size

    results = []
    for name, variants in by_name.items():
        outcomes = defaultdict(int)
        response_times = []
        weighted_score_total = 0

        for v in variants:
            outcome = v.outcome or "no_response"
            outcomes[outcome] += 1
            weighted_score_total += OUTCOME_WEIGHTS.get(outcome, 0)
            if v.response_time_hours and v.response_time_hours > 0:
                response_times.append(v.response_time_hours)

        count = len(variants)
        responses = count - outcomes.get("no_response", 0)
        avg_response_time = (
            round(sum(response_times) / len(response_times), 1)
            if response_times else 0
        )
        weighted_score = round(weighted_score_total / max(count, 1), 2)

        results.append({
            "variant": name,
            "display_name": STRATEGIES.get(name, {}).get("name", name),
            "times_used": count,
            "response_rate": round(responses / max(count, 1), 3),
            "avg_response_time_hours": avg_response_time,
            "weighted_score": weighted_score,
            "outcomes": dict(outcomes),
        })

    # Sort by weighted score
    results.sort(key=lambda r: r["weighted_score"], reverse=True)
    winner = results[0]["variant"] if results else None

    # Simple Chi-square test for response vs no response
    p_value = _chi_square_test(results)
    if p_value < 0.05:
        confidence = "high"
    elif p_value < 0.10:
        confidence = "medium"
    else:
        confidence = "low"

    if winner and sufficient:
        winner_display = STRATEGIES.get(winner, {}).get("name", winner)
        rec = f"'{winner_display}' has the highest weighted score. "
        if confidence == "high":
            rec += "This result is statistically significant (p < 0.05). Recommend using this strategy."
        elif confidence == "medium":
            rec += "Trending towards significance. More data would strengthen the conclusion."
        else:
            rec += "Not yet statistically significant. Keep testing with more applications."
    else:
        rec = f"Only {total_tested} variants tested. Need at least {min_sample_size} for meaningful analysis."

    return {
        "total_tested": total_tested,
        "sufficient_data": sufficient,
        "results": results,
        "winner": winner,
        "confidence": confidence,
        "p_value": round(p_value, 4),
        "recommendation": rec,
    }


def _chi_square_test(results: List[Dict]) -> float:
    """Simplified Chi-square test for response vs no response across variants."""
    if len(results) < 2:
        return 1.0

    # Build observed table: rows = variants, cols = [response, no_response]
    observed = []
    for r in results:
        responses = round(r["response_rate"] * r["times_used"])
        no_responses = r["times_used"] - responses
        observed.append([responses, no_responses])

    total = sum(r["times_used"] for r in results)
    total_responses = sum(row[0] for row in observed)
    total_no_responses = sum(row[1] for row in observed)

    if total == 0 or total_responses == 0 or total_no_responses == 0:
        return 1.0

    # Calculate chi-square statistic
    chi2 = 0.0
    for i, row in enumerate(observed):
        n_i = sum(row)
        if n_i == 0:
            continue
        expected_resp = n_i * total_responses / total
        expected_no = n_i * total_no_responses / total

        if expected_resp > 0:
            chi2 += (row[0] - expected_resp) ** 2 / expected_resp
        if expected_no > 0:
            chi2 += (row[1] - expected_no) ** 2 / expected_no

    # Degrees of freedom = (rows - 1) * (cols - 1)
    df = len(results) - 1
    if df <= 0:
        return 1.0

    # Approximate p-value using chi-square survival function
    return _chi2_survival(chi2, df)


def _chi2_survival(x: float, df: int) -> float:
    """Approximate chi-square survival function (1 - CDF)."""
    if x <= 0:
        return 1.0
    # Use Wilson-Hilferty approximation
    if df > 0:
        z = ((x / df) ** (1.0 / 3) - (1 - 2.0 / (9 * df))) / math.sqrt(2.0 / (9 * df))
        # Approximate normal CDF
        p = 0.5 * (1 + math.erf(z / math.sqrt(2)))
        return max(0, 1 - p)
    return 1.0


# ─────────────────────────────────────────────────────────
# Recommendation engine
# ─────────────────────────────────────────────────────────

def recommend_variant_for_job(db, analysis_data: Dict) -> Optional[str]:
    """
    Recommend which variant to use for a new application based on history.
    Returns strategy name or None if insufficient data.
    """
    perf = analyze_variant_performance(db)

    if not perf["sufficient_data"] or perf["confidence"] == "low":
        return None

    return perf["winner"]
