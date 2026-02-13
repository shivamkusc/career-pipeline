"""
network_manager.py — Network relationship management
LinkedIn CSV import, relationship tracking, outreach suggestions, and coffee chat generation.
"""

import csv
import io
import urllib.parse
from datetime import date, timedelta
from typing import Optional, Dict, List, Any
from collections import defaultdict

from ai_engine import call_claude


# ─────────────────────────────────────────────────────────
# LinkedIn CSV import
# ─────────────────────────────────────────────────────────

def import_linkedin_csv(csv_content: str, db) -> Dict[str, Any]:
    """
    Import LinkedIn connections from CSV export.

    LinkedIn CSV format:
    First Name,Last Name,Email Address,Company,Position,Connected On

    Returns:
        {imported: int, updated: int, skipped: int, errors: list}
    """
    from tracker import get_contact_by_email, create_contact, update_contact

    result = {"imported": 0, "updated": 0, "skipped": 0, "errors": []}

    try:
        reader = csv.DictReader(io.StringIO(csv_content))
    except Exception as e:
        result["errors"].append(f"Failed to parse CSV: {e}")
        return result

    # Validate required columns
    required = {"First Name", "Last Name"}
    if reader.fieldnames and not required.issubset(set(reader.fieldnames)):
        result["errors"].append(f"Missing columns. Expected at least: {required}. Got: {reader.fieldnames}")
        return result

    for row_num, row in enumerate(reader, start=2):
        try:
            first = (row.get("First Name") or "").strip()
            last = (row.get("Last Name") or "").strip()
            if not first and not last:
                result["skipped"] += 1
                continue

            name = f"{first} {last}".strip()
            email = (row.get("Email Address") or "").strip().lower() or None
            company = (row.get("Company") or "").strip() or None
            title = (row.get("Position") or "").strip() or None
            connected_on = (row.get("Connected On") or "").strip()

            # Parse connected date
            last_contacted = None
            if connected_on:
                try:
                    # LinkedIn format: "01 Jan 2024" or "2024-01-15"
                    for fmt in ("%d %b %Y", "%Y-%m-%d", "%m/%d/%Y", "%b %d, %Y"):
                        try:
                            last_contacted = date.fromisoformat(connected_on) if "-" in connected_on else None
                            if not last_contacted:
                                from datetime import datetime as dt
                                last_contacted = dt.strptime(connected_on, fmt).date()
                            break
                        except (ValueError, TypeError):
                            continue
                except Exception:
                    pass

            # Auto-tag based on title
            tags = _auto_tag(title)

            # Check for existing contact
            existing = get_contact_by_email(db, email) if email else None

            if existing:
                # Update with newer info
                updates = {}
                if company and not existing.company:
                    updates["company"] = company
                if title and not existing.title:
                    updates["title"] = title
                if last_contacted and (not existing.last_contacted or last_contacted > existing.last_contacted):
                    updates["last_contacted"] = last_contacted
                if tags:
                    existing_tags = set((existing.tags or "").split(","))
                    new_tags = existing_tags | set(tags.split(","))
                    merged = ",".join(t for t in new_tags if t)
                    if merged != existing.tags:
                        updates["tags"] = merged

                if updates:
                    update_contact(db, existing.id, **updates)
                    result["updated"] += 1
                else:
                    result["skipped"] += 1
            else:
                create_contact(
                    db,
                    name=name,
                    email=email,
                    company=company,
                    title=title,
                    linkedin_url=None,
                    relationship_strength="warm",
                    source="linkedin_import",
                    last_contacted=last_contacted,
                    tags=tags,
                )
                result["imported"] += 1

        except Exception as e:
            result["errors"].append(f"Row {row_num}: {e}")

    return result


def _auto_tag(title: Optional[str]) -> str:
    """Auto-generate tags based on job title keywords."""
    if not title:
        return ""
    title_lower = title.lower()
    tags = []

    tag_keywords = {
        "recruiter": ["recruiter", "talent acquisition", "hiring"],
        "engineer": ["engineer", "developer", "programmer", "swe"],
        "manager": ["manager", "director", "vp", "head of"],
        "designer": ["designer", "ux", "ui", "design"],
        "data": ["data scientist", "data analyst", "data engineer", "ml ", "machine learning"],
        "product": ["product manager", "product owner", "product lead"],
        "founder": ["founder", "ceo", "cto", "coo", "co-founder"],
    }

    for tag, keywords in tag_keywords.items():
        if any(kw in title_lower for kw in keywords):
            tags.append(tag)

    return ",".join(tags)


# ─────────────────────────────────────────────────────────
# Relationship strength calculation
# ─────────────────────────────────────────────────────────

def calculate_relationship_strength(
    last_contacted: Optional[date],
    interactions: List[Any],
    has_referral: bool = False,
) -> str:
    """
    Determine relationship strength based on interaction history.

    Rules:
    - 0 interactions in 12 months = cold
    - 1-2 interactions in 12 months = warm
    - 3+ interactions in 6 months = close
    - Referral given or received = upgrade to close
    """
    if has_referral:
        return "close"

    today = date.today()
    twelve_months_ago = today - timedelta(days=365)
    six_months_ago = today - timedelta(days=180)

    recent_interactions = 0
    very_recent = 0

    for interaction in interactions:
        i_date = getattr(interaction, "interaction_date", None)
        if not i_date:
            continue
        if i_date >= twelve_months_ago:
            recent_interactions += 1
        if i_date >= six_months_ago:
            very_recent += 1

    if very_recent >= 3:
        return "close"
    elif recent_interactions >= 1:
        return "warm"
    else:
        return "cold"


def decay_relationships(db, warm_threshold_days: int = 180, close_threshold_days: int = 120):
    """
    Downgrade relationship strength if no recent contact.
    Called periodically by scheduler.
    """
    from tracker import get_all_contacts, update_contact

    today = date.today()
    contacts = get_all_contacts(db)

    decayed = 0
    for c in contacts:
        if not c.last_contacted:
            if c.relationship_strength != "cold":
                update_contact(db, c.id, relationship_strength="cold")
                decayed += 1
            continue

        days_since = (today - c.last_contacted).days

        if c.relationship_strength == "close" and days_since > close_threshold_days:
            update_contact(db, c.id, relationship_strength="warm")
            decayed += 1
        elif c.relationship_strength == "warm" and days_since > warm_threshold_days:
            update_contact(db, c.id, relationship_strength="cold")
            decayed += 1

    return decayed


# ─────────────────────────────────────────────────────────
# Outreach suggestions
# ─────────────────────────────────────────────────────────

def suggest_outreach_targets(
    db,
    target_companies: Optional[List[str]] = None,
    target_roles: Optional[List[str]] = None,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    """
    Suggest people to reach out to based on job search goals.

    Scoring:
    - Works at target company: +10
    - Has target role/title: +7
    - Not contacted in 90+ days but warm/close: +5
    - Is recruiter: +2
    - Penalize if contacted in last 30 days: -8
    """
    from tracker import get_all_contacts, get_all_applications

    contacts = get_all_contacts(db)
    applications = get_all_applications(db)

    # Build target company list from applications if not provided
    if not target_companies:
        target_companies = list(set(a.company.lower() for a in applications if a.company))
    else:
        target_companies = [c.lower() for c in target_companies]

    if not target_roles:
        target_roles = []
    else:
        target_roles = [r.lower() for r in target_roles]

    today = date.today()
    scored = []

    for contact in contacts:
        score = 0
        reasons = []
        contact_company = (contact.company or "").lower()
        contact_title = (contact.title or "").lower()
        contact_tags = (contact.tags or "").lower()

        # Company match
        if contact_company and any(tc in contact_company or contact_company in tc for tc in target_companies):
            score += 10
            reasons.append(f"Works at target company ({contact.company})")

        # Title match
        if contact_title and target_roles:
            for role in target_roles:
                if role in contact_title:
                    score += 7
                    reasons.append(f"Has relevant title ({contact.title})")
                    break

        # Stale warm/close contacts
        if contact.last_contacted:
            days_since = (today - contact.last_contacted).days
            if days_since > 90 and contact.relationship_strength in ("warm", "close"):
                score += 5
                reasons.append(f"Haven't contacted in {days_since} days")
            if days_since < 30:
                score -= 8
                reasons.append("Recently contacted")
        elif contact.relationship_strength in ("warm", "close"):
            score += 5
            reasons.append("No contact date recorded")

        # Recruiter bonus
        if "recruiter" in contact_tags:
            score += 2
            reasons.append("Recruiter")

        if score > 0:
            # Determine suggested action
            if score >= 10:
                action = "referral_request"
            elif contact.relationship_strength == "cold":
                action = "coffee_chat"
            else:
                action = "check_in"

            scored.append({
                "contact": contact,
                "score": score,
                "reasons": reasons,
                "suggested_action": action,
            })

    # Sort by score descending
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:limit]


# ─────────────────────────────────────────────────────────
# Coffee chat / outreach message generation
# ─────────────────────────────────────────────────────────

def generate_coffee_chat_request(
    contact_name: str,
    contact_company: Optional[str],
    contact_title: Optional[str],
    user_background: str,
    talking_points: Optional[List[str]] = None,
    relationship: str = "cold",
) -> Dict[str, Any]:
    """
    Generate coffee chat / informational interview request.

    Returns:
        {
            'subject': str,
            'message': str,
            'linkedin_version': str (under 1900 chars),
            'tips': list[str],
        }
    """
    points_str = ""
    if talking_points:
        points_str = "\nTopics I'd like to discuss:\n" + "\n".join(f"- {p}" for p in talking_points)

    greeting_style = "first name" if relationship in ("warm", "close") else "full name"

    prompt = f"""Write a coffee chat / informational interview request message.

RECIPIENT:
- Name: {contact_name} (use {greeting_style} in greeting)
- Company: {contact_company or 'Unknown'}
- Title: {contact_title or 'Unknown'}
- Relationship: {relationship}

MY BACKGROUND (brief):
{user_background[:1000]}
{points_str}

Rules:
- 120-150 words for email version
- Be specific about why I'm reaching out
- Suggest 15-20 minute virtual coffee
- Include 2-3 specific questions
- Offer flexibility on timing
- Low-pressure sign-off
- No long dashes

Output with delimiters:

===SUBJECT_START===
(email subject line)
===SUBJECT_END===

===EMAIL_START===
(full email message, 120-150 words)
===EMAIL_END===

===LINKEDIN_START===
(condensed LinkedIn InMail version, under 1900 characters)
===LINKEDIN_END===

===TIPS_START===
(3-4 tips for sending this request, one per line)
===TIPS_END==="""

    raw = call_claude(
        messages=[{"role": "user", "content": prompt}],
        temperature=0.5,
        max_tokens=2000,
    )

    result = {"subject": "", "message": "", "linkedin_version": "", "tips": []}

    for key, (start, end) in {
        "subject": ("===SUBJECT_START===", "===SUBJECT_END==="),
        "message": ("===EMAIL_START===", "===EMAIL_END==="),
        "linkedin_version": ("===LINKEDIN_START===", "===LINKEDIN_END==="),
        "tips_raw": ("===TIPS_START===", "===TIPS_END==="),
    }.items():
        if start in raw and end in raw:
            content = raw.split(start)[1].split(end)[0].strip()
            if key == "tips_raw":
                result["tips"] = [line.strip().lstrip("- ") for line in content.split("\n") if line.strip()]
            else:
                result[key] = content

    return result


# ─────────────────────────────────────────────────────────
# Referral tracking
# ─────────────────────────────────────────────────────────

def track_referral_outcome(db, referral_id: int, outcome: str) -> Optional[Dict]:
    """
    Update referral with outcome and suggest follow-up actions.

    Returns dict with suggested actions or None if referral not found.
    """
    from tracker import get_referral, update_referral, update_contact, create_follow_up

    referral = get_referral(db, referral_id)
    if not referral:
        return None

    update_referral(db, referral_id, outcome=outcome)

    actions = []

    if outcome in ("got_interview", "got_offer"):
        # Schedule thank-you
        update_referral(db, referral_id, thank_you_sent=False)
        create_follow_up(
            db,
            application_id=referral.application_id,
            scheduled_date=date.today() + timedelta(days=1),
            action_type="Thank You",
            notes=f"Thank {referral.contact.name} for the referral that led to {outcome.replace('_', ' ')}",
        )
        actions.append(f"Thank-you follow-up created for {referral.contact.name}")

        # Upgrade relationship
        if referral.contact.relationship_strength != "close":
            update_contact(db, referral.contact_id, relationship_strength="close")
            actions.append(f"Upgraded {referral.contact.name} to 'close' relationship")

    elif outcome == "rejected":
        create_follow_up(
            db,
            application_id=referral.application_id,
            scheduled_date=date.today() + timedelta(days=2),
            action_type="Email Follow-up",
            notes=f"Thank {referral.contact.name} for the referral effort (regardless of outcome)",
        )
        actions.append("Thank-you follow-up created (for their effort)")

    return {"outcome": outcome, "actions": actions}


# ─────────────────────────────────────────────────────────
# Network gap detection
# ─────────────────────────────────────────────────────────

def detect_network_gaps(db) -> Dict[str, Any]:
    """
    Identify companies you're applying to where you have 0 contacts.

    Returns:
        {
            'gap_companies': list[str],
            'suggestions': list[dict],
        }
    """
    from tracker import get_all_applications, get_all_contacts

    applications = get_all_applications(db)
    contacts = get_all_contacts(db)

    # Build set of companies in network
    network_companies = set()
    for c in contacts:
        if c.company:
            network_companies.add(c.company.lower().strip())

    # Find application companies not in network
    app_companies = set()
    for a in applications:
        if a.company:
            app_companies.add(a.company.strip())

    gap_companies = []
    suggestions = []

    for company in sorted(app_companies):
        if company.lower() not in network_companies:
            gap_companies.append(company)
            search_query = f'{company} "Recruiter" OR "Talent Acquisition"'
            encoded = urllib.parse.quote(search_query)
            suggestions.append({
                "company": company,
                "strategy": "linkedin_search",
                "search_url": f"https://www.linkedin.com/search/results/people/?keywords={encoded}",
            })

    return {
        "gap_companies": gap_companies,
        "suggestions": suggestions,
    }
