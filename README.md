# Career Pipeline

An AI-powered job application engine that takes a job description and your master resume, then generates a **tailored resume**, **cover letter**, **cold email**, **LinkedIn message**, and a **list of recruiters** at the target company — all in one run.

Built with Claude Sonnet 4.5 and a multi-stage LLM chain architecture.

## How It Works

```
Job Description ─┐
                  ├──▶ Analyze ──▶ Tailor ──▶ Write ──▶ Validate ──▶ Score
Master Resume ────┘       │          │          │           │           │
                     JobAnalysis  LaTeX PDF   Cover Letter  Hallucination  ATS Keyword
                     (structured  (reworded   + Cold Email  Check (SAFE/   Coverage %
                      fit report)  bullets)   + LinkedIn    WARNING/DANGER)
```

### Pipeline Stages

| Stage | Temp | What it does |
|-------|------|-------------|
| **1. Analyze** | 0.1 | Extracts structured `JobAnalysis` — skills, keyword matches/gaps, differentiators, company research |
| **2. Tailor** | 0.3 | Rewords and reorders existing resume bullets to maximize keyword coverage (never invents experience) |
| **3. Write** | 0.6 | Generates cover letter, cold email, and LinkedIn message matched to your writing voice |
| **4. Validate** | 0.0 | Diffs original vs tailored resume bullet-by-bullet, flags any hallucinated content |
| **5. ATS Score** | 0.0 | Extracts top 20 JD keywords and computes match percentage against tailored resume |

### Recruiter Search

After generating materials, the pipeline searches for recruiters and talent acquisition contacts at the target company using:
- **Serper.dev** (Google SERP API) as primary
- **DuckDuckGo** as fallback
- **Manual LinkedIn URL** as last resort

## Output

```
outputs/
├── resumes/            → Shivam Kumar {Company} {Role} Resume.pdf
├── cover_letters/      → Shivam Kumar {Company} {Role} Cover Letter.pdf
├── cold_email.txt         (includes LinkedIn message)
├── research_notes.txt     (company talking points)
├── quality_report.txt     (hallucination check + ATS score)
└── potential_recruiters.txt
```

## Setup

### Prerequisites

- Python 3.10+
- LaTeX distribution with `pdflatex` ([MacTeX](https://www.tug.org/mactex/) on macOS, [TeX Live](https://www.tug.org/texlive/) on Linux)

### Installation

```bash
git clone https://github.com/shivamkusc/career-pipeline.git
cd career-pipeline
pip install -r requirements.txt
```

### API Keys

Create a `.env` file in the project root:

```env
ANTHROPIC_API_KEY=your_anthropic_api_key
SERPER_API_KEY=your_serper_dev_api_key    # optional, enables recruiter search
```

- **Anthropic API key** — [console.anthropic.com](https://console.anthropic.com/)
- **Serper.dev API key** — [serper.dev](https://serper.dev/) (2,500 free searches)

### Input Files

Place these in the `inputs/` directory:

| File | Format | Required |
|------|--------|----------|
| `master_resume.tex` | LaTeX | Yes |
| `job_description.txt` | Plain text | Yes |
| `style_sample.docx` | Word doc | No (improves cover letter voice matching) |

## Usage

```bash
python main.py
```

The pipeline will:
1. Analyze the job description against your resume
2. Tailor your resume (reword + reorder, no invented content)
3. Generate cover letter, cold email, and LinkedIn message
4. Validate for hallucinations
5. Compute ATS keyword coverage score
6. Search for recruiters at the target company
7. Save everything to `outputs/`

## Tech Stack

- **LLM**: Claude Sonnet 4.5 via Anthropic API (prompt caching, exponential backoff retries)
- **Resume format**: LaTeX → PDF via `pdflatex`
- **Cover letter**: Plaintext → LaTeX template → PDF
- **Search**: Serper.dev (Google SERP proxy) + DuckDuckGo fallback
- **Document parsing**: python-docx for .docx style samples

## Architecture Notes

- **Delimiter-based extraction** (`===LATEX_START===` / `===LATEX_END===`) instead of JSON for LaTeX content to avoid escaping conflicts
- **Temperature tuning** per stage — low for analysis/validation, higher for creative writing
- **Hallucination guard** — strict rules prevent the LLM from adding new bullet points, metrics, or experiences not in the original resume
- **LaTeX escape rules** baked into prompts to prevent compilation failures (`%` → `\%`, `&` → `\&`, etc.)
