"""
email_monitor.py — Email integration and auto-tracking
Classifies incoming emails, matches them to applications, and creates follow-ups.
Supports Gmail and Outlook via OAuth.
"""

import os
import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime, date, timedelta
from typing import Optional, Dict, List, Tuple, Any

from ai_engine import call_claude, parse_json_response

logger = logging.getLogger(__name__)

# Try importing optional OAuth libraries
try:
    from cryptography.fernet import Fernet
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False

try:
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import Flow
    from googleapiclient.discovery import build
    HAS_GMAIL = True
except ImportError:
    HAS_GMAIL = False

try:
    import msal
    import requests as http_requests
    HAS_OUTLOOK = True
except ImportError:
    HAS_OUTLOOK = False


# ─────────────────────────────────────────────────────────
# Encryption helpers
# ─────────────────────────────────────────────────────────

def _get_fernet():
    """Get Fernet instance from ENCRYPTION_KEY env var."""
    if not HAS_CRYPTO:
        return None
    key = os.environ.get("ENCRYPTION_KEY")
    if not key:
        return None
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except Exception:
        return None


def encrypt_token(token: str) -> Optional[str]:
    f = _get_fernet()
    if f and token:
        return f.encrypt(token.encode()).decode()
    return token


def decrypt_token(encrypted: str) -> Optional[str]:
    f = _get_fernet()
    if f and encrypted:
        try:
            return f.decrypt(encrypted.encode()).decode()
        except Exception:
            return encrypted
    return encrypted


# ─────────────────────────────────────────────────────────
# Abstract email provider
# ─────────────────────────────────────────────────────────

class EmailProvider(ABC):
    """Abstract base class for email providers."""

    @abstractmethod
    def get_auth_url(self, redirect_uri: str) -> str:
        """Get OAuth authorization URL."""
        pass

    @abstractmethod
    def authenticate(self, auth_code: str, redirect_uri: str) -> Dict:
        """Exchange auth code for tokens."""
        pass

    @abstractmethod
    def refresh_access_token(self, refresh_token: str) -> Dict:
        """Refresh access token."""
        pass

    @abstractmethod
    def fetch_recent_emails(self, access_token: str, since: datetime,
                            max_results: int = 50) -> List[Dict]:
        """Fetch emails since timestamp."""
        pass


# ─────────────────────────────────────────────────────────
# Gmail provider
# ─────────────────────────────────────────────────────────

class GmailProvider(EmailProvider):
    """Gmail API implementation using google-auth-oauthlib."""

    SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

    def __init__(self):
        self.client_id = os.environ.get("GMAIL_CLIENT_ID", "")
        self.client_secret = os.environ.get("GMAIL_CLIENT_SECRET", "")

    @property
    def is_configured(self):
        return bool(self.client_id and self.client_secret and HAS_GMAIL)

    def get_auth_url(self, redirect_uri: str) -> str:
        if not self.is_configured:
            return ""
        flow = Flow.from_client_config(
            {
                "web": {
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                }
            },
            scopes=self.SCOPES,
            redirect_uri=redirect_uri,
        )
        auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline")
        return auth_url

    def authenticate(self, auth_code: str, redirect_uri: str) -> Dict:
        if not self.is_configured:
            return {}
        flow = Flow.from_client_config(
            {
                "web": {
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                }
            },
            scopes=self.SCOPES,
            redirect_uri=redirect_uri,
        )
        flow.fetch_token(code=auth_code)
        creds = flow.credentials
        return {
            "access_token": creds.token,
            "refresh_token": creds.refresh_token,
            "expiry": creds.expiry.isoformat() if creds.expiry else None,
        }

    def refresh_access_token(self, refresh_token: str) -> Dict:
        if not self.is_configured:
            return {}
        creds = Credentials(
            token=None,
            refresh_token=refresh_token,
            client_id=self.client_id,
            client_secret=self.client_secret,
            token_uri="https://oauth2.googleapis.com/token",
        )
        from google.auth.transport.requests import Request
        creds.refresh(Request())
        return {
            "access_token": creds.token,
            "expiry": creds.expiry.isoformat() if creds.expiry else None,
        }

    def fetch_recent_emails(self, access_token: str, since: datetime,
                            max_results: int = 50) -> List[Dict]:
        if not HAS_GMAIL:
            return []
        creds = Credentials(token=access_token)
        service = build("gmail", "v1", credentials=creds)

        since_str = since.strftime("%Y/%m/%d")
        query = f"after:{since_str} category:primary"

        try:
            results = service.users().messages().list(
                userId="me", q=query, maxResults=max_results
            ).execute()
        except Exception as e:
            logger.error(f"Gmail fetch error: {e}")
            return []

        messages = results.get("messages", [])
        emails = []
        for msg in messages:
            try:
                detail = service.users().messages().get(
                    userId="me", id=msg["id"], format="metadata",
                    metadataHeaders=["From", "Subject", "Date"],
                ).execute()

                headers = {h["name"]: h["value"] for h in detail.get("payload", {}).get("headers", [])}
                snippet = detail.get("snippet", "")

                emails.append({
                    "email_id": msg["id"],
                    "sender_email": _extract_email(headers.get("From", "")),
                    "sender_name": _extract_name(headers.get("From", "")),
                    "subject": headers.get("Subject", ""),
                    "body_preview": snippet[:500],
                    "received_date": headers.get("Date", ""),
                })
            except Exception as e:
                logger.warning(f"Failed to fetch message {msg['id']}: {e}")
                continue

        return emails


# ─────────────────────────────────────────────────────────
# Outlook provider
# ─────────────────────────────────────────────────────────

class OutlookProvider(EmailProvider):
    """Microsoft Graph API implementation."""

    SCOPES = ["Mail.Read", "Mail.ReadBasic"]

    def __init__(self):
        self.client_id = os.environ.get("OUTLOOK_CLIENT_ID", "")
        self.client_secret = os.environ.get("OUTLOOK_CLIENT_SECRET", "")
        self.authority = "https://login.microsoftonline.com/common"

    @property
    def is_configured(self):
        return bool(self.client_id and self.client_secret and HAS_OUTLOOK)

    def get_auth_url(self, redirect_uri: str) -> str:
        if not self.is_configured:
            return ""
        app = msal.ConfidentialClientApplication(
            self.client_id,
            authority=self.authority,
            client_credential=self.client_secret,
        )
        return app.get_authorization_request_url(
            self.SCOPES, redirect_uri=redirect_uri
        )

    def authenticate(self, auth_code: str, redirect_uri: str) -> Dict:
        if not self.is_configured:
            return {}
        app = msal.ConfidentialClientApplication(
            self.client_id,
            authority=self.authority,
            client_credential=self.client_secret,
        )
        result = app.acquire_token_by_authorization_code(
            auth_code, scopes=self.SCOPES, redirect_uri=redirect_uri,
        )
        if "error" in result:
            logger.error(f"Outlook auth error: {result}")
            return {}
        return {
            "access_token": result.get("access_token"),
            "refresh_token": result.get("refresh_token"),
            "expiry": None,
        }

    def refresh_access_token(self, refresh_token: str) -> Dict:
        if not self.is_configured:
            return {}
        app = msal.ConfidentialClientApplication(
            self.client_id,
            authority=self.authority,
            client_credential=self.client_secret,
        )
        result = app.acquire_token_by_refresh_token(refresh_token, scopes=self.SCOPES)
        if "error" in result:
            logger.error(f"Outlook refresh error: {result}")
            return {}
        return {
            "access_token": result.get("access_token"),
            "refresh_token": result.get("refresh_token", refresh_token),
        }

    def fetch_recent_emails(self, access_token: str, since: datetime,
                            max_results: int = 50) -> List[Dict]:
        if not HAS_OUTLOOK:
            return []
        since_str = since.strftime("%Y-%m-%dT%H:%M:%SZ")
        url = (
            f"https://graph.microsoft.com/v1.0/me/messages"
            f"?$filter=receivedDateTime ge {since_str}"
            f"&$top={max_results}"
            f"&$select=id,from,subject,bodyPreview,receivedDateTime"
            f"&$orderby=receivedDateTime desc"
        )
        headers = {"Authorization": f"Bearer {access_token}"}
        try:
            resp = http_requests.get(url, headers=headers, timeout=15)
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 60))
                logger.warning(f"Outlook rate limited. Retry after {retry_after}s")
                return []
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.error(f"Outlook fetch error: {e}")
            return []

        emails = []
        for msg in data.get("value", []):
            sender = msg.get("from", {}).get("emailAddress", {})
            emails.append({
                "email_id": msg.get("id", ""),
                "sender_email": sender.get("address", ""),
                "sender_name": sender.get("name", ""),
                "subject": msg.get("subject", ""),
                "body_preview": (msg.get("bodyPreview", ""))[:500],
                "received_date": msg.get("receivedDateTime", ""),
            })
        return emails


# ─────────────────────────────────────────────────────────
# Email classification via LLM
# ─────────────────────────────────────────────────────────

def classify_email_stage(subject: str, body: str, sender_domain: str) -> Dict:
    """
    Use LLM to classify an email and extract structured data.

    Returns:
        {
            'stage': str (one of VALID_EMAIL_STAGES),
            'confidence': float (0.0-1.0),
            'extracted_data': { interview_date, interviewer_names, salary, etc. }
        }
    """
    prompt = f"""Classify this email related to a job application.

Email subject: {subject}
Email body (preview): {body[:2000]}
Sender domain: {sender_domain}

Classification categories:
- application_received: confirmation that application was received
- screening: invitation for phone/recruiter screen
- interview_invite: invitation for technical/onsite interview
- interview_schedule: scheduling details for an interview
- rejection: rejection notification
- offer: job offer or compensation discussion
- other: not related to job application process

Look for patterns:
- "unfortunately" + "position" = rejection
- "next steps" + "interview" = interview invite
- "offer" + salary/numbers = offer
- "schedule" + date/time = interview scheduling
- "received your application" = application_received

Return ONLY valid JSON:
{{
    "stage": "one of the categories above",
    "confidence": 0.0 to 1.0,
    "extracted_data": {{
        "interview_date": "YYYY-MM-DD or null",
        "interview_time": "HH:MM or null",
        "interview_type": "phone/video/onsite or null",
        "interviewer_names": ["names if mentioned"],
        "salary_offered": null or integer,
        "response_deadline": "YYYY-MM-DD or null",
        "rejection_reason": "reason if mentioned or null"
    }}
}}"""

    try:
        raw = call_claude(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=1000,
        )
        return parse_json_response(raw)
    except Exception as e:
        logger.error(f"Email classification error: {e}")
        return {
            "stage": "other",
            "confidence": 0.0,
            "extracted_data": {},
        }


# ─────────────────────────────────────────────────────────
# Auto-matching emails to applications
# ─────────────────────────────────────────────────────────

def auto_match_email_to_application(
    email: Dict,
    applications: list,
) -> Tuple[Optional[Any], float]:
    """
    Match incoming email to existing application.

    Strategy:
    1. Company domain match (email from @google.com -> Google application)
    2. Fuzzy company name match in subject/body
    3. Returns (application, confidence_score)
    """
    sender_email = email.get("sender_email", "")
    subject = email.get("subject", "")
    body = email.get("body_preview", "")
    combined_text = f"{subject} {body}".lower()

    if not sender_email or not applications:
        return None, 0.0

    # Extract sender domain
    sender_domain = ""
    if "@" in sender_email:
        sender_domain = sender_email.split("@")[1].lower()
        # Strip common email service domains
        skip_domains = {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com",
                        "aol.com", "icloud.com", "protonmail.com"}
        if sender_domain in skip_domains:
            sender_domain = ""

    best_match = None
    best_score = 0.0

    for app in applications:
        score = 0.0
        company = (app.company or "").lower()

        if not company:
            continue

        # Strategy 1: Domain match
        if sender_domain:
            company_slug = company.replace(" ", "").replace(",", "").replace(".", "")
            domain_slug = sender_domain.split(".")[0]
            if domain_slug in company_slug or company_slug in domain_slug:
                score += 0.6

        # Strategy 2: Company name in subject/body
        if company in combined_text:
            score += 0.3

        # Strategy 3: Role title match
        role = (app.role or "").lower()
        if role and role in combined_text:
            score += 0.1

        if score > best_score:
            best_score = score
            best_match = app

    return best_match, min(best_score, 1.0)


def schedule_followup_from_email(stage: str, application_id: int) -> Optional[Dict]:
    """
    Determine what follow-up to create based on email classification.

    Returns follow-up dict or None if no action needed.
    """
    today = date.today()

    if stage == "interview_invite":
        return {
            "application_id": application_id,
            "action_type": "Thank You",
            "scheduled_date": today + timedelta(days=1),
            "notes": "Auto-created: Send thank-you after interview",
        }
    elif stage == "rejection":
        return {
            "application_id": application_id,
            "action_type": "Email Follow-up",
            "scheduled_date": today + timedelta(days=1),
            "notes": "Auto-created: Send gracious response to rejection",
        }
    elif stage == "offer":
        return {
            "application_id": application_id,
            "action_type": "Email Follow-up",
            "scheduled_date": today + timedelta(days=2),
            "notes": "Auto-created: Respond to offer / begin negotiation",
        }
    elif stage == "screening":
        return {
            "application_id": application_id,
            "action_type": "Email Follow-up",
            "scheduled_date": today + timedelta(days=5),
            "notes": "Auto-created: Follow up after screening if no response",
        }

    return None


# ─────────────────────────────────────────────────────────
# Email monitoring job (called by scheduler)
# ─────────────────────────────────────────────────────────

def email_monitoring_job(db_session_factory):
    """
    Main email monitoring loop. Fetches, classifies, and matches emails.

    Args:
        db_session_factory: callable that returns a new DB session
    """
    from tracker import (
        get_all_applications, get_setting, set_setting,
        create_email_tracking, create_follow_up, update_application,
        OAuthToken,
    )

    db = db_session_factory()
    try:
        # Get last run time
        last_run_str = get_setting(db, "email_last_run")
        if last_run_str:
            since = datetime.fromisoformat(last_run_str)
        else:
            since = datetime.utcnow() - timedelta(days=7)

        # Get OAuth tokens
        tokens = db.query(OAuthToken).all()
        if not tokens:
            logger.info("No email providers configured")
            return

        all_applications = get_all_applications(db)
        stats = {"processed": 0, "matched": 0, "followups_created": 0, "errors": 0}

        for token_record in tokens:
            provider = None
            if token_record.provider == "gmail":
                provider = GmailProvider()
            elif token_record.provider == "outlook":
                provider = OutlookProvider()

            if not provider or not provider.is_configured:
                continue

            # Decrypt tokens
            access_token = decrypt_token(token_record.access_token_encrypted)
            refresh_token = decrypt_token(token_record.refresh_token_encrypted)

            # Check if token needs refresh
            if token_record.token_expiry and token_record.token_expiry < datetime.utcnow():
                try:
                    refreshed = provider.refresh_access_token(refresh_token)
                    access_token = refreshed.get("access_token", access_token)
                    token_record.access_token_encrypted = encrypt_token(access_token)
                    if refreshed.get("refresh_token"):
                        token_record.refresh_token_encrypted = encrypt_token(refreshed["refresh_token"])
                    if refreshed.get("expiry"):
                        token_record.token_expiry = datetime.fromisoformat(refreshed["expiry"])
                    db.commit()
                except Exception as e:
                    logger.error(f"Token refresh failed for {token_record.provider}: {e}")
                    stats["errors"] += 1
                    continue

            # Fetch emails
            try:
                emails = provider.fetch_recent_emails(access_token, since)
            except Exception as e:
                logger.error(f"Email fetch failed for {token_record.provider}: {e}")
                stats["errors"] += 1
                continue

            for email_data in emails:
                try:
                    # Check if already processed
                    from tracker import EmailTracking
                    existing = db.query(
                        db.query(EmailTracking).filter_by(
                            email_id=email_data.get("email_id")
                        ).exists()
                    ).scalar()
                    if existing:
                        continue

                    # Classify email
                    sender_domain = ""
                    if "@" in email_data.get("sender_email", ""):
                        sender_domain = email_data["sender_email"].split("@")[1]

                    classification = classify_email_stage(
                        email_data.get("subject", ""),
                        email_data.get("body_preview", ""),
                        sender_domain,
                    )

                    # Auto-match to application
                    matched_app, confidence = auto_match_email_to_application(
                        email_data, all_applications
                    )

                    # Create tracking record
                    tracking = create_email_tracking(
                        db,
                        application_id=matched_app.id if matched_app and confidence >= 0.5 else None,
                        email_id=email_data.get("email_id"),
                        sender_email=email_data.get("sender_email"),
                        sender_name=email_data.get("sender_name"),
                        subject=email_data.get("subject"),
                        body_preview=email_data.get("body_preview"),
                        received_date=datetime.utcnow(),
                        detected_stage=classification.get("stage", "other"),
                        confidence_score=classification.get("confidence", 0.0),
                        auto_matched=confidence >= 0.5,
                        parsed_data=classification.get("extracted_data"),
                        processed=True,
                        user_confirmed=None if confidence < 0.7 else True,
                    )
                    stats["processed"] += 1

                    # Update application status if high confidence
                    if matched_app and confidence >= 0.7:
                        stage = classification.get("stage")
                        status_map = {
                            "screening": "Screening",
                            "interview_invite": "Interview",
                            "interview_schedule": "Interview",
                            "rejection": "Rejected",
                            "offer": "Offer",
                        }
                        new_status = status_map.get(stage)
                        if new_status:
                            auto_update = get_setting(db, "email_auto_update", "true")
                            if auto_update == "true":
                                update_application(db, matched_app.id, status=new_status)
                        stats["matched"] += 1

                        # Create follow-up if applicable
                        followup_data = schedule_followup_from_email(stage, matched_app.id)
                        if followup_data:
                            create_follow_up(db, **followup_data)
                            stats["followups_created"] += 1

                except Exception as e:
                    logger.error(f"Error processing email: {e}")
                    stats["errors"] += 1

        # Update last run timestamp
        set_setting(db, "email_last_run", datetime.utcnow().isoformat())

        logger.info(f"Email monitoring complete: {stats}")
        return stats

    except Exception as e:
        logger.error(f"Email monitoring job error: {e}")
        db.rollback()
        raise
    finally:
        db.close()


# ─────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────

def _extract_email(from_header: str) -> str:
    """Extract email address from 'Name <email>' format."""
    if "<" in from_header and ">" in from_header:
        return from_header.split("<")[1].split(">")[0].strip()
    return from_header.strip()


def _extract_name(from_header: str) -> str:
    """Extract name from 'Name <email>' format."""
    if "<" in from_header:
        return from_header.split("<")[0].strip().strip('"')
    return ""


def get_provider_status() -> Dict[str, Any]:
    """Check which email providers are available and configured."""
    gmail = GmailProvider()
    outlook = OutlookProvider()
    return {
        "gmail": {
            "library_installed": HAS_GMAIL,
            "configured": gmail.is_configured,
        },
        "outlook": {
            "library_installed": HAS_OUTLOOK,
            "configured": outlook.is_configured,
        },
        "encryption_available": HAS_CRYPTO,
    }
