# Career Pipeline

An AI-powered job application engine that takes a job description and your master resume, then generates a **tailored resume**, **cover letter**, **cold email**, **LinkedIn message**, and a **list of recruiters** at the target company — all in one run.

Includes a full **application tracker**, **network relationship manager**, **automated email monitoring**, **A/B testing** of outreach variants, and **AI follow-up generation**.

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
| **6. Recruiters** | — | Searches for recruiters at the target company via Serper.dev / DuckDuckGo |

### Recruiter Search

After generating materials, the pipeline searches for recruiters and talent acquisition contacts at the target company using:
- **Serper.dev** (Google SERP API) as primary
- **DuckDuckGo** as fallback
- **Manual LinkedIn URL** as last resort

## Features

### Document Generation
- **Tailored Resume** — reworded bullets optimized for ATS keyword coverage, compiled to PDF via LaTeX
- **Cover Letter** — tone-matched to your writing style samples, paragraph-formatted PDF
- **Cold Email** — professional outreach with parsed subject line
- **LinkedIn Message** — character-counted (300 limit) with color-coded length indicator
- **Multiple Style Samples** — upload multiple `.docx` / `.pdf` writing samples for better voice matching

### Application Tracker
- Full CRUD for job applications with status pipeline (Applied → Screening → Interview → Offer / Rejected)
- Follow-up scheduling with reminders (Email, Phone, LinkedIn, Thank You, Other)
- Interview logging with outcome tracking
- Document-sent tracking per application
- Analytics dashboard with funnel visualization, weekly volume, ATS brackets, day-of-week success rates

### Network Relationship Manager
- Contact database with relationship strength tracking (cold / warm / close)
- LinkedIn CSV import with auto-tagging by job title
- Interaction timeline logging (email, call, coffee chat, referral, event, other)
- Referral tracking with outcome recording
- AI-generated coffee chat requests (email + LinkedIn versions)
- Outreach suggestions — scores contacts by relevance to target companies
- Network gap detection — finds companies you've applied to with no contacts
- Automatic relationship decay (warm → cold after 180 days, close → warm after 120 days)

### Email Integration
- **Gmail OAuth** — read-only access to classify incoming emails automatically
- AI-powered email classification (application received, screening, interview invite, rejection, offer)
- Auto-matching emails to tracked applications by company domain + name fuzzy matching
- Automatic follow-up creation based on email type
- Encrypted OAuth token storage (Fernet symmetric encryption)
- Confidence-based auto-update — high confidence matches update application status automatically

### A/B Testing
- 5 outreach strategies: Technical Depth, Business Impact, Culture Fit, Narrative Arc, Quantitative Proof
- Auto-selects best 3 strategies based on job title, skills, and responsibilities
- Generates cover letter + email + LinkedIn variants per strategy
- Chi-square significance testing for variant performance
- Weighted outcome scoring (no_response=0, rejection=1, screening=3, interview=7, offer=10)
- Recommendation engine — suggests winning variant when data reaches statistical significance

### AI Follow-up Generation
- 6 follow-up types: initial check-in, post-interview thank you, offer negotiation, rejection response, networking, custom
- Context-aware messages using application data (company, role, days since applied, status)
- Suggested follow-up schedules based on application status and method
- Batch generation across multiple applications

### Background Jobs
- Email monitoring (configurable interval, default 15 min)
- Daily follow-up reminders (configurable hour)
- Weekly network relationship decay
- Weekly A/B variant performance analysis with cached results
- Monthly cleanup (temp files + database vacuum)
- Per-job pause/resume/run-now controls

### Web UI
- Flask + HTMX + Pico CSS (no JavaScript frameworks)
- Real-time progress with stage timing (Server-Sent Events)
- Tabbed results: Resume, Cover Letter, Outreach, Recruiters, Quality, Analysis
- Inline PDF preview, ATS score bar, LinkedIn character counter
- Dark mode toggle
- File history with content-hash deduplication
- JD memory (last job description pre-filled on load)
- Toast notifications for copy/delete actions
- Keyboard shortcuts (1-6 tabs, D dark mode, K network, S settings)
- Settings page for all configurable options

## Output

```
outputs/
├── resumes/               → Shivam Kumar {Company} {Role} Resume.pdf
├── cover_letters/         → Shivam Kumar {Company} {Role} Cover Letter.pdf
├── cold_email.txt            (includes LinkedIn message)
├── research_notes.txt        (company talking points)
├── quality_report.txt        (hallucination check + ATS score)
└── potential_recruiters.txt
```

## Setup

### Prerequisites

- Python 3.9+
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
SERPER_API_KEY=your_serper_dev_api_key          # optional, enables recruiter search
ENCRYPTION_KEY=your_fernet_key                  # auto-generated, for OAuth token encryption
```

Generate an encryption key:
```python
from cryptography.fernet import Fernet
print(Fernet.generate_key().decode())
```

- **Anthropic API key** — [console.anthropic.com](https://console.anthropic.com/)
- **Serper.dev API key** — [serper.dev](https://serper.dev/) (2,500 free searches)

### Gmail Integration (optional)

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a project and enable the **Gmail API**
3. Configure **OAuth consent screen** (External, add your email as test user)
4. Create **OAuth 2.0 Client ID** (Web application)
5. Set redirect URI: `http://127.0.0.1:5001/oauth/callback/gmail`
6. Add to `.env`:
```env
GMAIL_CLIENT_ID=your_client_id
GMAIL_CLIENT_SECRET=your_client_secret
```
7. Start the app, go to Settings, and click **Connect Gmail**

## Usage

### Web UI (recommended)

```bash
python app.py
```

Open [http://127.0.0.1:5001](http://127.0.0.1:5001) in your browser.

### CLI

```bash
python main.py
```

Place input files in `inputs/`:

| File | Format | Required |
|------|--------|----------|
| `master_resume.tex` | LaTeX | Yes |
| `job_description.txt` | Plain text | Yes |
| `style_sample.docx` | Word doc (.docx / .pdf) | No (improves cover letter voice matching) |

## Tech Stack

| Layer | Technology |
|-------|-----------|
| **LLM** | Claude Sonnet 4.5 via Anthropic API (prompt caching, exponential backoff) |
| **Web UI** | Flask + HTMX + Pico CSS (SSE for real-time progress) |
| **Database** | SQLite via SQLAlchemy ORM |
| **Background Jobs** | APScheduler with SQLAlchemy job store |
| **Email** | Gmail API (google-auth-oauthlib), Outlook via MSAL (optional) |
| **Security** | Fernet symmetric encryption for OAuth tokens |
| **Resume Format** | LaTeX → PDF via `pdflatex` |
| **Search** | Serper.dev (Google SERP proxy) + DuckDuckGo fallback |
| **Document Parsing** | python-docx (.docx), PyPDF2 (.pdf) |

## Project Structure

```
career_pipeline/
├── app.py                  # Flask web server — all routes, SSE progress, file history
├── main.py                 # CLI entry point
├── ai_engine.py            # LLM calls — analyze, tailor, write, validate, score
├── pdf_builder.py          # LaTeX → PDF compilation
├── recruiter_hunt.py       # Serper.dev + DuckDuckGo recruiter search
├── tracker.py              # SQLAlchemy models + CRUD (17 models, full tracker)
├── followup_engine.py      # AI follow-up message generation (6 types)
├── email_monitor.py        # Gmail/Outlook OAuth, email classification, auto-matching
├── network_manager.py      # LinkedIn CSV import, relationship decay, outreach suggestions
├── ab_testing.py           # 5 variant strategies, chi-square significance testing
├── scheduler.py            # APScheduler background jobs (5 periodic tasks)
├── templates/
│   ├── base.html           # Layout with nav, dark mode toggle, keyboard shortcuts
│   ├── index.html          # Upload form with drag-drop, file history, JD memory
│   ├── partials/           # HTMX fragments (progress, results, tabs, variants, etc.)
│   ├── tracker/            # Application tracker pages (list, detail, analytics)
│   ├── network/            # Network dashboard + contact detail
│   └── settings/           # Settings page (email, follow-ups, network, A/B, jobs)
├── static/
│   └── style.css           # Custom styles (Pico CSS overrides, dark mode, components)
├── uploads/                # Persistent file storage with manifest.json
├── requirements.txt
├── .env                    # API keys and secrets (git-ignored)
└── .gitignore
```

## Database Schema

SQLite database (`applications.db`) with 17 models:

| Model | Purpose |
|-------|---------|
| Application | Job applications with status, dates, notes |
| FollowUp | Scheduled follow-up actions per application |
| Interview | Interview records with type, date, outcome |
| DocumentSent | Track which documents were sent where |
| Contact | Network contacts with relationship strength |
| ContactInteraction | Interaction timeline per contact |
| Referral | Referral tracking with outcome |
| ABTestVariant | A/B test variants with strategy and outcome |
| EmailTracking | Classified emails matched to applications |
| OAuthToken | Encrypted OAuth tokens for email providers |
| AppSetting | Key-value settings store |

## Architecture Notes

- **Delimiter-based extraction** (`===LATEX_START===` / `===LATEX_END===`) instead of JSON for LaTeX content to avoid escaping conflicts
- **Temperature tuning** per stage — low for analysis/validation, higher for creative writing
- **Hallucination guard** — strict rules prevent the LLM from adding new bullet points, metrics, or experiences not in the original resume
- **LaTeX escape rules** baked into prompts to prevent compilation failures (`%` → `\%`, `&` → `\&`, etc.)
- **Persistent upload storage** — files saved to `uploads/` with a JSON manifest; content-hash dedup prevents duplicates
- **APScheduler pickle safety** — scheduler jobs use lazy factory functions to avoid SQLAlchemy session serialization issues
- **Encrypted token storage** — OAuth access/refresh tokens encrypted at rest with Fernet; key stored in `.env`
- **Dual input handling** — API endpoints accept both JSON and form data for HTMX + programmatic access
