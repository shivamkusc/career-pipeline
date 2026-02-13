"""
tracker.py — Application Tracking System
SQLAlchemy models, CRUD helpers, and export utilities for tracking job applications.
"""

import os
import csv
import io
import calendar as cal_module
from datetime import datetime, date, timedelta
from typing import Optional, List, Dict, Any
from collections import defaultdict

from sqlalchemy import create_engine, Column, Integer, String, Text, Float, Boolean, DateTime, Date, ForeignKey, Index, JSON
from sqlalchemy.orm import declarative_base, sessionmaker, relationship, Session

# ─────────────────────────────────────────────────────────
# Database setup
# ─────────────────────────────────────────────────────────

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "applications.db")

engine = create_engine(f"sqlite:///{DB_PATH}", echo=False)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()

# ─────────────────────────────────────────────────────────
# Models
# ─────────────────────────────────────────────────────────

VALID_STATUSES = ["Applied", "Screening", "Interview", "Offer", "Rejected", "Ghosted"]
VALID_FOLLOW_UP_TYPES = ["Email Follow-up", "Thank You", "Check-in"]
VALID_INTERVIEW_TYPES = ["Phone", "Video", "Onsite", "Panel"]
VALID_DOC_TYPES = ["Resume", "Cover Letter", "Portfolio", "Cold Email"]


class Application(Base):
    __tablename__ = "applications"

    id = Column(Integer, primary_key=True, autoincrement=True)
    company = Column(String(200), nullable=False)
    role = Column(String(200), nullable=False)
    date_applied = Column(Date, default=date.today)
    status = Column(String(20), default="Applied")
    salary_range = Column(String(100), nullable=True)
    job_posting_url = Column(Text, nullable=True)
    application_method = Column(String(100), nullable=True)
    notes = Column(Text, nullable=True)
    last_updated = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    job_id = Column(String(8), nullable=True)
    ats_score = Column(Float, nullable=True)
    resume_hash = Column(String(16), nullable=True)
    cover_letter_hash = Column(String(16), nullable=True)

    follow_ups = relationship("FollowUp", back_populates="application", cascade="all, delete-orphan")
    interviews = relationship("Interview", back_populates="application", cascade="all, delete-orphan")
    documents = relationship("DocumentSent", back_populates="application", cascade="all, delete-orphan")


class FollowUp(Base):
    __tablename__ = "follow_ups"

    id = Column(Integer, primary_key=True, autoincrement=True)
    application_id = Column(Integer, ForeignKey("applications.id"), nullable=False)
    scheduled_date = Column(Date, nullable=False)
    action_type = Column(String(50), default="Email Follow-up")
    completed = Column(Boolean, default=False)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    application = relationship("Application", back_populates="follow_ups")


class Interview(Base):
    __tablename__ = "interviews"

    id = Column(Integer, primary_key=True, autoincrement=True)
    application_id = Column(Integer, ForeignKey("applications.id"), nullable=False)
    date_time = Column(DateTime, nullable=False)
    interview_type = Column(String(30), default="Phone")
    interviewer_names = Column(Text, nullable=True)
    outcome = Column(String(30), nullable=True)
    prep_notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    application = relationship("Application", back_populates="interviews")


class DocumentSent(Base):
    __tablename__ = "documents_sent"

    id = Column(Integer, primary_key=True, autoincrement=True)
    application_id = Column(Integer, ForeignKey("applications.id"), nullable=False)
    document_type = Column(String(30))
    version_hash = Column(String(16), nullable=True)
    sent_date = Column(Date, default=date.today)
    file_path = Column(Text, nullable=True)

    application = relationship("Application", back_populates="documents")


# ─── New models: Contacts, Referrals, A/B Testing, Email Tracking ───

VALID_RELATIONSHIP_STRENGTHS = ["cold", "warm", "close"]
VALID_CONTACT_SOURCES = ["linkedin_import", "recruiter_search", "manual", "email_auto"]
VALID_INTERACTION_TYPES = ["email", "linkedin_message", "phone", "coffee_chat", "referral_request", "thank_you", "other"]
VALID_REFERRAL_METHODS = ["direct_intro", "forwarded_resume", "recommendation", "other"]
VALID_REFERRAL_OUTCOMES = ["pending", "got_interview", "got_offer", "rejected", "no_response"]
VALID_VARIANT_OUTCOMES = ["no_response", "rejection", "screening", "interview", "offer"]
VALID_EMAIL_STAGES = [
    "application_received", "screening", "interview_invite",
    "interview_schedule", "rejection", "offer", "other",
]
VALID_FOLLOWUP_TYPES_EXTENDED = [
    "initial_check_in", "post_interview_thank_you", "offer_negotiation",
    "rejection_response", "networking", "custom",
]


class Contact(Base):
    __tablename__ = "contacts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False)
    email = Column(String(200), unique=True, nullable=True)
    company = Column(String(200), nullable=True)
    title = Column(String(200), nullable=True)
    linkedin_url = Column(Text, nullable=True)
    relationship_strength = Column(String(20), default="cold")
    source = Column(String(30), default="manual")
    last_contacted = Column(Date, nullable=True)
    contact_frequency_days = Column(Integer, default=90)
    tags = Column(Text, nullable=True)  # comma-separated
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    interactions = relationship("ContactInteraction", back_populates="contact", cascade="all, delete-orphan")
    referrals = relationship("Referral", back_populates="contact", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_contacts_email", "email"),
        Index("ix_contacts_company", "company"),
        Index("ix_contacts_last_contacted", "last_contacted"),
    )


class ContactInteraction(Base):
    __tablename__ = "contact_interactions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    contact_id = Column(Integer, ForeignKey("contacts.id"), nullable=False)
    interaction_date = Column(Date, default=date.today)
    interaction_type = Column(String(30), default="email")
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    contact = relationship("Contact", back_populates="interactions")

    __table_args__ = (
        Index("ix_interactions_contact", "contact_id"),
        Index("ix_interactions_date", "interaction_date"),
    )


class Referral(Base):
    __tablename__ = "referrals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    application_id = Column(Integer, ForeignKey("applications.id"), nullable=False)
    contact_id = Column(Integer, ForeignKey("contacts.id"), nullable=False)
    referral_date = Column(Date, default=date.today)
    referral_method = Column(String(30), default="direct_intro")
    outcome = Column(String(20), default="pending")
    thank_you_sent = Column(Boolean, default=False)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    application = relationship("Application", backref="referrals")
    contact = relationship("Contact", back_populates="referrals")

    __table_args__ = (
        Index("ix_referrals_application", "application_id"),
        Index("ix_referrals_contact", "contact_id"),
    )


class ABTestVariant(Base):
    __tablename__ = "ab_test_variants"

    id = Column(Integer, primary_key=True, autoincrement=True)
    application_id = Column(Integer, ForeignKey("applications.id"), nullable=True)
    job_id = Column(String(8), nullable=True)
    variant_name = Column(String(50), nullable=False)
    variant_description = Column(Text, nullable=True)
    cover_letter_text = Column(Text, nullable=True)
    cold_email_text = Column(Text, nullable=True)
    linkedin_message_text = Column(Text, nullable=True)
    strategy_prompt = Column(Text, nullable=True)
    used = Column(Boolean, default=False)
    response_received = Column(Boolean, default=False)
    response_time_hours = Column(Integer, nullable=True)
    outcome = Column(String(20), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    application = relationship("Application", backref="variants")

    __table_args__ = (
        Index("ix_variants_application", "application_id"),
        Index("ix_variants_used", "used"),
        Index("ix_variants_response", "response_received"),
    )


class EmailTracking(Base):
    __tablename__ = "email_tracking"

    id = Column(Integer, primary_key=True, autoincrement=True)
    application_id = Column(Integer, ForeignKey("applications.id"), nullable=True)
    email_id = Column(String(200), nullable=True)
    sender_email = Column(String(200), nullable=True)
    sender_name = Column(String(200), nullable=True)
    subject = Column(Text, nullable=True)
    body_preview = Column(Text, nullable=True)  # first 500 chars
    received_date = Column(DateTime, nullable=True)
    detected_stage = Column(String(30), nullable=True)
    confidence_score = Column(Float, default=0.0)
    auto_matched = Column(Boolean, default=False)
    parsed_data = Column(JSON, nullable=True)
    processed = Column(Boolean, default=False)
    user_confirmed = Column(Boolean, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    application = relationship("Application", backref="tracked_emails")

    __table_args__ = (
        Index("ix_email_sender", "sender_email"),
        Index("ix_email_received", "received_date"),
        Index("ix_email_processed", "processed"),
    )


class OAuthToken(Base):
    __tablename__ = "oauth_tokens"

    id = Column(Integer, primary_key=True, autoincrement=True)
    provider = Column(String(20), nullable=False)  # "gmail" or "outlook"
    access_token_encrypted = Column(Text, nullable=True)
    refresh_token_encrypted = Column(Text, nullable=True)
    token_expiry = Column(DateTime, nullable=True)
    email_address = Column(String(200), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AppSetting(Base):
    __tablename__ = "app_settings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String(100), unique=True, nullable=False)
    value = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ─────────────────────────────────────────────────────────
# Database initialization
# ─────────────────────────────────────────────────────────

def init_db():
    """Create all tables if they don't exist."""
    Base.metadata.create_all(engine)


def get_db() -> Session:
    """Get a new database session. Caller must close it."""
    return SessionLocal()


# ─────────────────────────────────────────────────────────
# Application CRUD
# ─────────────────────────────────────────────────────────

def create_application(db: Session, **kwargs) -> Application:
    """Create a new application record."""
    app = Application(**kwargs)
    db.add(app)
    db.commit()
    db.refresh(app)
    return app


def get_application(db: Session, app_id: int) -> Optional[Application]:
    """Get an application by ID with all relationships loaded."""
    return db.query(Application).filter(Application.id == app_id).first()


def get_all_applications(db: Session, status: Optional[str] = None,
                         search: Optional[str] = None) -> List[Application]:
    """Get all applications, optionally filtered by status or search query."""
    q = db.query(Application)
    if status and status in VALID_STATUSES:
        q = q.filter(Application.status == status)
    if search:
        pattern = f"%{search}%"
        q = q.filter(
            (Application.company.ilike(pattern)) |
            (Application.role.ilike(pattern))
        )
    return q.order_by(Application.last_updated.desc()).all()


def update_application(db: Session, app_id: int, **kwargs) -> Optional[Application]:
    """Update fields on an application."""
    app = get_application(db, app_id)
    if not app:
        return None
    for key, value in kwargs.items():
        if hasattr(app, key) and key != "id":
            setattr(app, key, value)
    app.last_updated = datetime.utcnow()
    db.commit()
    db.refresh(app)
    return app


def delete_application(db: Session, app_id: int) -> bool:
    """Delete an application and all related records."""
    app = get_application(db, app_id)
    if not app:
        return False
    db.delete(app)
    db.commit()
    return True


# ─────────────────────────────────────────────────────────
# Follow-up CRUD
# ─────────────────────────────────────────────────────────

def create_follow_up(db: Session, **kwargs) -> FollowUp:
    """Create a new follow-up."""
    fu = FollowUp(**kwargs)
    db.add(fu)
    db.commit()
    db.refresh(fu)
    return fu


def mark_follow_up_complete(db: Session, follow_up_id: int) -> Optional[FollowUp]:
    """Mark a follow-up as completed."""
    fu = db.query(FollowUp).filter(FollowUp.id == follow_up_id).first()
    if not fu:
        return None
    fu.completed = True
    db.commit()
    db.refresh(fu)
    return fu


def delete_follow_up(db: Session, follow_up_id: int) -> bool:
    """Delete a follow-up."""
    fu = db.query(FollowUp).filter(FollowUp.id == follow_up_id).first()
    if not fu:
        return False
    db.delete(fu)
    db.commit()
    return True


# ─────────────────────────────────────────────────────────
# Interview CRUD
# ─────────────────────────────────────────────────────────

def create_interview(db: Session, **kwargs) -> Interview:
    """Create a new interview."""
    iv = Interview(**kwargs)
    db.add(iv)
    db.commit()
    db.refresh(iv)
    return iv


def update_interview_outcome(db: Session, interview_id: int, outcome: str) -> Optional[Interview]:
    """Update an interview's outcome."""
    iv = db.query(Interview).filter(Interview.id == interview_id).first()
    if not iv:
        return None
    iv.outcome = outcome
    db.commit()
    db.refresh(iv)
    return iv


def delete_interview(db: Session, interview_id: int) -> bool:
    """Delete an interview."""
    iv = db.query(Interview).filter(Interview.id == interview_id).first()
    if not iv:
        return False
    db.delete(iv)
    db.commit()
    return True


# ─────────────────────────────────────────────────────────
# Document tracking
# ─────────────────────────────────────────────────────────

def create_document_sent(db: Session, **kwargs) -> DocumentSent:
    """Track a document sent with an application."""
    doc = DocumentSent(**kwargs)
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


# ─────────────────────────────────────────────────────────
# Enrichment (computed fields for templates)
# ─────────────────────────────────────────────────────────

def enrich_application(app: Application) -> Dict[str, Any]:
    """Add computed fields for template rendering."""
    days_since = (date.today() - app.date_applied).days if app.date_applied else 0

    overdue_followup = False
    next_followup = None
    for fu in app.follow_ups:
        if not fu.completed and fu.scheduled_date < date.today():
            overdue_followup = True
        if not fu.completed and (next_followup is None or fu.scheduled_date < next_followup):
            next_followup = fu.scheduled_date

    next_interview = None
    for iv in app.interviews:
        iv_date = iv.date_time.date() if iv.date_time else None
        if iv_date and iv_date >= date.today():
            if next_interview is None or iv_date < next_interview:
                next_interview = iv_date

    urgency = "normal"
    if overdue_followup:
        urgency = "overdue"
    elif app.status == "Applied" and days_since > 7:
        urgency = "stale"

    return {
        "app": app,
        "days_since": days_since,
        "urgency": urgency,
        "overdue_followup": overdue_followup,
        "next_followup": next_followup,
        "next_interview": next_interview,
    }


# ─────────────────────────────────────────────────────────
# Export utilities
# ─────────────────────────────────────────────────────────

def export_csv(db: Session) -> str:
    """Export all applications as CSV string."""
    apps = get_all_applications(db)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "ID", "Company", "Role", "Date Applied", "Status",
        "Salary Range", "URL", "Method", "ATS Score", "Notes", "Last Updated"
    ])
    for a in apps:
        writer.writerow([
            a.id, a.company, a.role,
            a.date_applied.isoformat() if a.date_applied else "",
            a.status, a.salary_range or "", a.job_posting_url or "",
            a.application_method or "",
            f"{int(a.ats_score * 100)}%" if a.ats_score else "",
            a.notes or "",
            a.last_updated.isoformat() if a.last_updated else "",
        ])
    return output.getvalue()


def export_notion_markdown(db: Session) -> str:
    """Export applications as a Notion-compatible markdown table."""
    apps = get_all_applications(db)
    lines = [
        "| Company | Role | Date Applied | Status | ATS Score | Notes |",
        "|---------|------|-------------|--------|-----------|-------|",
    ]
    for a in apps:
        date_str = a.date_applied.isoformat() if a.date_applied else ""
        ats_str = f"{int(a.ats_score * 100)}%" if a.ats_score else "N/A"
        notes_short = (a.notes or "").replace("|", "/")[:60]
        company = (a.company or "").replace("|", "/")
        role = (a.role or "").replace("|", "/")
        lines.append(f"| {company} | {role} | {date_str} | {a.status} | {ats_str} | {notes_short} |")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────
# Analytics
# ─────────────────────────────────────────────────────────

def get_analytics(db: Session) -> Dict[str, Any]:
    """Compute analytics data for the dashboard."""
    apps = get_all_applications(db)

    # Status funnel counts
    funnel = {s: 0 for s in VALID_STATUSES}
    for a in apps:
        if a.status in funnel:
            funnel[a.status] += 1

    # Response time: avg days from Applied to last_updated for non-Applied apps
    response_times = []
    for a in apps:
        if a.status != "Applied" and a.date_applied and a.last_updated:
            delta = (a.last_updated.date() - a.date_applied).days
            if delta >= 0:
                response_times.append(delta)
    avg_response = round(sum(response_times) / len(response_times), 1) if response_times else 0

    # Weekly volume: applications per week for last 12 weeks
    weekly_volume = []
    today = date.today()
    for i in range(11, -1, -1):
        week_start = today - timedelta(days=today.weekday() + 7 * i)
        week_end = week_start + timedelta(days=6)
        count = sum(
            1 for a in apps
            if a.date_applied and week_start <= a.date_applied <= week_end
        )
        label = week_start.strftime("%b %d")
        weekly_volume.append({"label": label, "count": count})

    # ATS score brackets
    ats_brackets = {"0-25%": 0, "25-50%": 0, "50-75%": 0, "75-100%": 0}
    for a in apps:
        if a.ats_score is not None:
            pct = a.ats_score * 100
            if pct < 25:
                ats_brackets["0-25%"] += 1
            elif pct < 50:
                ats_brackets["25-50%"] += 1
            elif pct < 75:
                ats_brackets["50-75%"] += 1
            else:
                ats_brackets["75-100%"] += 1

    # Success rate by day of week
    day_stats = defaultdict(lambda: {"total": 0, "positive": 0})
    for a in apps:
        if a.date_applied:
            day_name = a.date_applied.strftime("%A")
            day_stats[day_name]["total"] += 1
            if a.status in ("Screening", "Interview", "Offer"):
                day_stats[day_name]["positive"] += 1

    day_success = {}
    for day in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]:
        stats = day_stats[day]
        if stats["total"] > 0:
            day_success[day] = round(stats["positive"] / stats["total"] * 100)
        else:
            day_success[day] = 0

    # Conversion rates
    total = len(apps)
    conversions = {}
    if total > 0:
        conversions["applied_to_screening"] = round(
            sum(1 for a in apps if a.status in ("Screening", "Interview", "Offer")) / total * 100
        )
        conversions["screening_to_interview"] = round(
            sum(1 for a in apps if a.status in ("Interview", "Offer"))
            / max(sum(1 for a in apps if a.status in ("Screening", "Interview", "Offer")), 1)
            * 100
        )
        conversions["interview_to_offer"] = round(
            sum(1 for a in apps if a.status == "Offer")
            / max(sum(1 for a in apps if a.status in ("Interview", "Offer")), 1)
            * 100
        )

    return {
        "total": total,
        "funnel": funnel,
        "avg_response_days": avg_response,
        "weekly_volume": weekly_volume,
        "ats_brackets": ats_brackets,
        "day_success": day_success,
        "conversions": conversions,
    }


# ─────────────────────────────────────────────────────────
# Calendar helpers
# ─────────────────────────────────────────────────────────

def get_calendar_data(db: Session, year: int, month: int) -> Dict[str, Any]:
    """Build calendar grid data for a given month."""
    apps = get_all_applications(db)

    # Collect events
    events_by_date = defaultdict(list)
    for a in apps:
        for fu in a.follow_ups:
            if not fu.completed and fu.scheduled_date.year == year and fu.scheduled_date.month == month:
                events_by_date[fu.scheduled_date.day].append({
                    "type": "follow_up",
                    "label": f"{fu.action_type}: {a.company}",
                    "app_id": a.id,
                })
        for iv in a.interviews:
            iv_date = iv.date_time.date() if iv.date_time else None
            if iv_date and iv_date.year == year and iv_date.month == month:
                events_by_date[iv_date.day].append({
                    "type": "interview",
                    "label": f"{iv.interview_type} @ {a.company}",
                    "app_id": a.id,
                })

    # Build weeks grid
    first_weekday, num_days = cal_module.monthrange(year, month)
    # Adjust to Monday=0 start
    weeks = []
    current_week = [None] * first_weekday
    for day in range(1, num_days + 1):
        current_week.append({
            "day": day,
            "events": events_by_date.get(day, []),
            "is_today": (day == date.today().day and month == date.today().month and year == date.today().year),
        })
        if len(current_week) == 7:
            weeks.append(current_week)
            current_week = []
    if current_week:
        current_week.extend([None] * (7 - len(current_week)))
        weeks.append(current_week)

    # Prev/next month
    if month == 1:
        prev_month, prev_year = 12, year - 1
    else:
        prev_month, prev_year = month - 1, year
    if month == 12:
        next_month, next_year = 1, year + 1
    else:
        next_month, next_year = month + 1, year

    month_name = cal_module.month_name[month]

    return {
        "weeks": weeks,
        "month": month,
        "year": year,
        "month_name": month_name,
        "prev_month": prev_month,
        "prev_year": prev_year,
        "next_month": next_month,
        "next_year": next_year,
    }


# ─────────────────────────────────────────────────────────
# Contact CRUD
# ─────────────────────────────────────────────────────────

def create_contact(db: Session, **kwargs) -> Contact:
    c = Contact(**kwargs)
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def get_contact(db: Session, contact_id: int) -> Optional[Contact]:
    return db.query(Contact).filter(Contact.id == contact_id).first()


def get_contact_by_email(db: Session, email: str) -> Optional[Contact]:
    if not email:
        return None
    return db.query(Contact).filter(Contact.email == email).first()


def get_all_contacts(db: Session, search: Optional[str] = None,
                     company: Optional[str] = None,
                     strength: Optional[str] = None,
                     tag: Optional[str] = None) -> List[Contact]:
    q = db.query(Contact)
    if search:
        pattern = f"%{search}%"
        q = q.filter(
            (Contact.name.ilike(pattern)) |
            (Contact.company.ilike(pattern)) |
            (Contact.email.ilike(pattern))
        )
    if company:
        q = q.filter(Contact.company.ilike(f"%{company}%"))
    if strength and strength in VALID_RELATIONSHIP_STRENGTHS:
        q = q.filter(Contact.relationship_strength == strength)
    if tag:
        q = q.filter(Contact.tags.ilike(f"%{tag}%"))
    return q.order_by(Contact.updated_at.desc()).all()


def update_contact(db: Session, contact_id: int, **kwargs) -> Optional[Contact]:
    c = get_contact(db, contact_id)
    if not c:
        return None
    for key, value in kwargs.items():
        if hasattr(c, key) and key != "id":
            setattr(c, key, value)
    c.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(c)
    return c


def delete_contact(db: Session, contact_id: int) -> bool:
    c = get_contact(db, contact_id)
    if not c:
        return False
    db.delete(c)
    db.commit()
    return True


# ─────────────────────────────────────────────────────────
# Contact Interaction CRUD
# ─────────────────────────────────────────────────────────

def create_interaction(db: Session, **kwargs) -> ContactInteraction:
    ci = ContactInteraction(**kwargs)
    db.add(ci)
    db.commit()
    db.refresh(ci)
    # Update contact's last_contacted
    contact = get_contact(db, ci.contact_id)
    if contact:
        interaction_date = ci.interaction_date or date.today()
        if not contact.last_contacted or interaction_date > contact.last_contacted:
            contact.last_contacted = interaction_date
            db.commit()
    return ci


def get_interactions_for_contact(db: Session, contact_id: int) -> List[ContactInteraction]:
    return (db.query(ContactInteraction)
            .filter(ContactInteraction.contact_id == contact_id)
            .order_by(ContactInteraction.interaction_date.desc())
            .all())


# ─────────────────────────────────────────────────────────
# Referral CRUD
# ─────────────────────────────────────────────────────────

def create_referral(db: Session, **kwargs) -> Referral:
    r = Referral(**kwargs)
    db.add(r)
    db.commit()
    db.refresh(r)
    return r


def get_referral(db: Session, referral_id: int) -> Optional[Referral]:
    return db.query(Referral).filter(Referral.id == referral_id).first()


def get_referrals_for_application(db: Session, app_id: int) -> List[Referral]:
    return db.query(Referral).filter(Referral.application_id == app_id).all()


def update_referral(db: Session, referral_id: int, **kwargs) -> Optional[Referral]:
    r = get_referral(db, referral_id)
    if not r:
        return None
    for key, value in kwargs.items():
        if hasattr(r, key) and key != "id":
            setattr(r, key, value)
    db.commit()
    db.refresh(r)
    return r


# ─────────────────────────────────────────────────────────
# A/B Test Variant CRUD
# ─────────────────────────────────────────────────────────

def create_variant(db: Session, **kwargs) -> ABTestVariant:
    v = ABTestVariant(**kwargs)
    db.add(v)
    db.commit()
    db.refresh(v)
    return v


def get_variants_for_job(db: Session, job_id: str) -> List[ABTestVariant]:
    return (db.query(ABTestVariant)
            .filter(ABTestVariant.job_id == job_id)
            .order_by(ABTestVariant.created_at)
            .all())


def get_variant(db: Session, variant_id: int) -> Optional[ABTestVariant]:
    return db.query(ABTestVariant).filter(ABTestVariant.id == variant_id).first()


def update_variant(db: Session, variant_id: int, **kwargs) -> Optional[ABTestVariant]:
    v = get_variant(db, variant_id)
    if not v:
        return None
    for key, value in kwargs.items():
        if hasattr(v, key) and key != "id":
            setattr(v, key, value)
    db.commit()
    db.refresh(v)
    return v


def get_all_variants_with_outcomes(db: Session) -> List[ABTestVariant]:
    return (db.query(ABTestVariant)
            .filter(ABTestVariant.used == True)
            .order_by(ABTestVariant.created_at.desc())
            .all())


# ─────────────────────────────────────────────────────────
# Email Tracking CRUD
# ─────────────────────────────────────────────────────────

def create_email_tracking(db: Session, **kwargs) -> EmailTracking:
    et = EmailTracking(**kwargs)
    db.add(et)
    db.commit()
    db.refresh(et)
    return et


def get_unprocessed_emails(db: Session) -> List[EmailTracking]:
    return (db.query(EmailTracking)
            .filter(EmailTracking.processed == False)
            .order_by(EmailTracking.received_date)
            .all())


def get_unconfirmed_matches(db: Session) -> List[EmailTracking]:
    return (db.query(EmailTracking)
            .filter(EmailTracking.auto_matched == True,
                    EmailTracking.user_confirmed == None)
            .order_by(EmailTracking.received_date.desc())
            .all())


def get_emails_for_application(db: Session, app_id: int) -> List[EmailTracking]:
    return (db.query(EmailTracking)
            .filter(EmailTracking.application_id == app_id)
            .order_by(EmailTracking.received_date.desc())
            .all())


# ─────────────────────────────────────────────────────────
# Settings CRUD
# ─────────────────────────────────────────────────────────

def get_setting(db: Session, key: str, default: Optional[str] = None) -> Optional[str]:
    s = db.query(AppSetting).filter(AppSetting.key == key).first()
    return s.value if s else default


def set_setting(db: Session, key: str, value: str):
    s = db.query(AppSetting).filter(AppSetting.key == key).first()
    if s:
        s.value = value
        s.updated_at = datetime.utcnow()
    else:
        s = AppSetting(key=key, value=value)
        db.add(s)
    db.commit()


# ─────────────────────────────────────────────────────────
# Extended Analytics (includes network + variant data)
# ─────────────────────────────────────────────────────────

def get_extended_analytics(db: Session) -> Dict[str, Any]:
    """Get analytics including network and A/B testing data."""
    base = get_analytics(db)

    # Referral stats
    all_referrals = db.query(Referral).all()
    referral_count = len(all_referrals)
    referral_success = sum(1 for r in all_referrals if r.outcome in ("got_interview", "got_offer"))
    base["referral_count"] = referral_count
    base["referral_success_rate"] = round(referral_success / max(referral_count, 1) * 100)

    # Network stats
    contacts = db.query(Contact).all()
    base["total_contacts"] = len(contacts)
    base["contacts_by_strength"] = {
        "cold": sum(1 for c in contacts if c.relationship_strength == "cold"),
        "warm": sum(1 for c in contacts if c.relationship_strength == "warm"),
        "close": sum(1 for c in contacts if c.relationship_strength == "close"),
    }

    # Variant performance summary
    used_variants = db.query(ABTestVariant).filter(ABTestVariant.used == True).all()
    variant_stats = defaultdict(lambda: {"count": 0, "responses": 0, "interviews": 0, "offers": 0})
    for v in used_variants:
        vs = variant_stats[v.variant_name]
        vs["count"] += 1
        if v.response_received:
            vs["responses"] += 1
        if v.outcome == "interview":
            vs["interviews"] += 1
        elif v.outcome == "offer":
            vs["offers"] += 1
    base["variant_stats"] = dict(variant_stats)

    # Follow-up effectiveness
    all_fus = db.query(FollowUp).all()
    completed_fus = [f for f in all_fus if f.completed]
    base["followup_total"] = len(all_fus)
    base["followup_completed"] = len(completed_fus)

    return base
