"""
resume_engine.py — Refactored Resume Tailoring Engine
Architecture: 3-step chain (Analyze → Tailor → Write) + Validation
"""

import json
import time
import sys
from dataclasses import dataclass, field
from typing import Optional
from anthropic import Anthropic
from docx import Document

client = Anthropic()


# ─────────────────────────────────────────────────────────
# Data Models
# ─────────────────────────────────────────────────────────

@dataclass
class JobAnalysis:
    company_name: str
    role_title: str
    location: str
    hard_skills: list
    soft_skills: list
    key_responsibilities: list
    nice_to_haves: list
    my_differentiators: list
    keyword_matches: list
    keyword_gaps: list
    research_notes: str


@dataclass
class TailoredOutput:
    resume_latex: str
    cover_letter: str
    cold_email: str
    linkedin_message: str
    analysis: JobAnalysis
    validation_results: Optional[dict] = None
    ats_score: Optional[dict] = None


# ─────────────────────────────────────────────────────────
# Utilities
# ─────────────────────────────────────────────────────────

def read_docx(file_path: str) -> str:
    """Read a .docx file and return its text content."""
    doc = Document(file_path)
    return "\n".join([p.text for p in doc.paragraphs if p.text.strip()])


def strip_code_fences(text: str) -> str:
    """Remove markdown code fences from LLM output."""
    text = text.strip()
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()


def parse_delimited_sections(response_text: str) -> dict:
    """Parse delimited sections from LLM response."""
    sections = {}
    markers = {
        "cover_letter": ("===COVER_LETTER_START===", "===COVER_LETTER_END==="),
        "cold_email": ("===EMAIL_START===", "===EMAIL_END==="),
        "linkedin_msg": ("===LINKEDIN_START===", "===LINKEDIN_END==="),
    }
    for key, (start, end) in markers.items():
        if start in response_text and end in response_text:
            content = response_text.split(start)[1].split(end)[0].strip()
            sections[key] = content
        else:
            sections[key] = None
    return sections


def call_claude(
    messages: list,
    system: str = None,
    temperature: float = 0.3,
    max_tokens: int = 8000,
    cache_system: bool = False,
    max_retries: int = 3,
) -> str:
    """Call Anthropic API with retry logic and optional prompt caching."""
    kwargs = {
        "model": "claude-sonnet-4-5-20250929",
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": messages,
    }
    if system:
        if cache_system:
            kwargs["system"] = [
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        else:
            kwargs["system"] = system

    for attempt in range(max_retries):
        try:
            response = client.messages.create(**kwargs)
            return response.content[0].text
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            wait = 2 ** attempt
            print(f"  ⚠️  Retry {attempt + 1}/{max_retries}: {e}. Waiting {wait}s...")
            time.sleep(wait)


def parse_json_response(raw: str) -> dict:
    """Safely parse JSON from an LLM response."""
    raw = strip_code_fences(raw)
    return json.loads(raw)


# ─────────────────────────────────────────────────────────
# Step 1: Analyze JD + Resume
# ─────────────────────────────────────────────────────────

def analyze_job_and_resume(jd: str, resume: str) -> JobAnalysis:
    """Extract structured analysis comparing JD against resume."""
    print("📊 Step 1/3: Analyzing job description and resume fit...")

    prompt = f"""Analyze this job description against my resume. Be honest about
gaps and specific about differentiators.

JOB DESCRIPTION:
{jd}

MY RESUME:
{resume}

Return ONLY valid JSON (no markdown, no commentary) with this structure:
{{
    "company_name": "Exact company name",
    "role_title": "Exact role title",
    "location": "City, State or Remote",
    "hard_skills": ["technical skills required"],
    "soft_skills": ["soft skills mentioned"],
    "key_responsibilities": ["top 5 responsibilities"],
    "nice_to_haves": ["preferred but not required skills"],
    "my_differentiators": ["What makes me unique for THIS role, be specific"],
    "keyword_matches": ["JD keywords I already demonstrate in my resume"],
    "keyword_gaps": ["JD keywords I genuinely do not demonstrate"],
    "research_notes": "2-3 specific, factual things about this company worth mentioning"
}}

Rules:
- keyword_matches: only list skills clearly evidenced in my resume
- keyword_gaps: be honest, do not list skills I actually have
- my_differentiators: focus on combinations of experience that are rare
- research_notes: only include verifiable facts from the JD itself"""

    raw = call_claude(
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,
        max_tokens=3000,
    )
    data = parse_json_response(raw)
    return JobAnalysis(**data)


# ─────────────────────────────────────────────────────────
# Step 2: Tailor Resume
# ─────────────────────────────────────────────────────────

RESUME_RULES = """EDITING RULES (STRICT, DO NOT BREAK):
1. Do NOT add new bullet points, sections, or lines that don't exist in the original.
2. Do NOT invent metrics, tools, employers, titles, dates, or achievements.
   If a keyword would require lying, skip it.
3. You MAY reword existing bullets to incorporate JD keywords naturally.
   Prefer swapping synonyms over adding new phrases.
4. You MAY reorder bullets within a role so the most relevant appear first.
5. You MAY reorder and swap items in the Skills section to prioritize JD skills.
6. Keep the same LaTeX structure, macros, spacing, section order, and formatting.
7. The summary should be 2 lines max, specific to this role, and human-sounding.
8. Never use long dash characters. Use commas, parentheses, or periods.
9. Each bullet's original intent, scope, and metrics must be preserved.
10. Keep it ATS-friendly, concise, and natural-sounding (not keyword-stuffed).

LATEX RULES (CRITICAL — broken LaTeX = no PDF):
- ALWAYS escape special LaTeX characters: % as \\%, & as \\&, # as \\#
- NEVER use unescaped % in text (e.g. write 95\\% not 95%)
- Ensure every opening brace has a matching closing brace
- Do NOT add or remove \\resumeSubHeadingListStart / \\resumeSubHeadingListEnd pairs"""


def tailor_resume(resume: str, analysis: JobAnalysis) -> str:
    """Reword and reorder existing resume content to match JD."""
    print("✏️  Step 2/3: Tailoring resume...")

    prompt = f"""You are editing my LaTeX resume to better target a specific role.

BEFORE YOU EDIT, write a brief plan:
- Which 3 bullets are MOST relevant and should stay prominent?
- Which keywords from the JD can I naturally swap into existing bullets?
- What should the summary emphasize for this role?

Then output the full edited LaTeX.

TARGET ROLE ANALYSIS:
- Company: {analysis.company_name}
- Role: {analysis.role_title}
- Required skills: {', '.join(analysis.hard_skills)}
- Soft skills: {', '.join(analysis.soft_skills)}
- Key responsibilities: {json.dumps(analysis.key_responsibilities)}
- My differentiators: {json.dumps(analysis.my_differentiators)}
- Keywords I match: {', '.join(analysis.keyword_matches)}
- Keywords I'm missing: {', '.join(analysis.keyword_gaps)}

MY CURRENT RESUME:
{resume}

{RESUME_RULES}

Output your brief plan first, then the full LaTeX document after a line reading:
===LATEX_START===
[full document here]
===LATEX_END==="""

    raw = call_claude(
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=8000,
    )

    if "===LATEX_START===" in raw and "===LATEX_END===" in raw:
        return raw.split("===LATEX_START===")[1].split("===LATEX_END===")[0].strip()

    if "\\documentclass" in raw:
        return raw[raw.index("\\documentclass"):]

    return raw


# ─────────────────────────────────────────────────────────
# Step 3: Write Cover Letter + Outreach
# ─────────────────────────────────────────────────────────

def write_narratives(
    tailored_resume: str,
    analysis: JobAnalysis,
    style_voice: str,
) -> dict:
    """Generate cover letter, cold email, and LinkedIn message."""
    print("📝 Step 3/3: Writing cover letter and outreach...")

    system_prompt = f"""You are my career writing assistant. Your job is to write
in my voice. Study the style sample below.

MY WRITING STYLE SAMPLE:
{style_voice}

When writing in my voice, absorb these patterns:
- My sentence length and rhythm
- My vocabulary (casual, formal, technical?)
- How I open paragraphs and transition between ideas
- My level of directness
- Any phrases or patterns I tend to repeat

Do NOT copy content from the sample. Match the VOICE only.

Additional rules:
- Allow 1-2 tiny natural grammar imperfections in cover letter and email
- Never use long dash characters (use commas or parentheses)
- Never invent facts, metrics, or experiences about me"""

    prompt = f"""Write application materials for this role.

COMPANY: {analysis.company_name}
ROLE: {analysis.role_title}
LOCATION: {analysis.location}
THEIR TOP PRIORITIES: {json.dumps(analysis.key_responsibilities[:5])}
MY DIFFERENTIATORS: {json.dumps(analysis.my_differentiators)}
RESEARCH TALKING POINTS: {analysis.research_notes}

MY RESUME (for factual reference only):
{tailored_resume}

Generate three outputs between delimiters:

===COVER_LETTER_START===
(180-260 words)

OPENING STRATEGY: Lead with a specific observation about the company or role
that connects to my experience. The reader should think "this person did homework."

Strong opener examples (adapt, don't copy):
- "Your team's work on [X] caught my attention because I solved a similar problem at..."
- "[Company]'s approach to [challenge] reminded me of the architecture decisions we faced..."

BODY: Tell ONE specific story from my experience that directly maps to their needs.
Mention what I'd focus on in my first 3-6 months.

CLOSE: A clear, confident next step. Not "looking forward to hearing from you."
===COVER_LETTER_END===

===EMAIL_START===
(90-140 words)
Include a Subject: line that's specific (not just "Application for X").
Open with why I'm reaching out + one specific company reference.
One credential sentence. One clear ask. No filler.
===EMAIL_END===

===LINKEDIN_START===
(Under 300 characters)
Mention role, one credential, clear ask.
===LINKEDIN_END==="""

    raw = call_claude(
        messages=[{"role": "user", "content": prompt}],
        system=system_prompt,
        temperature=0.6,
        max_tokens=3000,
        cache_system=True,
    )

    return parse_delimited_sections(raw)


# ─────────────────────────────────────────────────────────
# Validation & Scoring
# ─────────────────────────────────────────────────────────

def validate_no_hallucination(original: str, tailored: str) -> dict:
    """Check tailored resume for invented content."""
    print("🔍 Validating: checking for invented content...")

    prompt = f"""Compare these two resumes bullet by bullet. For each bullet in
TAILORED that differs from ORIGINAL, classify it:

- SAFE: rewording of existing content with no new factual claims
- WARNING: adds framing or emphasis not in original (plausible but unverified)
- DANGER: invents metrics, tools, projects, employers, or achievements

ORIGINAL RESUME:
{original}

TAILORED RESUME:
{tailored}

Return ONLY valid JSON:
{{
    "flags": [
        {{"bullet": "first 50 chars of bullet...", "status": "SAFE", "reason": "..."}},
        ...
    ],
    "danger_count": 0,
    "warning_count": 0,
    "overall": "PASS or REVIEW or FAIL"
}}

Be strict. If in doubt, mark WARNING."""

    raw = call_claude(
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
        max_tokens=4000,
    )
    return parse_json_response(raw)


def compute_ats_score(jd: str, tailored_resume: str) -> dict:
    """Compute keyword coverage score against JD."""
    print("📊 Computing ATS keyword coverage...")

    prompt = f"""Extract the 20 most important keywords and phrases from this job
description (technical skills, tools, methodologies, soft skills).

Then check which ones appear (exact match or close synonym) in the resume.

JOB DESCRIPTION:
{jd}

RESUME:
{tailored_resume}

Return ONLY valid JSON:
{{
    "total_keywords": ["kw1", "kw2", ...],
    "matched": ["kw1", "kw3", ...],
    "missing": ["kw2", ...],
    "score": 0.75
}}"""

    raw = call_claude(
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
        max_tokens=2000,
    )
    return parse_json_response(raw)


# ─────────────────────────────────────────────────────────
# Main Pipeline
# ─────────────────────────────────────────────────────────

def run_pipeline(
    jd: str,
    resume_latex: str,
    style_sample_path: Optional[str] = None,
) -> TailoredOutput:
    """Full pipeline: Analyze → Tailor → Write → Validate → Score."""

    style_voice = ""
    if style_sample_path:
        print("📖 Reading style sample...")
        style_voice = read_docx(style_sample_path)

    # Step 1: Analyze
    analysis = analyze_job_and_resume(jd, resume_latex)
    print(f"  ✅ Company: {analysis.company_name}")
    print(f"  ✅ Role: {analysis.role_title}")
    print(f"  ✅ Keyword matches: {len(analysis.keyword_matches)}")
    print(f"  ⚠️  Keyword gaps: {', '.join(analysis.keyword_gaps[:5])}")

    # Step 2: Tailor resume
    tailored_latex = tailor_resume(resume_latex, analysis)

    # Step 3: Write narratives
    narratives = write_narratives(tailored_latex, analysis, style_voice)

    # Step 4: Validate (hallucination check)
    validation = validate_no_hallucination(resume_latex, tailored_latex)
    danger_count = validation.get("danger_count", 0)
    if danger_count > 0:
        print(f"  🚨 {danger_count} DANGER flags: potentially invented claims!")
        for flag in validation.get("flags", []):
            if flag["status"] == "DANGER":
                print(f"     → {flag['bullet'][:60]}... ({flag['reason']})")
    else:
        print("  ✅ Validation passed: no invented content detected")

    # Step 5: ATS score
    ats = compute_ats_score(jd, tailored_latex)
    score = ats.get("score", 0)
    print(f"  📊 ATS keyword coverage: {score:.0%} ({len(ats.get('matched', []))}/{len(ats.get('total_keywords', []))})")
    if ats.get("missing"):
        print(f"  📊 Missing: {', '.join(ats['missing'][:5])}")

    return TailoredOutput(
        resume_latex=tailored_latex,
        cover_letter=narratives.get("cover_letter", ""),
        cold_email=narratives.get("cold_email", ""),
        linkedin_message=narratives.get("linkedin_msg", ""),
        analysis=analysis,
        validation_results=validation,
        ats_score=ats,
    )
