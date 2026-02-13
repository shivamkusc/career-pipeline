"""
app.py — Flask + HTMX Web UI for Career Pipeline
Routes: upload form, background pipeline, SSE progress, results, PDF download
"""

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import os
import re
import uuid
import hashlib
import tempfile
import threading
import time
import json
from datetime import date, datetime

from flask import (
    Flask,
    render_template,
    request,
    Response,
    send_file,
    stream_with_context,
    jsonify,
    abort,
)

from ai_engine import (
    analyze_job_and_resume,
    tailor_resume,
    write_narratives,
    validate_no_hallucination,
    compute_ats_score,
    read_style_sample,
)
from pdf_builder import build_pdf, build_cover_letter_pdf
from recruiter_hunt import find_recruiters
from tracker import (
    init_db, get_db, VALID_STATUSES, VALID_FOLLOW_UP_TYPES, VALID_INTERVIEW_TYPES,
    create_application, get_application, get_all_applications,
    update_application, delete_application,
    create_follow_up, mark_follow_up_complete, delete_follow_up,
    create_interview, update_interview_outcome, delete_interview,
    create_document_sent,
    enrich_application,
    export_csv as tracker_export_csv,
    export_notion_markdown,
    get_analytics, get_calendar_data,
    # New: contacts, referrals, variants, email, settings
    create_contact, get_contact, get_all_contacts, update_contact, delete_contact,
    create_interaction, get_interactions_for_contact,
    create_referral, get_referral, update_referral,
    create_variant, get_variants_for_job, get_variant, update_variant,
    create_email_tracking, get_unconfirmed_matches,
    get_setting, set_setting,
    get_extended_analytics,
    OAuthToken, EmailTracking,
)
from followup_engine import generate_followup_message, suggest_followup_schedule
from network_manager import (
    import_linkedin_csv, suggest_outreach_targets, generate_coffee_chat_request,
    detect_network_gaps, track_referral_outcome,
)
from ab_testing import generate_variants, analyze_variant_performance, recommend_variant_for_job
from email_monitor import get_provider_status, GmailProvider, OutlookProvider, encrypt_token

app = Flask(__name__)

# In-memory job store — cleared on restart
jobs = {}

TEMP_DIR = os.path.join(tempfile.gettempdir(), "career_pipeline_jobs")
os.makedirs(TEMP_DIR, exist_ok=True)

# Initialize SQLite database for application tracker
init_db()

# Start background scheduler (if APScheduler is installed)
try:
    from scheduler import init_scheduler, get_job_status
    init_scheduler()
except ImportError:
    def get_job_status():
        return []


@app.context_processor
def inject_today():
    """Make today's date available in all templates."""
    return {"today_date": date.today()}

# ─────────────────────────────────────────────────────────
# Persistent upload storage
# ─────────────────────────────────────────────────────────

UPLOADS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")
UPLOADS_RESUMES_DIR = os.path.join(UPLOADS_DIR, "resumes")
UPLOADS_STYLES_DIR = os.path.join(UPLOADS_DIR, "styles")
MANIFEST_PATH = os.path.join(UPLOADS_DIR, "manifest.json")

os.makedirs(UPLOADS_RESUMES_DIR, exist_ok=True)
os.makedirs(UPLOADS_STYLES_DIR, exist_ok=True)


def _load_manifest():
    """Load the upload manifest from disk."""
    if os.path.exists(MANIFEST_PATH):
        with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"resumes": [], "styles": []}


def _save_manifest(manifest):
    """Write the upload manifest to disk."""
    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)


def _content_hash(data: bytes) -> str:
    """Return a short SHA-256 hex digest for dedup."""
    return hashlib.sha256(data).hexdigest()[:16]


def _save_upload(file_data: bytes, original_filename: str, category: str) -> dict:
    """Save an uploaded file to persistent storage. Returns the manifest entry.
    Deduplicates by content hash — if an identical file exists, returns it instead.
    """
    manifest = _load_manifest()
    chash = _content_hash(file_data)

    # Check for duplicate
    entries = manifest.get(category, [])
    for entry in entries:
        if entry.get("content_hash") == chash:
            # Update the timestamp so it sorts to top as "most recent"
            entry["saved_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            _save_manifest(manifest)
            return entry

    # Save new file
    file_id = str(uuid.uuid4())[:8]
    if category == "resumes":
        dest_dir = UPLOADS_RESUMES_DIR
    else:
        dest_dir = UPLOADS_STYLES_DIR

    # Preserve original extension
    _, ext = os.path.splitext(original_filename)
    stored_name = f"{file_id}{ext}"
    dest_path = os.path.join(dest_dir, stored_name)

    with open(dest_path, "wb") as f:
        f.write(file_data)

    entry = {
        "id": file_id,
        "original_filename": original_filename,
        "stored_name": stored_name,
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "size_bytes": len(file_data),
        "content_hash": chash,
    }

    entries.append(entry)
    manifest[category] = entries
    _save_manifest(manifest)
    return entry


def _get_upload_path(file_id: str, category: str):
    """Look up the on-disk path for a saved upload by its ID."""
    manifest = _load_manifest()
    for entry in manifest.get(category, []):
        if entry["id"] == file_id:
            if category == "resumes":
                return os.path.join(UPLOADS_RESUMES_DIR, entry["stored_name"])
            else:
                return os.path.join(UPLOADS_STYLES_DIR, entry["stored_name"])
    return None


def _delete_upload(file_id: str, category: str) -> bool:
    """Remove an upload from storage and manifest. Returns True if found."""
    manifest = _load_manifest()
    entries = manifest.get(category, [])
    for i, entry in enumerate(entries):
        if entry["id"] == file_id:
            # Delete the file
            if category == "resumes":
                fpath = os.path.join(UPLOADS_RESUMES_DIR, entry["stored_name"])
            else:
                fpath = os.path.join(UPLOADS_STYLES_DIR, entry["stored_name"])
            if os.path.exists(fpath):
                os.remove(fpath)
            entries.pop(i)
            manifest[category] = entries
            _save_manifest(manifest)
            return True
    return False


def _format_size(size_bytes: int) -> str:
    """Human-readable file size."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"


# ─────────────────────────────────────────────────────────
# Pipeline stages (run in background thread)
# ─────────────────────────────────────────────────────────

STAGES = ["analyze", "tailor", "write", "validate", "ats", "recruiters", "pdf"]


def _safe_filename(text):
    """Sanitize a string for use in filenames."""
    if not text:
        return ""
    return re.sub(r'[^\w\-]', '_', text).strip('_')[:30]


def run_pipeline_job(job_id, jd, resume_latex, style_paths):
    """Execute the full pipeline, updating job state at each stage."""
    job = jobs[job_id]

    try:
        # Stage 1: Analyze
        job["stage"] = "analyze"
        t0 = time.time()
        analysis = analyze_job_and_resume(jd, resume_latex)
        job["timings"]["analyze"] = round(time.time() - t0, 1)
        job["analysis"] = analysis
        job["completed_count"] = 1

        # Stage 2: Tailor
        job["stage"] = "tailor"
        t0 = time.time()
        tailored_latex = tailor_resume(resume_latex, analysis)
        job["timings"]["tailor"] = round(time.time() - t0, 1)
        job["tailored_latex"] = tailored_latex
        job["completed_count"] = 2

        # Stage 3: Write narratives
        job["stage"] = "write"
        t0 = time.time()
        style_voice = ""
        if style_paths:
            parts = []
            for sp in style_paths:
                if sp and os.path.exists(sp):
                    text = read_style_sample(sp)
                    if text.strip():
                        parts.append(text)
            style_voice = "\n\n---\n\n".join(parts)
        narratives = write_narratives(tailored_latex, analysis, style_voice)
        job["timings"]["write"] = round(time.time() - t0, 1)
        job["narratives"] = narratives
        job["completed_count"] = 3

        # Stage 4: Validate
        job["stage"] = "validate"
        t0 = time.time()
        validation = validate_no_hallucination(resume_latex, tailored_latex)
        job["timings"]["validate"] = round(time.time() - t0, 1)
        job["validation"] = validation
        job["completed_count"] = 4

        # Stage 5: ATS score
        job["stage"] = "ats"
        t0 = time.time()
        ats = compute_ats_score(jd, tailored_latex)
        job["timings"]["ats"] = round(time.time() - t0, 1)
        job["ats"] = ats
        job["completed_count"] = 5

        # Stage 6: Find recruiters
        job["stage"] = "recruiters"
        t0 = time.time()
        company = analysis.company_name
        location = analysis.location
        recruiters = []
        if company and company != "Unknown Company":
            try:
                recruiters = find_recruiters(company, location)
            except Exception:
                recruiters = []
        job["timings"]["recruiters"] = round(time.time() - t0, 1)
        job["recruiters"] = recruiters or []
        job["completed_count"] = 6

        # Stage 7: Build PDFs
        job["stage"] = "pdf"
        t0 = time.time()
        job_dir = os.path.join(TEMP_DIR, job_id)
        os.makedirs(job_dir, exist_ok=True)

        dark_mode = job.get("dark_mode_pdf", False)

        # Save resume .tex and compile
        resume_latex_src = tailored_latex
        if dark_mode:
            # Inject dark mode colors after \begin{document}
            resume_latex_src = tailored_latex.replace(
                r"\begin{document}",
                "\\usepackage{xcolor}\n\\begin{document}\n\\pagecolor[HTML]{1a1a2e}\\color[HTML]{e0e0e0}"
            )
        tex_path = os.path.join(job_dir, "resume.tex")
        with open(tex_path, "w", encoding="utf-8") as f:
            f.write(resume_latex_src)
        build_pdf(tex_path, job_dir)

        # Build cover letter PDF
        cover_letter = narratives.get("cover_letter", "")
        if cover_letter:
            build_cover_letter_pdf(cover_letter, job_dir, "cover_letter",
                                   dark_mode=dark_mode)

        job["timings"]["pdf"] = round(time.time() - t0, 1)
        job["completed_count"] = 7
        job["status"] = "done"
        job["stage"] = "done"

    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)
        job["stage"] = "error"


# ─────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────

@app.route("/")
def index():
    manifest = _load_manifest()
    # Sort by saved_at descending (most recent first)
    resumes = sorted(manifest.get("resumes", []),
                     key=lambda x: x.get("saved_at", ""), reverse=True)
    styles = sorted(manifest.get("styles", []),
                    key=lambda x: x.get("saved_at", ""), reverse=True)
    # Add human-readable sizes
    for entry in resumes + styles:
        entry["size_display"] = _format_size(entry.get("size_bytes", 0))
    return render_template("index.html", resumes=resumes, styles=styles)


@app.route("/run", methods=["POST"])
def run():
    """Accept uploads, start pipeline in background, return progress UI."""
    # Read JD from textarea or uploaded file
    jd = request.form.get("jd_text", "").strip()
    jd_file = request.files.get("jd_file")
    if not jd and jd_file and jd_file.filename:
        jd = jd_file.read().decode("utf-8", errors="replace")

    if not jd:
        return '<div class="notice" role="alert">Please provide a job description.</div>', 400

    # Read resume: from new upload OR from history
    resume_latex = None
    resume_id = request.form.get("resume_id", "").strip()
    resume_file = request.files.get("resume_file")

    if resume_file and resume_file.filename:
        # New upload — read and save to history
        file_data = resume_file.read()
        resume_latex = file_data.decode("utf-8", errors="replace")
        _save_upload(file_data, resume_file.filename, "resumes")
    elif resume_id:
        # Load from history
        path = _get_upload_path(resume_id, "resumes")
        if path and os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                resume_latex = f.read()

    if not resume_latex:
        return '<div class="notice" role="alert">Please upload a .tex resume file or select one from history.</div>', 400

    # Read style samples: from new uploads OR from history (supports multiple)
    style_paths = []
    style_ids = request.form.getlist("style_id")  # multiple hidden inputs
    style_files = request.files.getlist("style_files")  # multiple file input

    job_id = str(uuid.uuid4())[:8]
    job_dir = os.path.join(TEMP_DIR, job_id)

    # Handle new file uploads
    for sf in style_files:
        if sf and sf.filename:
            file_data = sf.read()
            _save_upload(file_data, sf.filename, "styles")
            os.makedirs(job_dir, exist_ok=True)
            _, ext = os.path.splitext(sf.filename)
            saved_name = f"style_{len(style_paths)}{ext}"
            saved_path = os.path.join(job_dir, saved_name)
            with open(saved_path, "wb") as f:
                f.write(file_data)
            style_paths.append(saved_path)

    # Handle history selections (multiple IDs)
    for sid in style_ids:
        sid = sid.strip()
        if sid:
            path = _get_upload_path(sid, "styles")
            if path and os.path.exists(path):
                style_paths.append(path)

    dark_mode_pdf = request.form.get("dark_mode_pdf") == "on"

    # Initialize job
    jobs[job_id] = {
        "status": "running",
        "stage": "starting",
        "analysis": None,
        "tailored_latex": None,
        "narratives": None,
        "validation": None,
        "ats": None,
        "recruiters": [],
        "error": None,
        "timings": {},
        "completed_count": 0,
        "dark_mode_pdf": dark_mode_pdf,
    }

    # Start background thread
    thread = threading.Thread(
        target=run_pipeline_job,
        args=(job_id, jd, resume_latex, style_paths),
        daemon=True,
    )
    thread.start()

    return render_template("partials/progress.html", job_id=job_id)


# ─────────────────────────────────────────────────────────
# Upload history API
# ─────────────────────────────────────────────────────────

@app.route("/api/uploads")
def api_uploads():
    """Return the upload manifest as JSON."""
    manifest = _load_manifest()
    return jsonify(manifest)


@app.route("/api/uploads/<category>/<file_id>", methods=["DELETE"])
def api_delete_upload(category, file_id):
    """Delete a saved upload from history."""
    if category not in ("resumes", "styles"):
        abort(400)
    if _delete_upload(file_id, category):
        return "", 200
    abort(404)


# ─────────────────────────────────────────────────────────
# SSE streaming
# ─────────────────────────────────────────────────────────

@app.route("/stream/<job_id>")
def stream(job_id):
    """SSE endpoint — streams stage completion events."""
    if job_id not in jobs:
        abort(404)

    def generate():
        seen_stages = set()
        stage_labels = {
            "analyze": "Analyzed job description & resume",
            "tailor": "Tailored resume to match JD",
            "write": "Generated cover letter & outreach",
            "validate": "Validated for hallucinations",
            "ats": "Computed ATS keyword score",
            "recruiters": "Searched for recruiters",
            "pdf": "Built PDF documents",
        }

        while True:
            job = jobs.get(job_id)
            if not job:
                break

            completed = job.get("completed_count", 0)
            total = len(STAGES)

            # Send events for completed stages
            for stage in STAGES:
                if stage not in seen_stages and _stage_completed(job, stage):
                    seen_stages.add(stage)
                    label = stage_labels.get(stage, stage)
                    elapsed = job.get("timings", {}).get(stage)
                    timing_str = f" ({elapsed}s)" if elapsed is not None else ""
                    html = f'<div class="stage-done">&#10003; {label}{timing_str}</div>'
                    yield f"event: {stage}\ndata: {html}\n\n"

            # Send progress count
            progress_html = f'{completed} / {total} stages complete'
            yield f"event: progress\ndata: {progress_html}\n\n"

            # Send progress bar value
            progress_bar_html = f'<progress value="{completed}" max="{total}"></progress>'
            yield f"event: progressbar\ndata: {progress_bar_html}\n\n"

            if job["status"] == "done":
                analysis = job.get("analysis")
                summary = ""
                if analysis:
                    summary = f"{analysis.company_name} &mdash; {analysis.role_title}"
                html = f'<div id="results-trigger" hx-get="/results/{job_id}" hx-trigger="load" hx-target="#results-area" hx-swap="innerHTML">{summary}</div>'
                yield f"event: done\ndata: {html}\n\n"
                break

            if job["status"] == "error":
                err = job.get("error", "Unknown error")
                html = f'<div class="notice error" role="alert">Pipeline error: {err}</div><a href="/" role="button" class="secondary outline">Run Again</a>'
                yield f"event: done\ndata: {html}\n\n"
                break

            time.sleep(1)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


def _stage_completed(job, stage):
    """Check if a given stage has finished based on job state."""
    stage_keys = {
        "analyze": "analysis",
        "tailor": "tailored_latex",
        "write": "narratives",
        "validate": "validation",
        "ats": "ats",
        "recruiters": "recruiters",
    }
    if stage == "pdf":
        return job["status"] == "done"
    key = stage_keys.get(stage)
    if key:
        return job.get(key) is not None
    return False


# ─────────────────────────────────────────────────────────
# Results & tabs
# ─────────────────────────────────────────────────────────

@app.route("/results/<job_id>")
def results(job_id):
    """Return full results page with tabs."""
    job = jobs.get(job_id)
    if not job:
        abort(404)
    if job["status"] != "done":
        return '<div class="notice">Pipeline still running...</div>'

    analysis = job["analysis"]
    ats = job.get("ats", {})
    validation = job.get("validation", {})
    narratives = job.get("narratives", {})
    recruiters = job.get("recruiters", [])
    tailored_latex = job.get("tailored_latex", "")
    timings = job.get("timings", {})

    # Check if PDFs exist
    job_dir = os.path.join(TEMP_DIR, job_id)
    has_resume_pdf = os.path.exists(os.path.join(job_dir, "resume.pdf"))
    has_cover_pdf = os.path.exists(os.path.join(job_dir, "cover_letter.pdf"))

    return render_template(
        "partials/results.html",
        job_id=job_id,
        analysis=analysis,
        ats=ats,
        validation=validation,
        narratives=narratives,
        recruiters=recruiters,
        tailored_latex=tailored_latex,
        has_resume_pdf=has_resume_pdf,
        has_cover_pdf=has_cover_pdf,
        timings=timings,
    )


@app.route("/tab/<job_id>/<tab_name>")
def tab(job_id, tab_name):
    """Serve individual tab partials for HTMX tab switching."""
    job = jobs.get(job_id)
    if not job or job["status"] != "done":
        abort(404)

    analysis = job["analysis"]
    ats = job.get("ats", {})
    validation = job.get("validation", {})
    narratives = job.get("narratives", {})
    recruiters = job.get("recruiters", [])
    tailored_latex = job.get("tailored_latex", "")
    timings = job.get("timings", {})

    job_dir = os.path.join(TEMP_DIR, job_id)
    has_resume_pdf = os.path.exists(os.path.join(job_dir, "resume.pdf"))
    has_cover_pdf = os.path.exists(os.path.join(job_dir, "cover_letter.pdf"))

    template_map = {
        "resume": "partials/resume.html",
        "cover": "partials/cover.html",
        "outreach": "partials/outreach.html",
        "recruiters": "partials/recruiters.html",
        "quality": "partials/quality.html",
        "analysis": "partials/analysis.html",
    }

    template = template_map.get(tab_name)
    if not template:
        abort(404)

    return render_template(
        template,
        job_id=job_id,
        analysis=analysis,
        ats=ats,
        validation=validation,
        narratives=narratives,
        recruiters=recruiters,
        tailored_latex=tailored_latex,
        has_resume_pdf=has_resume_pdf,
        has_cover_pdf=has_cover_pdf,
        timings=timings,
    )


# ─────────────────────────────────────────────────────────
# File download / preview
# ─────────────────────────────────────────────────────────

@app.route("/download/<job_id>/<file_type>")
def download(job_id, file_type):
    """Serve generated PDF files with descriptive filenames."""
    if job_id not in jobs:
        abort(404)

    job = jobs[job_id]
    job_dir = os.path.join(TEMP_DIR, job_id)
    file_map = {
        "resume": "resume.pdf",
        "cover_letter": "cover_letter.pdf",
    }

    filename = file_map.get(file_type)
    if not filename:
        abort(404)

    file_path = os.path.join(job_dir, filename)
    if not os.path.exists(file_path):
        abort(404)

    # Build descriptive download name from analysis
    analysis = job.get("analysis")
    if analysis:
        company = _safe_filename(analysis.company_name)
        role = _safe_filename(analysis.role_title)
        if company and role:
            if file_type == "resume":
                download_name = f"Resume_{company}_{role}.pdf"
            else:
                download_name = f"CoverLetter_{company}_{role}.pdf"
        else:
            download_name = filename
    else:
        download_name = filename

    return send_file(
        file_path,
        as_attachment=True,
        download_name=download_name,
    )


@app.route("/preview/<job_id>/<file_type>")
def preview(job_id, file_type):
    """Serve PDF files inline for iframe preview."""
    if job_id not in jobs:
        abort(404)

    job_dir = os.path.join(TEMP_DIR, job_id)
    file_map = {
        "resume": "resume.pdf",
        "cover_letter": "cover_letter.pdf",
    }

    filename = file_map.get(file_type)
    if not filename:
        abort(404)

    file_path = os.path.join(job_dir, filename)
    if not os.path.exists(file_path):
        abort(404)

    return send_file(file_path, mimetype="application/pdf")


# ─────────────────────────────────────────────────────────
# Application Tracker routes
# ─────────────────────────────────────────────────────────

@app.route("/tracker")
def tracker_dashboard():
    """Main tracker dashboard with kanban board."""
    db = get_db()
    try:
        apps = get_all_applications(db)
        columns = {s: [] for s in VALID_STATUSES}
        for a in apps:
            enriched = enrich_application(a)
            if a.status in columns:
                columns[a.status].append(enriched)
        return render_template("tracker/dashboard.html",
                               columns=columns, statuses=VALID_STATUSES,
                               today=date.today().isoformat())
    finally:
        db.close()


@app.route("/tracker/add", methods=["POST"])
def tracker_add():
    """Create a new application from form data."""
    db = get_db()
    try:
        date_str = request.form.get("date_applied", "").strip()
        applied_date = date.fromisoformat(date_str) if date_str else date.today()

        create_application(
            db,
            company=request.form.get("company", "").strip(),
            role=request.form.get("role", "").strip(),
            date_applied=applied_date,
            status=request.form.get("status", "Applied"),
            salary_range=request.form.get("salary_range", "").strip() or None,
            job_posting_url=request.form.get("job_posting_url", "").strip() or None,
            application_method=request.form.get("application_method", "").strip() or None,
            notes=request.form.get("notes", "").strip() or None,
        )
        # Redirect back to dashboard (full page refresh)
        return Response(status=204, headers={"HX-Redirect": "/tracker"})
    finally:
        db.close()


@app.route("/tracker/add/<job_id>", methods=["POST"])
def tracker_add_from_pipeline(job_id):
    """Quick-add from pipeline results. Auto-populates from JobAnalysis."""
    job = jobs.get(job_id)
    if not job or job["status"] != "done":
        return '<div class="notice error">Pipeline job not found or not complete.</div>', 404

    analysis = job["analysis"]
    ats = job.get("ats", {})

    db = get_db()
    try:
        app_record = create_application(
            db,
            company=analysis.company_name,
            role=analysis.role_title,
            date_applied=date.today(),
            status="Applied",
            job_id=job_id,
            ats_score=ats.get("score"),
            notes=request.form.get("notes", "").strip() or None,
            job_posting_url=request.form.get("job_posting_url", "").strip() or None,
            application_method=request.form.get("application_method", "").strip() or None,
        )

        # Link documents
        job_dir = os.path.join(TEMP_DIR, job_id)
        resume_path = os.path.join(job_dir, "resume.pdf")
        cover_path = os.path.join(job_dir, "cover_letter.pdf")
        if os.path.exists(resume_path):
            create_document_sent(db, application_id=app_record.id,
                                 document_type="Resume", file_path=resume_path)
        if os.path.exists(cover_path):
            create_document_sent(db, application_id=app_record.id,
                                 document_type="Cover Letter", file_path=cover_path)

        return render_template("partials/add_to_tracker.html",
                               success=True, app_id=app_record.id,
                               company=analysis.company_name, role=analysis.role_title)
    finally:
        db.close()


@app.route("/tracker/application/<int:app_id>")
def tracker_detail(app_id):
    """Detailed view of a single application."""
    db = get_db()
    try:
        app_record = get_application(db, app_id)
        if not app_record:
            abort(404)
        enriched = enrich_application(app_record)
        pipeline_data = None
        if app_record.job_id and app_record.job_id in jobs:
            pipeline_data = jobs[app_record.job_id]
        return render_template("tracker/detail.html",
                               **enriched, pipeline_data=pipeline_data,
                               statuses=VALID_STATUSES,
                               follow_up_types=VALID_FOLLOW_UP_TYPES,
                               interview_types=VALID_INTERVIEW_TYPES)
    finally:
        db.close()


@app.route("/api/tracker/application/<int:app_id>", methods=["PATCH"])
def tracker_update(app_id):
    """Update an application (status, notes, etc)."""
    db = get_db()
    try:
        data = request.get_json(silent=True) or {}
        if not data:
            data = request.form.to_dict()
        # Filter out empty strings
        data = {k: v for k, v in data.items() if v != ""}
        updated = update_application(db, app_id, **data)
        if not updated:
            abort(404)
        return "", 204
    finally:
        db.close()


@app.route("/api/tracker/application/<int:app_id>", methods=["DELETE"])
def tracker_delete(app_id):
    """Delete an application."""
    db = get_db()
    try:
        if delete_application(db, app_id):
            return "", 200
        abort(404)
    finally:
        db.close()


@app.route("/tracker/calendar")
def tracker_calendar():
    """Calendar view showing follow-ups and interviews."""
    db = get_db()
    try:
        now = date.today()
        month = request.args.get("month", now.month, type=int)
        year = request.args.get("year", now.year, type=int)
        cal_data = get_calendar_data(db, year, month)
        return render_template("tracker/calendar.html", **cal_data)
    finally:
        db.close()


@app.route("/tracker/analytics")
def tracker_analytics():
    """Analytics dashboard with charts (extended with network + variant data)."""
    db = get_db()
    try:
        data = get_extended_analytics(db)
        return render_template("tracker/analytics.html", analytics=data)
    finally:
        db.close()


@app.route("/api/tracker/follow-up", methods=["POST"])
def tracker_add_follow_up():
    """Add a follow-up to an application."""
    db = get_db()
    try:
        app_id = int(request.form.get("application_id"))
        create_follow_up(
            db,
            application_id=app_id,
            scheduled_date=date.fromisoformat(request.form.get("scheduled_date")),
            action_type=request.form.get("action_type", "Email Follow-up"),
            notes=request.form.get("notes", "").strip() or None,
        )
        app_record = get_application(db, app_id)
        return render_template("partials/tracker_followups.html", app=app_record)
    finally:
        db.close()


@app.route("/api/tracker/follow-up/<int:fu_id>", methods=["PATCH"])
def tracker_complete_follow_up(fu_id):
    """Mark a follow-up as complete."""
    db = get_db()
    try:
        fu = mark_follow_up_complete(db, fu_id)
        if not fu:
            abort(404)
        app_record = get_application(db, fu.application_id)
        return render_template("partials/tracker_followups.html", app=app_record)
    finally:
        db.close()


@app.route("/api/tracker/follow-up/<int:fu_id>", methods=["DELETE"])
def tracker_delete_follow_up(fu_id):
    """Delete a follow-up."""
    db = get_db()
    try:
        if delete_follow_up(db, fu_id):
            return "", 200
        abort(404)
    finally:
        db.close()


@app.route("/api/tracker/interview", methods=["POST"])
def tracker_add_interview():
    """Add an interview to an application."""
    db = get_db()
    try:
        app_id = int(request.form.get("application_id"))
        create_interview(
            db,
            application_id=app_id,
            date_time=datetime.fromisoformat(request.form.get("date_time")),
            interview_type=request.form.get("interview_type", "Phone"),
            interviewer_names=request.form.get("interviewer_names", "").strip() or None,
            prep_notes=request.form.get("prep_notes", "").strip() or None,
        )
        app_record = get_application(db, app_id)
        return render_template("partials/tracker_interviews.html", app=app_record)
    finally:
        db.close()


@app.route("/api/tracker/interview/<int:iv_id>", methods=["PATCH"])
def tracker_update_interview(iv_id):
    """Update interview outcome."""
    db = get_db()
    try:
        outcome = request.form.get("outcome", "")
        iv = update_interview_outcome(db, iv_id, outcome)
        if not iv:
            abort(404)
        app_record = get_application(db, iv.application_id)
        return render_template("partials/tracker_interviews.html", app=app_record)
    finally:
        db.close()


@app.route("/api/tracker/interview/<int:iv_id>", methods=["DELETE"])
def tracker_delete_interview(iv_id):
    """Delete an interview."""
    db = get_db()
    try:
        if delete_interview(db, iv_id):
            return "", 200
        abort(404)
    finally:
        db.close()


@app.route("/api/tracker/export/csv")
def tracker_csv_export():
    """Export all applications as CSV download."""
    db = get_db()
    try:
        csv_content = tracker_export_csv(db)
        return Response(
            csv_content,
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=applications.csv"},
        )
    finally:
        db.close()


@app.route("/api/tracker/export/notion")
def tracker_notion_export():
    """Export as Notion-formatted markdown."""
    db = get_db()
    try:
        md = export_notion_markdown(db)
        return Response(md, mimetype="text/plain",
                        headers={"Content-Disposition": "attachment; filename=applications_notion.md"})
    finally:
        db.close()


# ─────────────────────────────────────────────────────────
# Quick Regenerate routes
# ─────────────────────────────────────────────────────────

@app.route("/regenerate/<job_id>/cover_letter", methods=["POST"])
def regenerate_cover_letter(job_id):
    """Re-generate cover letter using stored pipeline data."""
    job = jobs.get(job_id)
    if not job or job["status"] != "done":
        return '<div class="notice error">Job not found or not complete.</div>', 404

    tailored_latex = job.get("tailored_latex", "")
    analysis = job.get("analysis")
    if not analysis:
        return '<div class="notice error">No analysis data available.</div>', 400

    style_voice = ""
    narratives = write_narratives(tailored_latex, analysis, style_voice)
    job["narratives"] = narratives

    job_dir = os.path.join(TEMP_DIR, job_id)
    cover_letter = narratives.get("cover_letter", "")
    if cover_letter:
        build_cover_letter_pdf(cover_letter, job_dir, "cover_letter")

    has_cover_pdf = os.path.exists(os.path.join(job_dir, "cover_letter.pdf"))

    return render_template(
        "partials/cover.html",
        job_id=job_id,
        narratives=narratives,
        has_cover_pdf=has_cover_pdf,
    )


# ─────────────────────────────────────────────────────────
# Network routes
# ─────────────────────────────────────────────────────────

@app.route("/network")
def network_dashboard():
    """Network relationship manager dashboard."""
    db = get_db()
    try:
        search = request.args.get("search", "").strip()
        strength = request.args.get("strength", "").strip()
        tag = request.args.get("tag", "").strip()

        contacts = get_all_contacts(db, search=search or None,
                                    strength=strength or None,
                                    tag=tag or None)

        strength_counts = {"close": 0, "warm": 0, "cold": 0}
        all_contacts = get_all_contacts(db)
        for c in all_contacts:
            s = c.relationship_strength or "cold"
            if s in strength_counts:
                strength_counts[s] += 1

        suggestions = suggest_outreach_targets(db, limit=5)
        gaps = detect_network_gaps(db)

        return render_template(
            "network/dashboard.html",
            contacts=contacts,
            total_contacts=len(all_contacts),
            strength_counts=strength_counts,
            suggestions=suggestions,
            gaps=gaps,
        )
    finally:
        db.close()


@app.route("/network/import", methods=["POST"])
def network_import():
    """Import LinkedIn CSV connections."""
    csv_file = request.files.get("csv_file")
    if not csv_file or not csv_file.filename:
        return '<div class="notice error">No CSV file provided.</div>', 400

    csv_content = csv_file.read().decode("utf-8", errors="replace")
    db = get_db()
    try:
        result = import_linkedin_csv(csv_content, db)
        return (
            f'<div class="notice">'
            f'Imported: {result["imported"]}, Updated: {result["updated"]}, '
            f'Skipped: {result["skipped"]}'
            f'{"".join("<br>Error: " + e for e in result.get("errors", []))}'
            f'</div>'
        )
    finally:
        db.close()


@app.route("/network/contact/<int:contact_id>")
def network_contact_detail(contact_id):
    """Contact detail page."""
    db = get_db()
    try:
        contact = get_contact(db, contact_id)
        if not contact:
            abort(404)
        interactions = get_interactions_for_contact(db, contact_id)
        referrals = contact.referrals
        return render_template(
            "network/contact_detail.html",
            contact=contact,
            interactions=interactions,
            referrals=referrals,
        )
    finally:
        db.close()


@app.route("/api/network/contacts", methods=["POST"])
def api_create_contact():
    """Create a new contact."""
    db = get_db()
    try:
        data = request.get_json(silent=True) or request.form
        create_contact(
            db,
            name=(data.get("name", "") or "").strip(),
            email=(data.get("email", "") or "").strip() or None,
            company=(data.get("company", "") or "").strip() or None,
            title=(data.get("title", "") or "").strip() or None,
            linkedin_url=(data.get("linkedin_url", "") or "").strip() or None,
            relationship_strength=data.get("relationship_strength", "warm"),
            source=data.get("source", "manual"),
            tags=(data.get("tags", "") or "").strip() or None,
            notes=(data.get("notes", "") or "").strip() or None,
        )
        return "", 200
    finally:
        db.close()


@app.route("/api/network/contacts/<int:contact_id>", methods=["PUT"])
def api_update_contact(contact_id):
    """Update a contact."""
    db = get_db()
    try:
        data = request.get_json(silent=True) or request.form
        updates = {}
        for field in ["name", "email", "company", "title", "linkedin_url",
                       "relationship_strength", "tags", "notes"]:
            val = data.get(field)
            if val is not None:
                updates[field] = val.strip() if isinstance(val, str) else val
        freq = data.get("contact_frequency_days")
        if freq:
            updates["contact_frequency_days"] = int(freq)
        update_contact(db, contact_id, **updates)
        return "", 200
    finally:
        db.close()


@app.route("/api/network/contacts/<int:contact_id>", methods=["DELETE"])
def api_delete_contact(contact_id):
    """Delete a contact."""
    db = get_db()
    try:
        if delete_contact(db, contact_id):
            return "", 200
        abort(404)
    finally:
        db.close()


@app.route("/api/network/interaction", methods=["POST"])
def api_log_interaction():
    """Log a contact interaction."""
    db = get_db()
    try:
        data = request.get_json(silent=True) or request.form
        contact_id = int(data.get("contact_id"))
        create_interaction(
            db,
            contact_id=contact_id,
            interaction_date=date.fromisoformat(data.get("interaction_date", date.today().isoformat())),
            interaction_type=data.get("interaction_type", "email"),
            notes=(data.get("notes", "") or "").strip() or None,
        )
        interactions = get_interactions_for_contact(db, contact_id)
        contact = get_contact(db, contact_id)
        return render_template(
            "network/contact_detail.html",
            contact=contact,
            interactions=interactions,
            referrals=contact.referrals,
        )
    finally:
        db.close()


@app.route("/api/network/coffee-chat/<int:contact_id>", methods=["POST"])
def api_coffee_chat(contact_id):
    """Generate coffee chat request for a contact."""
    db = get_db()
    try:
        contact = get_contact(db, contact_id)
        if not contact:
            abort(404)

        # Get user background from most recent resume
        background = "Experienced professional"  # fallback
        manifest = _load_manifest()
        resumes = manifest.get("resumes", [])
        if resumes:
            latest = resumes[-1]
            path = os.path.join(UPLOADS_RESUMES_DIR, latest["stored_name"])
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    background = f.read()[:2000]

        chat = generate_coffee_chat_request(
            contact_name=contact.name,
            contact_company=contact.company,
            contact_title=contact.title,
            user_background=background,
            relationship=contact.relationship_strength,
        )

        return render_template("partials/coffee_chat.html", chat=chat)
    finally:
        db.close()


@app.route("/api/network/referral/new", methods=["POST"])
def api_create_referral():
    """Create a referral tracking record."""
    db = get_db()
    try:
        create_referral(
            db,
            application_id=int(request.form.get("application_id")),
            contact_id=int(request.form.get("contact_id")),
            referral_method=request.form.get("referral_method", "direct_intro"),
            notes=request.form.get("notes", "").strip() or None,
        )
        return '<div class="notice">Referral tracked successfully.</div>'
    finally:
        db.close()


@app.route("/api/network/referral/<int:referral_id>", methods=["PATCH"])
def api_update_referral(referral_id):
    """Update referral outcome."""
    db = get_db()
    try:
        outcome = request.form.get("outcome", "pending")
        result = track_referral_outcome(db, referral_id, outcome)
        if not result:
            abort(404)
        return '<div class="notice">Referral updated. ' + '; '.join(result.get("actions", [])) + '</div>'
    finally:
        db.close()


# ─────────────────────────────────────────────────────────
# Follow-up generation routes
# ─────────────────────────────────────────────────────────

@app.route("/api/followup/generate", methods=["POST"])
def api_generate_followup():
    """Generate an AI-powered follow-up message."""
    db = get_db()
    try:
        app_id = request.form.get("application_id")
        followup_type = request.form.get("followup_type", "initial_check_in")

        context = {}
        if app_id:
            app_record = get_application(db, int(app_id))
            if app_record:
                context.update({
                    "company": app_record.company,
                    "role": app_record.role,
                    "days_since_applied": (date.today() - app_record.date_applied).days if app_record.date_applied else 0,
                    "status": app_record.status,
                })

        # Additional context from form
        for key in ["interviewer_name", "interview_notes", "offer_amount",
                     "market_rate", "rejection_reason", "custom_instructions",
                     "contact_name", "contact_company", "contact_title"]:
            val = request.form.get(key)
            if val:
                if key in ("offer_amount", "market_rate"):
                    try:
                        context[key] = int(val)
                    except ValueError:
                        pass
                else:
                    context[key] = val

        followup = generate_followup_message(followup_type, context)
        return render_template("partials/followup_generated.html", followup=followup)
    finally:
        db.close()


@app.route("/api/followup/suggest/<int:app_id>")
def api_suggest_followups(app_id):
    """Get suggested follow-up schedule for an application."""
    db = get_db()
    try:
        app_record = get_application(db, app_id)
        if not app_record:
            abort(404)
        suggestions = suggest_followup_schedule(
            status=app_record.status,
            application_method=app_record.application_method,
            days_since_applied=(date.today() - app_record.date_applied).days if app_record.date_applied else 0,
        )
        return jsonify(suggestions)
    finally:
        db.close()


# ─────────────────────────────────────────────────────────
# A/B Testing routes
# ─────────────────────────────────────────────────────────

@app.route("/results/<job_id>/variants", methods=["POST"])
def generate_job_variants(job_id):
    """Generate A/B test variants for a pipeline job."""
    job = jobs.get(job_id)
    if not job or job["status"] != "done":
        return '<div class="notice error">Job not found or not complete.</div>', 404

    analysis = job.get("analysis")
    tailored_latex = job.get("tailored_latex", "")
    if not analysis:
        return '<div class="notice error">No analysis data available.</div>', 400

    # Convert analysis dataclass to dict
    analysis_dict = {
        "company_name": analysis.company_name,
        "role_title": analysis.role_title,
        "hard_skills": analysis.hard_skills,
        "key_responsibilities": analysis.key_responsibilities,
        "my_differentiators": analysis.my_differentiators,
        "research_notes": analysis.research_notes,
    }

    variants = generate_variants(analysis_dict, tailored_latex)

    # Save variants to database
    db = get_db()
    try:
        for v in variants:
            variant_record = create_variant(
                db,
                job_id=job_id,
                variant_name=v["name"],
                variant_description=v.get("description", ""),
                cover_letter_text=v.get("cover_letter", ""),
                cold_email_text=v.get("cold_email", ""),
                linkedin_message_text=v.get("linkedin_message", ""),
                strategy_prompt=v.get("strategy_prompt", ""),
            )
            v["db_id"] = variant_record.id
            v["used"] = False
            v["display_name"] = v.get("display_name", v["name"])

        # Check for recommendation
        recommendation = recommend_variant_for_job(db, analysis_dict)
    finally:
        db.close()

    return render_template("partials/variants.html",
                           variants=variants,
                           recommendation=recommendation)


@app.route("/api/variants/<int:variant_id>/mark_used", methods=["POST"])
def api_mark_variant_used(variant_id):
    """Mark a variant as the one that was actually sent."""
    db = get_db()
    try:
        update_variant(db, variant_id, used=True)
        return '<span class="badge badge-green">Marked as Used</span>'
    finally:
        db.close()


@app.route("/api/variants/<int:variant_id>/outcome", methods=["POST"])
def api_variant_outcome(variant_id):
    """Record outcome for a variant."""
    db = get_db()
    try:
        outcome = request.form.get("outcome", "no_response")
        response_hours = request.form.get("response_time_hours")
        updates = {"outcome": outcome, "response_received": outcome != "no_response"}
        if response_hours:
            updates["response_time_hours"] = int(response_hours)
        update_variant(db, variant_id, **updates)
        return '<div class="notice">Outcome recorded.</div>'
    finally:
        db.close()


@app.route("/analytics/variants")
def variant_analytics():
    """A/B variant performance analytics page."""
    db = get_db()
    try:
        analysis = analyze_variant_performance(db)
        return render_template("tracker/analytics.html",
                               analytics={"variant_analysis": analysis,
                                           **get_extended_analytics(db)})
    finally:
        db.close()


# ─────────────────────────────────────────────────────────
# Settings routes
# ─────────────────────────────────────────────────────────

@app.route("/settings")
def settings_page():
    """Application settings page."""
    db = get_db()
    try:
        settings = {}
        for key in ["email_check_interval", "email_auto_update",
                     "default_checkin_days", "default_thankyou_hours", "followup_tone",
                     "network_warm_decay_days", "network_close_decay_days",
                     "auto_generate_variants", "default_variant_count",
                     "show_variant_recommendation"]:
            settings[key] = get_setting(db, key)

        providers = get_provider_status()

        # Check OAuth connections
        gmail_connected = False
        gmail_email = ""
        outlook_connected = False
        outlook_email = ""
        gmail_token = db.query(OAuthToken).filter_by(provider="gmail").first()
        if gmail_token:
            gmail_connected = True
            gmail_email = gmail_token.email_address or "Connected"
        outlook_token = db.query(OAuthToken).filter_by(provider="outlook").first()
        if outlook_token:
            outlook_connected = True
            outlook_email = outlook_token.email_address or "Connected"

        scheduler_jobs = get_job_status()

        return render_template(
            "settings/settings.html",
            settings=settings,
            providers=providers,
            gmail_connected=gmail_connected,
            gmail_email=gmail_email,
            outlook_connected=outlook_connected,
            outlook_email=outlook_email,
            scheduler_jobs=scheduler_jobs,
        )
    finally:
        db.close()


@app.route("/settings/save", methods=["POST"])
def settings_save():
    """Save settings from form."""
    db = get_db()
    try:
        for key in request.form:
            value = request.form.get(key, "")
            if key.endswith("_switch") or request.form.get(key) == "on":
                value = "true"
            set_setting(db, key, value)

        # Handle checkboxes that aren't sent when unchecked
        for cb in ["email_auto_update", "auto_generate_variants", "show_variant_recommendation"]:
            if cb not in request.form:
                set_setting(db, cb, "false")

        return "", 200
    finally:
        db.close()


# ─────────────────────────────────────────────────────────
# OAuth routes
# ─────────────────────────────────────────────────────────

@app.route("/oauth/start/<provider>")
def oauth_start(provider):
    """Start OAuth flow for email provider."""
    base_url = request.host_url.rstrip("/")
    redirect_uri = f"{base_url}/oauth/callback/{provider}"

    if provider == "gmail":
        p = GmailProvider()
    elif provider == "outlook":
        p = OutlookProvider()
    else:
        abort(400)

    if not p.is_configured:
        return '<div class="notice error">Provider not configured. Check environment variables.</div>'

    auth_url = p.get_auth_url(redirect_uri)
    if not auth_url:
        return '<div class="notice error">Failed to generate auth URL.</div>'

    return f'<script>window.location.href="{auth_url}";</script>'


@app.route("/oauth/callback/<provider>")
def oauth_callback(provider):
    """Handle OAuth callback."""
    code = request.args.get("code")
    if not code:
        return '<div class="notice error">No authorization code received.</div>'

    base_url = request.host_url.rstrip("/")
    redirect_uri = f"{base_url}/oauth/callback/{provider}"

    if provider == "gmail":
        p = GmailProvider()
    elif provider == "outlook":
        p = OutlookProvider()
    else:
        abort(400)

    tokens = p.authenticate(code, redirect_uri)
    if not tokens:
        return '<div class="notice error">Authentication failed.</div>'

    db = get_db()
    try:
        # Remove existing token for this provider
        existing = db.query(OAuthToken).filter_by(provider=provider).first()
        if existing:
            db.delete(existing)
            db.commit()

        # Save new token
        oauth = OAuthToken(
            provider=provider,
            access_token_encrypted=encrypt_token(tokens.get("access_token", "")),
            refresh_token_encrypted=encrypt_token(tokens.get("refresh_token", "")),
            token_expiry=datetime.fromisoformat(tokens["expiry"]) if tokens.get("expiry") else None,
        )
        db.add(oauth)
        db.commit()
    finally:
        db.close()

    return '<script>window.location.href="/settings";</script>'


@app.route("/settings/email/disconnect/<provider>", methods=["POST"])
def email_disconnect(provider):
    """Disconnect email provider."""
    db = get_db()
    try:
        token = db.query(OAuthToken).filter_by(provider=provider).first()
        if token:
            db.delete(token)
            db.commit()
        return "", 200
    finally:
        db.close()


# ─────────────────────────────────────────────────────────
# Email review routes
# ─────────────────────────────────────────────────────────

@app.route("/api/email/review")
def email_review():
    """Get unconfirmed email matches for review."""
    db = get_db()
    try:
        unconfirmed = get_unconfirmed_matches(db)
        items = []
        for em in unconfirmed:
            items.append({
                "id": em.id,
                "subject": em.subject,
                "sender": em.sender_name or em.sender_email,
                "date": em.received_date.isoformat() if em.received_date else "",
                "stage": em.detected_stage,
                "confidence": f"{em.confidence_score:.0%}" if em.confidence_score else "N/A",
                "matched_app": em.application.company if em.application else "Unmatched",
            })
        return jsonify(items)
    finally:
        db.close()


@app.route("/api/email/confirm/<int:tracking_id>", methods=["POST"])
def email_confirm_match(tracking_id):
    """Confirm auto-matched email."""
    db = get_db()
    try:
        em = db.query(EmailTracking).filter_by(id=tracking_id).first()
        if em:
            em.user_confirmed = True
            db.commit()
            return "", 200
        abort(404)
    finally:
        db.close()


@app.route("/api/email/reject/<int:tracking_id>", methods=["POST"])
def email_reject_match(tracking_id):
    """Reject auto-matched email."""
    db = get_db()
    try:
        em = db.query(EmailTracking).filter_by(id=tracking_id).first()
        if em:
            em.user_confirmed = False
            em.auto_matched = False
            em.application_id = None
            db.commit()
            return "", 200
        abort(404)
    finally:
        db.close()


if __name__ == "__main__":
    app.run(debug=True, port=5001)
