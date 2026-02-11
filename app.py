"""
app.py — Flask + HTMX Web UI for Career Pipeline
Routes: upload form, background pipeline, SSE progress, results, PDF download
"""

import os
import re
import uuid
import hashlib
import tempfile
import threading
import time
import json

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
    read_docx,
)
from pdf_builder import build_pdf, build_cover_letter_pdf
from recruiter_hunt import find_recruiters

app = Flask(__name__)

# In-memory job store — cleared on restart
jobs = {}

TEMP_DIR = os.path.join(tempfile.gettempdir(), "career_pipeline_jobs")
os.makedirs(TEMP_DIR, exist_ok=True)

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


def run_pipeline_job(job_id, jd, resume_latex, style_path):
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
        if style_path and os.path.exists(style_path):
            style_voice = read_docx(style_path)
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

        # Save resume .tex and compile
        tex_path = os.path.join(job_dir, "resume.tex")
        with open(tex_path, "w", encoding="utf-8") as f:
            f.write(tailored_latex)
        build_pdf(tex_path, job_dir)

        # Build cover letter PDF
        cover_letter = narratives.get("cover_letter", "")
        if cover_letter:
            build_cover_letter_pdf(cover_letter, job_dir, "cover_letter")

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

    # Read style sample: from new upload OR from history
    style_path = None
    style_id = request.form.get("style_id", "").strip()
    style_file = request.files.get("style_file")

    job_id = str(uuid.uuid4())[:8]

    if style_file and style_file.filename:
        # New upload — save to history and use
        file_data = style_file.read()
        _save_upload(file_data, style_file.filename, "styles")
        job_dir = os.path.join(TEMP_DIR, job_id)
        os.makedirs(job_dir, exist_ok=True)
        style_path = os.path.join(job_dir, "style_sample.docx")
        with open(style_path, "wb") as f:
            f.write(file_data)
    elif style_id:
        # Load from history
        style_path = _get_upload_path(style_id, "styles")

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
    }

    # Start background thread
    thread = threading.Thread(
        target=run_pipeline_job,
        args=(job_id, jd, resume_latex, style_path),
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


if __name__ == "__main__":
    app.run(debug=True, port=5001)
