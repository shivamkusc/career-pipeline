import os
import time
import random
import urllib.parse

# Load .env file if python-dotenv is available, otherwise rely on shell env
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

# DuckDuckGo (fallback)
try:
    from ddgs import DDGS
    HAS_DDGS = True
except ImportError:
    try:
        from duckduckgo_search import DDGS
        HAS_DDGS = True
    except ImportError:
        HAS_DDGS = False

SERPER_API_KEY = os.environ.get("SERPER_API_KEY")


def _search_serper(company, location="", max_results=5):
    """Strategy A: Serper.dev Google Search API — fast and reliable."""
    if not HAS_REQUESTS or not SERPER_API_KEY:
        return None

    query = f'site:linkedin.com/in/ "{company}" ("Recruiter" OR "Talent Acquisition") {location}'.strip()

    print(f"   🔍 Querying Serper: {query}")

    resp = requests.post(
        "https://google.serper.dev/search",
        headers={
            "X-API-KEY": SERPER_API_KEY,
            "Content-Type": "application/json",
        },
        json={"q": query, "num": max_results},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()

    items = data.get("organic", [])
    if not items:
        return None

    recruiters = []
    for item in items:
        title = item.get("title", "Unknown")
        name = title.split("-")[0].split("|")[0].strip()
        recruiters.append({
            "name": name,
            "link": item.get("link", "#"),
            "snippet": item.get("snippet", ""),
        })
    return recruiters


def _search_ddg(company, location="", max_results=5):
    """Strategy B: DuckDuckGo text search — free, no key needed."""
    if not HAS_DDGS:
        print("   ⚠️ DuckDuckGo library not installed")
        return None

    try:
        time.sleep(random.uniform(3, 6))

        ddgs = DDGS(timeout=20)
        query = f'site:linkedin.com/in/ "{company}" ("Recruiter" OR "Talent Acquisition") {location}'.strip()

        print(f"   🔍 Querying DuckDuckGo: {query}")

        results = list(ddgs.text(query, max_results=max_results))

        if not results:
            print("   ⚠️ DuckDuckGo returned 0 results")
            return None

        recruiters = []
        for r in results:
            name = r.get("title", "Unknown").split("-")[0].strip()
            recruiters.append({
                "name": name,
                "link": r.get("href", "#"),
                "snippet": r.get("body", ""),
            })
        return recruiters

    except Exception as e:
        print(f"   ❌ DuckDuckGo error: {e}")
        return None


def _fallback_link(company):
    """Strategy C: Generate a manual LinkedIn search URL."""
    search_query = f'{company} "Recruiter" OR "Talent Acquisition"'
    encoded_query = urllib.parse.quote(search_query)
    direct_link = f"https://www.linkedin.com/search/results/people/?keywords={encoded_query}"

    return [{
        "name": "👉 CLICK HERE to see Recruiters",
        "link": direct_link,
        "snippet": f"Auto-search was blocked. Click this link to open LinkedIn search for {company}.",
    }]


def find_recruiters(company, location=""):
    """Try Serper → DuckDuckGo → manual link, return first success."""
    print(f"🔎 Searching for recruiters at {company}...")

    company = company.strip()
    if not company:
        return []

    # Strategy A: Serper.dev (Google Search API)
    try:
        results = _search_serper(company, location)
        if results:
            print(f"   ✅ Found {len(results)} matches via Serper (Google)")
            return results
        elif SERPER_API_KEY:
            print("   ⚠️ Serper returned no results, trying DuckDuckGo...")
        else:
            print("   ⚠️ Serper not configured, trying DuckDuckGo...")
    except Exception as e:
        print(f"   ⚠️ Serper failed: {e}")

    # Strategy B: DuckDuckGo
    try:
        results = _search_ddg(company, location)
        if results:
            print(f"   ✅ Found {len(results)} matches via DuckDuckGo")
            return results
    except Exception as e:
        print(f"   ⚠️ DuckDuckGo failed: {e}")

    # Strategy C: Manual fallback link
    print("   ⚠️ All search methods failed. Generating manual LinkedIn link...")
    return _fallback_link(company)
