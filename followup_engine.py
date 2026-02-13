"""
followup_engine.py — AI-powered follow-up message generation
Generates contextual follow-up messages for job applications using LLM.
"""

import json
from datetime import date, timedelta
from typing import Optional, Dict, List, Any

from ai_engine import call_claude, read_style_sample, strip_code_fences


# ─────────────────────────────────────────────────────────
# Follow-up message generation
# ─────────────────────────────────────────────────────────

FOLLOWUP_PROMPTS = {
    "initial_check_in": {
        "description": "7-10 days after application",
        "tone": "professional, brief, reaffirm interest",
        "length": "75-125 words",
        "instructions": """Write a follow-up email for a job application submitted {days_since_applied} days ago.
- Reference a specific detail from the job description
- Reaffirm your unique value proposition
- Keep it brief and respectful of their time
- Don't be desperate or over-eager""",
    },
    "post_interview_thank_you": {
        "description": "Within 24 hours of interview",
        "tone": "enthusiastic but not desperate",
        "length": "100-150 words",
        "instructions": """Write a thank-you email after an interview.
- Reference a specific topic from the conversation: {interview_notes}
- Interviewer name: {interviewer_name}
- Reinforce your fit for the role
- Offer to provide additional information
- Be genuine, not formulaic""",
    },
    "offer_negotiation": {
        "description": "After receiving offer",
        "tone": "grateful + confident",
        "length": "150-200 words",
        "instructions": """Write a salary negotiation email.
- Current offer: ${offer_amount:,}
- Market rate research: ${market_rate:,}
- Express genuine gratitude for the offer
- Provide specific justification for the ask
- Use flexible language ("I was hoping we could discuss...")
- Anchoring: mention market range up to 15% above your target
- Don't give ultimatums""",
    },
    "rejection_response": {
        "description": "After receiving rejection",
        "tone": "gracious, forward-looking",
        "length": "60-90 words",
        "instructions": """Write a response to a job rejection.
- Thank them sincerely for their time
- Ask for specific feedback (optional, professional)
- Express interest in future opportunities
- Suggest staying connected
- Keep it short and dignified
- Rejection reason (if known): {rejection_reason}""",
    },
    "networking": {
        "description": "Coffee chat / networking request",
        "tone": "respectful of their time, specific ask",
        "length": "100-130 words",
        "instructions": """Write a networking / coffee chat request.
- Contact: {contact_name} at {contact_company}, {contact_title}
- Mention mutual connection or commonality if available
- Include 2-3 specific questions you'd like to discuss
- Suggest a 15-20 minute timeframe
- Be respectful of their schedule""",
    },
    "custom": {
        "description": "Custom follow-up",
        "tone": "as specified",
        "length": "100-200 words",
        "instructions": """Write a follow-up message based on this context:
{custom_instructions}""",
    },
}


def generate_followup_message(
    follow_up_type: str,
    context: Dict[str, Any],
    user_style_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Generate a contextual follow-up message via LLM.

    Args:
        follow_up_type: One of the FOLLOWUP_PROMPTS keys
        context: Dictionary with relevant context (application data, etc.)
        user_style_path: Path to style sample file (optional)

    Returns:
        {
            'subject': str,
            'message': str,
            'suggested_send_date': str (ISO date),
            'tips': list[str],
            'variant_note': str,
        }
    """
    template = FOLLOWUP_PROMPTS.get(follow_up_type, FOLLOWUP_PROMPTS["custom"])

    # Build context string for the prompt
    company = context.get("company", "the company")
    role = context.get("role", "the position")
    days_since = context.get("days_since_applied", 7)

    # Format instructions with context values
    instructions = template["instructions"]
    format_vars = {
        "days_since_applied": days_since,
        "interviewer_name": context.get("interviewer_name", "the interviewer"),
        "interview_notes": context.get("interview_notes", "general discussion"),
        "offer_amount": context.get("offer_amount", 0),
        "market_rate": context.get("market_rate", 0),
        "rejection_reason": context.get("rejection_reason", "not specified"),
        "contact_name": context.get("contact_name", ""),
        "contact_company": context.get("contact_company", ""),
        "contact_title": context.get("contact_title", ""),
        "custom_instructions": context.get("custom_instructions", ""),
    }
    try:
        instructions = instructions.format(**format_vars)
    except (KeyError, ValueError):
        pass

    # Read user style if provided
    style_section = ""
    if user_style_path:
        style_text = read_style_sample(user_style_path)
        if style_text.strip():
            style_section = f"""
MY WRITING STYLE SAMPLE (match voice, not content):
{style_text[:2000]}
"""

    system_prompt = f"""You are a career communications assistant. Write follow-up messages that
sound natural and human. Match the user's voice if a style sample is provided.
{style_section}
Rules:
- Allow 1-2 tiny natural grammar imperfections (contractions, informal transitions)
- Never use long dash characters (use commas or parentheses instead)
- Never invent facts about the candidate
- Be concise and respectful of the recipient's time"""

    prompt = f"""Generate a follow-up message for this context:

COMPANY: {company}
ROLE: {role}
FOLLOW-UP TYPE: {follow_up_type} ({template['description']})
TONE: {template['tone']}
TARGET LENGTH: {template['length']}

{instructions}

ADDITIONAL CONTEXT:
{json.dumps({k: v for k, v in context.items() if k not in ('custom_instructions',)}, indent=2, default=str)}

Output using these delimiters:

===SUBJECT_START===
(Email subject line - specific, not generic)
===SUBJECT_END===

===MESSAGE_START===
(The follow-up message)
===MESSAGE_END===

===TIPS_START===
(3-4 tactical tips for sending this follow-up, one per line)
===TIPS_END===

===NOTE_START===
(Brief note about the tone/approach of this message and alternatives)
===NOTE_END==="""

    raw = call_claude(
        messages=[{"role": "user", "content": prompt}],
        system=system_prompt,
        temperature=0.5,
        max_tokens=2000,
    )

    # Parse delimited sections
    result = {
        "subject": "",
        "message": "",
        "tips": [],
        "variant_note": "",
        "suggested_send_date": _suggest_send_date(follow_up_type, context),
    }

    for key, (start, end) in {
        "subject": ("===SUBJECT_START===", "===SUBJECT_END==="),
        "message": ("===MESSAGE_START===", "===MESSAGE_END==="),
        "tips_raw": ("===TIPS_START===", "===TIPS_END==="),
        "variant_note": ("===NOTE_START===", "===NOTE_END==="),
    }.items():
        if start in raw and end in raw:
            content = raw.split(start)[1].split(end)[0].strip()
            if key == "tips_raw":
                result["tips"] = [line.strip().lstrip("- ") for line in content.split("\n") if line.strip()]
            else:
                result[key] = content

    return result


def _suggest_send_date(follow_up_type: str, context: Dict) -> str:
    """Calculate the recommended send date based on follow-up type."""
    today = date.today()

    if follow_up_type == "initial_check_in":
        days_since = context.get("days_since_applied", 0)
        if days_since < 7:
            return (today + timedelta(days=7 - days_since)).isoformat()
        return today.isoformat()

    elif follow_up_type == "post_interview_thank_you":
        return today.isoformat()  # Send same day

    elif follow_up_type == "offer_negotiation":
        return (today + timedelta(days=2)).isoformat()

    elif follow_up_type == "rejection_response":
        return (today + timedelta(days=1)).isoformat()

    elif follow_up_type == "networking":
        return (today + timedelta(days=1)).isoformat()

    return today.isoformat()


# ─────────────────────────────────────────────────────────
# Follow-up schedule suggestion
# ─────────────────────────────────────────────────────────

def suggest_followup_schedule(
    status: str,
    application_method: Optional[str] = None,
    days_since_applied: int = 0,
) -> List[Dict[str, Any]]:
    """
    Generate recommended follow-up timeline based on application status and method.

    Returns list of suggested follow-ups with type, timing, priority, and reason.
    """
    suggestions = []

    if status == "Applied":
        if application_method == "referral":
            suggestions.append({
                "type": "initial_check_in",
                "days_after": 5,
                "priority": "high",
                "reason": "Referral applications warrant earlier follow-up",
            })
        elif application_method == "linkedin":
            suggestions.append({
                "type": "initial_check_in",
                "days_after": 14,
                "priority": "low",
                "reason": "LinkedIn Easy Apply has longer response cycles",
            })
        else:
            suggestions.append({
                "type": "initial_check_in",
                "days_after": 10,
                "priority": "medium",
                "reason": "Standard follow-up timing for direct applications",
            })

    elif status == "Screening":
        suggestions.append({
            "type": "post_interview_thank_you",
            "days_after": 0,
            "priority": "high",
            "reason": "Send thank-you same day as screening call",
        })
        suggestions.append({
            "type": "initial_check_in",
            "days_after": 5,
            "priority": "medium",
            "reason": "Follow up if no response after screening",
        })

    elif status == "Interview":
        suggestions.append({
            "type": "post_interview_thank_you",
            "days_after": 0,
            "priority": "high",
            "reason": "Send thank-you within 24 hours of interview",
        })
        suggestions.append({
            "type": "initial_check_in",
            "days_after": 3,
            "priority": "high",
            "reason": "Check in after final interview if no response",
        })

    elif status == "Offer":
        suggestions.append({
            "type": "offer_negotiation",
            "days_after": 2,
            "priority": "high",
            "reason": "Respond to offer within 2-3 days with negotiation",
        })

    elif status == "Rejected":
        suggestions.append({
            "type": "rejection_response",
            "days_after": 1,
            "priority": "medium",
            "reason": "Gracious response keeps the door open for future roles",
        })

    return suggestions


# ─────────────────────────────────────────────────────────
# Batch follow-up generation
# ─────────────────────────────────────────────────────────

def batch_generate_followups(applications: List[Dict]) -> Dict[str, Any]:
    """
    Generate follow-ups for multiple applications in one LLM call.
    More efficient for weekly follow-up routine.

    Args:
        applications: List of dicts with {id, company, role, status, days_since, method}

    Returns:
        Dict mapping application id to generated message dict
    """
    if not applications:
        return {}

    # Build combined prompt
    app_sections = []
    for i, app in enumerate(applications):
        app_sections.append(
            f"APPLICATION {i+1} (ID: {app['id']}):\n"
            f"  Company: {app.get('company', 'Unknown')}\n"
            f"  Role: {app.get('role', 'Unknown')}\n"
            f"  Status: {app.get('status', 'Applied')}\n"
            f"  Days since applied: {app.get('days_since', 0)}\n"
            f"  Method: {app.get('method', 'direct')}"
        )

    prompt = f"""Generate brief follow-up check-in emails for these {len(applications)} applications.
For each, write a short (75-100 word) professional follow-up email.

{chr(10).join(app_sections)}

For EACH application, output:

===APP_{{ID}}_SUBJECT_START===
(subject line)
===APP_{{ID}}_SUBJECT_END===

===APP_{{ID}}_MESSAGE_START===
(message body)
===APP_{{ID}}_MESSAGE_END===

Where {{ID}} is the application ID from above."""

    raw = call_claude(
        messages=[{"role": "user", "content": prompt}],
        temperature=0.5,
        max_tokens=4000,
    )

    results = {}
    for app in applications:
        app_id = str(app["id"])
        subject_start = f"===APP_{app_id}_SUBJECT_START==="
        subject_end = f"===APP_{app_id}_SUBJECT_END==="
        msg_start = f"===APP_{app_id}_MESSAGE_START==="
        msg_end = f"===APP_{app_id}_MESSAGE_END==="

        subject = ""
        message = ""
        if subject_start in raw and subject_end in raw:
            subject = raw.split(subject_start)[1].split(subject_end)[0].strip()
        if msg_start in raw and msg_end in raw:
            message = raw.split(msg_start)[1].split(msg_end)[0].strip()

        if subject or message:
            results[app_id] = {
                "subject": subject,
                "message": message,
                "suggested_send_date": date.today().isoformat(),
            }

    return results
