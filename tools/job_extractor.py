"""
Job Description Extractor Tool.
Extracts structured job posting details and clean job descriptions from URLs
using pure HTML / DOM parsing (BeautifulSoup with headless Playwright DOM fallback).
Includes specialized high-accuracy support for Workday job portals (targeting [data-automation-id="job-posting-details"]).
No screenshots are used.
"""

import json
import logging
import re
import sys
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup, Tag

logger = logging.getLogger(__name__)

# Standard browser headers to avoid basic bot blocks
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Upgrade-Insecure-Requests": "1",
}

# Common job description container selectors across job boards
JOB_SELECTORS = [
    # Workday primary selectors
    '[data-automation-id="job-posting-details"]',
    '[data-automation-id="jobPostingDescription"]',
    # Indeed
    "#jobDescriptionText",
    ".jobsearch-jobDescriptionText",
    # LinkedIn
    ".show-more-less-html__markup",
    ".description__text",
    ".jobs-description__content",
    ".jobs-box__html-content",
    # Greenhouse
    "#content .body",
    "#content",
    ".job-post",
    # Lever
    ".posting-requirements",
    ".section-wrapper",
    ".section.page-centered",
    # Ashby
    ".ashby-job-posting-description",
    "[class*='ashby-job-posting']",
    # Generic semantic tags and classes
    "[class*='job-description' i]",
    "[id*='job-description' i]",
    "[class*='jobDescription' i]",
    "[id*='jobDescription' i]",
    "[class*='job-details' i]",
    "[id*='job-details' i]",
    "[class*='posting-description' i]",
    "[class*='role-description' i]",
    "[class*='vacancy-desc' i]",
    "article",
    "main",
    '[role="main"]',
]


@dataclass
class JobDetails:
    """Structured representation of extracted job details."""

    url: str
    title: Optional[str] = None
    company: Optional[str] = None
    location: Optional[str] = None
    employment_type: Optional[str] = None
    date_posted: Optional[str] = None
    requisition_id: Optional[str] = None
    job_description: str = ""
    extraction_method: str = "none"  # 'workday-api', 'workday-dom', 'json-ld', 'dom-selector', 'playwright-dom'
    success: bool = True
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return asdict(self)


class JobExtractorError(Exception):
    """Raised when job extraction fails."""
    pass


class JobExtractor:
    """
    Extracts job descriptions and metadata from web links using pure HTML / DOM inspection.
    """

    def __init__(
        self,
        headers: Optional[Dict[str, str]] = None,
        timeout: int = 15,
        enable_browser_fallback: bool = True,
    ):
        self.headers = headers or DEFAULT_HEADERS
        self.timeout = timeout
        self.enable_browser_fallback = enable_browser_fallback

    @staticmethod
    def _clean_html_to_text(soup_or_tag: Tag | BeautifulSoup) -> str:
        """
        Converts an HTML tag / DOM fragment into clean, structured text preserving paragraphs and bullet lists.
        """
        tag = BeautifulSoup(str(soup_or_tag), "html.parser")

        # Strip non-content / noise tags
        for element in tag(["script", "style", "noscript", "svg", "button", "nav", "footer", "header", "form", "iframe"]):
            element.decompose()

        # Format lists cleanly with markdown dashes
        for li in tag.find_all("li"):
            text = li.get_text(strip=True)
            if text:
                li.replace_with(f"\n- {text}")

        # Format headings with line breaks
        for h in tag.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
            text = h.get_text(strip=True)
            if text:
                h.replace_with(f"\n\n### {text}\n")

        for p in tag.find_all(["p", "div", "section"]):
            p.append("\n")

        for br in tag.find_all("br"):
            br.replace_with("\n")

        raw_text = tag.get_text()

        # Collapse excess empty lines
        lines = [line.strip() for line in raw_text.splitlines()]
        cleaned_paragraphs: List[str] = []
        consecutive_empty = 0

        for line in lines:
            if line:
                cleaned_paragraphs.append(line)
                consecutive_empty = 0
            else:
                consecutive_empty += 1
                if consecutive_empty <= 1:
                    cleaned_paragraphs.append("")

        return "\n".join(cleaned_paragraphs).strip()

    # -------------------------------------------------------------------------
    # Workday Specialized Extraction (API + DOM)
    # -------------------------------------------------------------------------

    def _is_workday_url(self, url: str) -> bool:
        """Checks if the URL is hosted on a Workday career portal."""
        return "myworkdayjobs.com" in url.lower()

    def _extract_workday_api(self, url: str) -> Optional[JobDetails]:
        """
        Extracts Workday job posting directly using Workday's client-facing REST API (cxs).
        Very fast and reliable for all *.myworkdayjobs.com postings.
        """
        parsed = urlparse(url)
        # Matches: ...myworkdayjobs.com/{optional-lang}/{site}/job/{location}/{job_slug_id}
        match = re.search(r"myworkdayjobs\.com/(?:[a-zA-Z\-]+/)?([^/]+)/job/(.+)", url)
        if not match:
            return None

        site = match.group(1)
        job_path = match.group(2).split("?")[0]
        company = parsed.netloc.split(".")[0]  # e.g., nvidia from nvidia.wd5.myworkdayjobs.com

        api_url = f"https://{parsed.netloc}/wday/cxs/{company}/{site}/job/{job_path}"

        try:
            session = requests.Session()
            session.headers.update(self.headers)
            session.headers["Accept"] = "application/json"
            resp = session.get(api_url, timeout=self.timeout)

            if resp.status_code == 200:
                data = resp.json()
                info = data.get("jobPostingInfo", {})
                title = info.get("title")
                location = info.get("location")
                time_type = info.get("timeType")
                posted_on = info.get("postedOn")
                req_id = info.get("jobReqId")
                raw_desc = info.get("jobDescription", "")

                clean_desc = self._clean_html_to_text(BeautifulSoup(raw_desc, "html.parser")) if raw_desc else ""

                if clean_desc and len(clean_desc) > 80:
                    return JobDetails(
                        url=url,
                        title=title,
                        company=company.capitalize(),
                        location=location,
                        employment_type=time_type,
                        date_posted=posted_on,
                        requisition_id=req_id,
                        job_description=clean_desc,
                        extraction_method="workday-api",
                        success=True,
                    )
        except Exception as e:
            logger.debug("Workday API extraction failed for %s: %s", url, e)

        return None

    def _extract_workday_dom(self, soup: BeautifulSoup, url: str) -> Optional[JobDetails]:
        """
        Extracts Workday job posting from DOM targeting [data-automation-id="job-posting-details"].
        """
        details_container = soup.find(attrs={"data-automation-id": "job-posting-details"})
        if not details_container:
            # Also check for direct jobPostingDescription
            desc_container = soup.find(attrs={"data-automation-id": "jobPostingDescription"})
            if desc_container:
                clean_desc = self._clean_html_to_text(desc_container)
                title_el = soup.find(attrs={"data-automation-id": "jobPostingHeader"}) or soup.find("h1") or soup.find("h2")
                title = title_el.get_text(strip=True) if title_el else (soup.title.get_text(strip=True) if soup.title else None)
                return JobDetails(
                    url=url,
                    title=title,
                    job_description=clean_desc,
                    extraction_method="workday-dom",
                    success=True,
                )
            return None

        # Extract metadata fields inside job-posting-details
        location = None
        loc_el = details_container.find(attrs={"data-automation-id": "locations"})
        if loc_el:
            dd = loc_el.find("dd")
            location = dd.get_text(strip=True) if dd else loc_el.get_text(strip=True)

        employment_type = None
        time_el = details_container.find(attrs={"data-automation-id": "time"})
        if time_el:
            dd = time_el.find("dd")
            employment_type = dd.get_text(strip=True) if dd else time_el.get_text(strip=True)

        date_posted = None
        posted_el = details_container.find(attrs={"data-automation-id": "postedOn"})
        if posted_el:
            dd = posted_el.find("dd")
            date_posted = dd.get_text(strip=True) if dd else posted_el.get_text(strip=True)

        req_id = None
        req_el = details_container.find(attrs={"data-automation-id": "requisitionId"})
        if req_el:
            dd = req_el.find("dd")
            req_id = dd.get_text(strip=True) if dd else req_el.get_text(strip=True)

        # Extract job description body
        desc_container = details_container.find(attrs={"data-automation-id": "jobPostingDescription"})
        if desc_container:
            clean_desc = self._clean_html_to_text(desc_container)
        else:
            clean_desc = self._clean_html_to_text(details_container)

        # Extract Title
        title_el = soup.find(attrs={"data-automation-id": "jobPostingHeader"}) or soup.find("h1") or soup.find("h2")
        title = title_el.get_text(strip=True) if title_el else (soup.title.get_text(strip=True) if soup.title else None)

        company = urlparse(url).netloc.split(".")[0].capitalize()

        if len(clean_desc) > 80:
            return JobDetails(
                url=url,
                title=title,
                company=company,
                location=location,
                employment_type=employment_type,
                date_posted=date_posted,
                requisition_id=req_id,
                job_description=clean_desc,
                extraction_method="workday-dom",
                success=True,
            )

        return None

    # -------------------------------------------------------------------------
    # Schema.org JSON-LD Extraction
    # -------------------------------------------------------------------------

    def _extract_from_json_ld(self, soup: BeautifulSoup, url: str) -> Optional[JobDetails]:
        """
        Extracts JobPosting schema from JSON-LD scripts (<script type="application/ld+json">).
        """
        for script in soup.find_all("script", type="application/ld+json"):
            if not script.string:
                continue
            try:
                data = json.loads(script.string.strip())
            except Exception:
                continue

            items = data if isinstance(data, list) else [data]
            if isinstance(data, dict) and "@graph" in data and isinstance(data["@graph"], list):
                items.extend(data["@graph"])

            for item in items:
                if not isinstance(item, dict):
                    continue

                schema_type = item.get("@type", "")
                is_job = (
                    schema_type == "JobPosting"
                    or (isinstance(schema_type, list) and "JobPosting" in schema_type)
                )

                if is_job:
                    title = item.get("title")
                    company = None
                    hiring_org = item.get("hiringOrganization")
                    if isinstance(hiring_org, dict):
                        company = hiring_org.get("name")
                    elif isinstance(hiring_org, str):
                        company = hiring_org

                    location = None
                    job_loc = item.get("jobLocation")
                    if isinstance(job_loc, dict):
                        addr = job_loc.get("address")
                        if isinstance(addr, dict):
                            loc_parts = [
                                addr.get("addressLocality"),
                                addr.get("addressRegion"),
                                addr.get("addressCountry"),
                            ]
                            location = ", ".join(p for p in loc_parts if p)
                        elif isinstance(addr, str):
                            location = addr
                    elif isinstance(job_loc, list) and job_loc:
                        first_loc = job_loc[0]
                        if isinstance(first_loc, dict) and "address" in first_loc:
                            addr = first_loc["address"]
                            if isinstance(addr, dict):
                                loc_parts = [
                                    addr.get("addressLocality"),
                                    addr.get("addressRegion"),
                                    addr.get("addressCountry"),
                                ]
                                location = ", ".join(p for p in loc_parts if p)

                    employment_type = item.get("employmentType")
                    date_posted = item.get("datePosted")
                    raw_description = item.get("description", "")

                    clean_desc = (
                        self._clean_html_to_text(BeautifulSoup(raw_description, "html.parser"))
                        if raw_description
                        else ""
                    )

                    if len(clean_desc) > 80:
                        return JobDetails(
                            url=url,
                            title=title,
                            company=company,
                            location=location,
                            employment_type=employment_type,
                            date_posted=date_posted,
                            job_description=clean_desc,
                            extraction_method="json-ld",
                            success=True,
                        )
        return None

    # -------------------------------------------------------------------------
    # Generic DOM Selectors & Body Fallback
    # -------------------------------------------------------------------------

    def _extract_from_dom_selectors(self, soup: BeautifulSoup, url: str) -> Optional[JobDetails]:
        """
        Extracts content using common DOM selectors for job descriptions.
        """
        # First check Workday DOM specifics
        workday_res = self._extract_workday_dom(soup, url)
        if workday_res:
            return workday_res

        best_candidate: Optional[Tag] = None
        best_length = 0

        for selector in JOB_SELECTORS:
            try:
                elements = soup.select(selector)
            except Exception:
                continue

            for el in elements:
                text_len = len(el.get_text(strip=True))
                if text_len > 120 and text_len > best_length:
                    best_candidate = el
                    best_length = text_len

            if best_candidate and best_length > 300:
                break

        if best_candidate:
            cleaned_text = self._clean_html_to_text(best_candidate)
            if len(cleaned_text) > 80:
                h1 = soup.find("h1")
                title = h1.get_text(strip=True) if h1 else (soup.title.get_text(strip=True) if soup.title else None)

                return JobDetails(
                    url=url,
                    title=title,
                    job_description=cleaned_text,
                    extraction_method="dom-selector",
                    success=True,
                )

        return None

    def _extract_from_body_fallback(self, soup: BeautifulSoup, url: str) -> Optional[JobDetails]:
        """
        Extracts job description from <body> after stripping noise elements.
        """
        body = soup.body
        if not body:
            return None

        cleaned_text = self._clean_html_to_text(body)
        if len(cleaned_text) > 100:
            title = soup.title.get_text(strip=True) if soup.title else None
            return JobDetails(
                url=url,
                title=title,
                job_description=cleaned_text,
                extraction_method="body-fallback",
                success=True,
            )
        return None

    def _parse_html_content(self, html: str, url: str) -> Optional[JobDetails]:
        """
        Parses raw HTML string with BeautifulSoup applying all pure DOM extraction tiers.
        """
        soup = BeautifulSoup(html, "html.parser")

        # Tier 1: Workday DOM if matching selector found
        workday_res = self._extract_workday_dom(soup, url)
        if workday_res:
            return workday_res

        # Tier 2: Schema.org JSON-LD JobPosting
        res = self._extract_from_json_ld(soup, url)
        if res:
            return res

        # Tier 3: Specific high-probability job description DOM selectors
        res = self._extract_from_dom_selectors(soup, url)
        if res:
            return res

        # Tier 4: Cleaned body fallback
        res = self._extract_from_body_fallback(soup, url)
        if res:
            return res

        return None

    # -------------------------------------------------------------------------
    # Fetching Helpers (Static vs Headless Playwright DOM)
    # -------------------------------------------------------------------------

    def _fetch_static_html(self, url: str) -> str:
        """Fetches HTML using standard HTTP request."""
        session = requests.Session()
        session.headers.update(self.headers)
        response = session.get(url, timeout=self.timeout, allow_redirects=True)
        response.raise_for_status()
        return response.text

    def _fetch_playwright_dom(self, url: str) -> str:
        """
        Renders page DOM using headless Playwright Chromium (pure HTML/DOM inspection, no screenshot).
        Waits for [data-automation-id="job-posting-details"] on Workday pages or DOM ready on others.
        """
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
            )
            context = browser.new_context(
                user_agent=self.headers["User-Agent"],
                viewport={"width": 1280, "height": 800},
            )
            page = context.new_page()

            # Navigate and wait for DOM content
            page.goto(url, wait_until="domcontentloaded", timeout=self.timeout * 1000)

            # If Workday URL, explicitly wait for the job posting container
            if self._is_workday_url(url):
                try:
                    page.wait_for_selector(
                        '[data-automation-id="job-posting-details"], [data-automation-id="jobPostingDescription"]',
                        timeout=12000,
                    )
                except Exception:
                    pass
            else:
                try:
                    page.wait_for_load_state("networkidle", timeout=5000)
                except Exception:
                    pass

            rendered_html = page.content()
            browser.close()
            return rendered_html

    # -------------------------------------------------------------------------
    # Main Extraction Entrypoint
    # -------------------------------------------------------------------------

    def extract(self, url: str) -> JobDetails:
        """
        Extracts job description and metadata from the given URL.
        1. For Workday URLs (*.myworkdayjobs.com), checks fast REST API endpoint first.
        2. Attempts static HTTP fetch + BeautifulSoup DOM analysis.
        3. If static fetch fails or returns empty/shell content (SPA), falls back to Playwright DOM rendering.
        """
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return JobDetails(
                url=url,
                success=False,
                error=f"Invalid URL: '{url}'. Must include http:// or https://",
            )

        # Fast path for Workday URLs
        if self._is_workday_url(url):
            workday_api_result = self._extract_workday_api(url)
            if workday_api_result and len(workday_api_result.job_description) >= 100:
                return workday_api_result

        static_error: Optional[str] = None
        html_content: Optional[str] = None

        # Step 1: Static HTML fetch
        try:
            html_content = self._fetch_static_html(url)
            details = self._parse_html_content(html_content, url)
            if details and len(details.job_description) >= 150:
                return details
        except Exception as e:
            static_error = str(e)
            logger.debug("Static fetch failed for %s: %s", url, e)

        # Step 2: Headless Playwright DOM Fallback (pure DOM inspection, no screenshot)
        if self.enable_browser_fallback:
            try:
                rendered_html = self._fetch_playwright_dom(url)
                details = self._parse_html_content(rendered_html, url)
                if details and len(details.job_description) >= 80:
                    details.extraction_method = f"playwright-{details.extraction_method}"
                    return details
            except Exception as e:
                logger.debug("Playwright DOM extraction failed for %s: %s", url, e)
                return JobDetails(
                    url=url,
                    success=False,
                    error=f"Extraction failed: static error ({static_error}); browser error ({e})",
                )

        # If static HTML was retrieved but had short content
        if html_content:
            details = self._parse_html_content(html_content, url)
            if details and details.job_description:
                return details

        return JobDetails(
            url=url,
            success=False,
            error=static_error or "Could not find job description content on page.",
        )


# Convenience functions for agent tools and scripts

def extract_job_description(url: str, enable_browser_fallback: bool = True) -> JobDetails:
    """
    Extracts structured job posting details from a URL using pure HTML / DOM inspection.

    Args:
        url: Link to the job posting.
        enable_browser_fallback: If True, uses headless Playwright DOM fallback for SPAs.

    Returns:
        JobDetails instance with title, company, location, and clean job_description text.
    """
    extractor = JobExtractor(enable_browser_fallback=enable_browser_fallback)
    return extractor.extract(url)


def get_job_description_text(url: str) -> str:
    """
    Convenience tool function: extracts and returns just the clean job description text.
    Suitable directly as an Agent Tool function.
    """
    details = extract_job_description(url)
    if not details.success or not details.job_description:
        raise JobExtractorError(details.error or "Failed to extract job description.")
    return details.job_description


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m tool.job_extractor <job_url>")
        sys.exit(1)

    target_url = sys.argv[1]
    print(f"Extracting job description from: {target_url} ...")
    res = extract_job_description(target_url)

    if res.success:
        print("\n=== Job Details ===")
        print(f"Title:          {res.title}")
        print(f"Company:        {res.company}")
        print(f"Location:       {res.location}")
        print(f"Time Type:      {res.employment_type}")
        print(f"Date Posted:    {res.date_posted}")
        print(f"Requisition ID: {res.requisition_id}")
        print(f"Method:         {res.extraction_method}")
        print(f"Desc Length:    {len(res.job_description)} chars")
        print("\n=== Description Preview ===")
        print(res.job_description[:600] + ("..." if len(res.job_description) > 600 else ""))
    else:
        print(f"\nFailed: {res.error}", file=sys.stderr)
        sys.exit(1)
