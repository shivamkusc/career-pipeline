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

from sqlalchemy import create_engine, Column, Integer, String, Text, Float, Boolean, DateTime, Date, ForeignKey
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
