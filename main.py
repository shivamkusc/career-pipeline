import os
from dotenv import load_dotenv
load_dotenv()

from ai_engine import run_pipeline
from recruiter_hunt import find_recruiters
from pdf_builder import build_pdf, build_cover_letter_pdf

# --- Settings ---
INPUT_DIR = "inputs"
OUTPUT_DIR = "outputs"
RESUME_FILE = "master_resume.tex"
JD_FILE = "job_description.txt"
STYLE_FILE = "style_sample.docx"


def main():
    print("=" * 60)
    print("🚀 CAREER PIPELINE")
    print("=" * 60)

    # 1. Check Paths
    resume_path = os.path.join(INPUT_DIR, RESUME_FILE)
    jd_path = os.path.join(INPUT_DIR, JD_FILE)
    style_path = os.path.join(INPUT_DIR, STYLE_FILE)

    if not os.path.exists(resume_path) or not os.path.exists(jd_path):
        print("❌ Critical Error: Input files missing!")
        return

    # 2. Read Files
    with open(resume_path, "r", encoding="utf-8") as f:
        resume_content = f.read()
    with open(jd_path, "r", encoding="utf-8") as f:
        jd_content = f.read()

    # 3. Run AI Pipeline (Analyze → Tailor → Write → Validate → Score)
    style_arg = style_path if os.path.exists(style_path) else None
    result = run_pipeline(jd_content, resume_content, style_arg)

    # 4. Save Content
    print("\n💾 Saving Generated Files...")
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Clean role title and company name for filenames
    def sanitize(name):
        name = name.replace("/", "-").replace("\\", "-")
        return "".join(c for c in name if c not in '<>:"|?*')

    role_title = sanitize(result.analysis.role_title)
    company_name = sanitize(result.analysis.company_name)

    # Save resume as PDF in dedicated folder
    resume_dir = os.path.join(OUTPUT_DIR, "resumes")
    os.makedirs(resume_dir, exist_ok=True)
    resume_filename = f"Shivam Kumar {company_name} {role_title} Resume"
    tex_output_path = os.path.join(resume_dir, f"{resume_filename}.tex")
    with open(tex_output_path, "w", encoding="utf-8") as f:
        f.write(result.resume_latex)

    # Save cover letter as PDF in dedicated folder
    if result.cover_letter:
        cover_dir = os.path.join(OUTPUT_DIR, "cover_letters")
        os.makedirs(cover_dir, exist_ok=True)
        cover_filename = f"Shivam Kumar {company_name} {role_title} Cover Letter"
        build_cover_letter_pdf(result.cover_letter, cover_dir, cover_filename)
        print(f"✅ Saved cover letter: {cover_filename}.pdf")

    with open(os.path.join(OUTPUT_DIR, "cold_email.txt"), "w", encoding="utf-8") as f:
        content = result.cold_email or ""
        if result.linkedin_message:
            content += f"\n\n---\nLinkedIn version ({len(result.linkedin_message)} chars):\n{result.linkedin_message}"
        f.write(content)

    with open(os.path.join(OUTPUT_DIR, "research_notes.txt"), "w", encoding="utf-8") as f:
        f.write(result.analysis.research_notes)
    print("✅ Saved research_notes.txt")

    # Save validation & ATS results
    if result.validation_results or result.ats_score:
        with open(os.path.join(OUTPUT_DIR, "quality_report.txt"), "w", encoding="utf-8") as f:
            if result.validation_results:
                f.write("=== HALLUCINATION CHECK ===\n")
                f.write(f"Overall: {result.validation_results.get('overall', 'N/A')}\n")
                f.write(f"Danger flags: {result.validation_results.get('danger_count', 0)}\n")
                f.write(f"Warning flags: {result.validation_results.get('warning_count', 0)}\n\n")
                for flag in result.validation_results.get("flags", []):
                    f.write(f"[{flag['status']}] {flag['bullet']}\n  Reason: {flag['reason']}\n\n")

            if result.ats_score:
                f.write("\n=== ATS KEYWORD COVERAGE ===\n")
                f.write(f"Score: {result.ats_score.get('score', 0):.0%}\n")
                f.write(f"Matched: {', '.join(result.ats_score.get('matched', []))}\n")
                f.write(f"Missing: {', '.join(result.ats_score.get('missing', []))}\n")
        print("✅ Saved quality_report.txt")

    # 5. Build Resume PDF
    build_pdf(tex_output_path, resume_dir)
    # Clean up .tex after successful PDF build
    if os.path.exists(os.path.join(resume_dir, f"{resume_filename}.pdf")):
        os.remove(tex_output_path)
    print(f"✅ Saved resume: {resume_filename}.pdf")

    # 6. Find Recruiters
    company = result.analysis.company_name
    location = result.analysis.location

    if company and company != "Unknown Company":
        found_people = find_recruiters(company, location)

        recruiter_file = os.path.join(OUTPUT_DIR, "potential_recruiters.txt")
        with open(recruiter_file, "w", encoding="utf-8") as f:
            f.write(f"--- RECRUITERS FOR {company.upper()} ---\n\n")
            if found_people:
                for p in found_people:
                    f.write(f"👤 Name: {p['name']}\n")
                    f.write(f"🔗 Link: {p['link']}\n")
                    f.write(f"📝 Bio: {p['snippet']}\n")
                    f.write("-" * 30 + "\n")
                print(f"✅ Found {len(found_people)} potential recruiters! Check 'potential_recruiters.txt'")
            else:
                f.write("No direct matches found.\n")
    else:
        print("⚠️ Could not detect company name, skipping recruiter search.")

    print(f"\n{'=' * 60}")
    print("✅ DONE! Check 'outputs/' folder.")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
