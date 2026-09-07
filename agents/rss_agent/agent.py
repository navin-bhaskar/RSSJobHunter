"""
RSS Job Processing Agent using OpenAI Agents SDK.
Ingests raw RSS feed items and executes OpenAI Agents SDK Runner
to generate structured, ATS-compliant job postings.
"""

from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional
from dotenv import load_dotenv

import importlib

def _get_openai_agents_sdk():
    """Dynamically imports Agent and Runner from the installed openai-agents package."""
    orig_path = sys.path.copy()
    saved_agents_mod = sys.modules.get("agents")
    if "agents" in sys.modules:
        del sys.modules["agents"]

    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    sys.path = [
        p for p in sys.path
        if os.path.abspath(p) != project_root
        and os.path.abspath(p) != os.path.abspath(".")
        and p not in ("", ".")
    ]
    try:
        import agents as _sdk
        agent_cls = _sdk.Agent
        runner_cls = _sdk.Runner
        return agent_cls, runner_cls
    finally:
        sys.path = orig_path
        if saved_agents_mod is not None:
            sys.modules["agents"] = saved_agents_mod

Agent, Runner = _get_openai_agents_sdk()




from tools import fetch_rss_feed, get_universal_id
from database import create_job, get_job_by_universal_id
from .schema import StructuredJobListingSchema, StructuredFeedSchema

logger = logging.getLogger(__name__)


class RSSJobAgent:
    """
    AI Agent powered by OpenAI Agents SDK that ingests raw RSS feed items
    and produces structured ATS-compliant job listings.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
    ):
        load_dotenv()
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.model = model or os.getenv("OPENAI_MODEL", "gpt-4o")
        self.base_url = base_url or os.getenv("OPENAI_BASE_URL")

        if self.api_key and self.api_key != "your_openai_api_key_here":
            os.environ["OPENAI_API_KEY"] = self.api_key
        if self.base_url:
            os.environ["OPENAI_BASE_URL"] = self.base_url

    def process_feed_url(
        self,
        url: str,
        rss_source_id: Optional[int] = None,
        max_items: Optional[int] = None,
        use_ai: bool = True,
    ) -> StructuredFeedSchema:
        """
        Fetches an RSS feed URL and converts all entries into structured job listings.

        Args:
            url: The RSS/Atom/JSON feed URL.
            rss_source_id: Optional database ID reference for the RSS source feed.
            max_items: Optional limit on the number of feed entries to process.
            use_ai: If True and OPENAI_API_KEY is configured, uses OpenAI Agents SDK; otherwise uses fallback rule parser.

        Returns:
            StructuredFeedSchema instance containing list of structured job listings.
        """
        raw_feed = fetch_rss_feed(url, max_items=max_items)
        raw_items = raw_feed.get("items", [])

        structured_jobs: List[StructuredJobListingSchema] = []
        for item in raw_items:
            try:
                job = self.process_feed_item(item, rss_source_id=rss_source_id, use_ai=use_ai)
                structured_jobs.append(job)
            except Exception as err:
                logger.warning(f"Error processing feed item '{item.get('title')}': {err}")

        return StructuredFeedSchema(
            feed_title=raw_feed.get("feed_title", "Untitled Feed"),
            feed_url=url,
            processed_at=datetime.now(timezone.utc).isoformat(),
            total_raw_items=len(raw_items),
            total_structured_items=len(structured_jobs),
            jobs=structured_jobs,
        )

    def process_feed_item(
        self,
        item: Dict[str, Any],
        rss_source_id: Optional[int] = None,
        use_ai: bool = True,
    ) -> StructuredJobListingSchema:
        """
        Processes a single raw RSS feed item into a StructuredJobListingSchema using OpenAI Agents SDK.
        """
        link = item.get("link", "").strip()
        guid = item.get("id") or link
        uid = item.get("universal_id") or get_universal_id(guid)

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key or api_key == "your_openai_api_key_here":
            raise ValueError(
                "OpenAI API key is missing or invalid. Please configure OPENAI_API_KEY in your .env file."
            )

        return self._process_item_with_openai_agent(item, uid=uid, rss_source_id=rss_source_id)

    def _process_item_with_openai_agent(
        self,
        item: Dict[str, Any],
        uid: str,
        rss_source_id: Optional[int] = None,
    ) -> StructuredJobListingSchema:
        """Executes OpenAI Agents SDK Agent and Runner on a raw feed item."""
        system_prompt = (
            "You are an expert AI Job Intelligence & ATS Parsing Agent. "
            "Your task is to analyze the provided raw RSS job posting item and extract "
            "a clean, rich, structured job listing following the output schema.\n\n"
            "Requirements:\n"
            "- Normalize job title (strip 'Hiring:', 'Company:', or location tags).\n"
            "- Extract precise hiring company name.\n"
            "- Identify remote status (is_remote: True/False) and location requirements.\n"
            "- Detect employment_type: 'full_time', 'part_time', 'contract', 'freelance', or 'internship'.\n"
            "- Detect experience_level: 'junior', 'mid', 'senior', 'lead', or 'executive'.\n"
            "- Extract numeric min_salary_usd and max_salary_usd if compensation is specified.\n"
            "- Extract all technical skills, frameworks, and programming languages into required_skills array.\n"
            "- Generate a concise 2-sentence executive job_summary."
        )

        raw_summary = item.get("summary") or item.get("content") or ""
        user_prompt = (
            f"=== RAW RSS JOB ENTRY ===\n"
            f"Headline Title: {item.get('title')}\n"
            f"Author/Company: {item.get('author')}\n"
            f"Link: {item.get('link')}\n"
            f"Categories: {', '.join(item.get('categories', []))}\n\n"
            f"Description Text:\n{raw_summary}\n"
        )

        agent = Agent(
            name="RSSJobIntelligenceAgent",
            instructions=system_prompt,
            model=self.model,
            output_type=StructuredJobListingSchema,
        )

        result = Runner.run_sync(
            starting_agent=agent,
            input=user_prompt,
        )

        parsed = result.final_output
        if not isinstance(parsed, StructuredJobListingSchema):
            if isinstance(parsed, dict):
                parsed = StructuredJobListingSchema.model_validate(parsed)
            elif isinstance(parsed, str):
                parsed = StructuredJobListingSchema.model_validate_json(parsed)

        parsed.universal_id = uid
        parsed.canonical_url = item.get("link", "")
        parsed.application_url = item.get("link", "")
        parsed.published_at = item.get("published_iso") or item.get("published")
        parsed.rss_source_id = rss_source_id

        return parsed



def process_rss_feed(
    url: str,
    rss_source_id: Optional[int] = None,
    max_items: Optional[int] = None,
    use_ai: bool = True,
) -> Dict[str, Any]:
    """
    Convenience function for processing an RSS feed URL into structured output dictionary.

    Args:
        url: The RSS feed URL.
        rss_source_id: Optional database ID reference for the RSS source feed.
        max_items: Optional maximum number of feed items to process.
        use_ai: Whether to use OpenAI Agents SDK.

    Returns:
        Structured feed output dictionary.
    """
    agent = RSSJobAgent()
    structured = agent.process_feed_url(url, rss_source_id=rss_source_id, max_items=max_items, use_ai=use_ai)
    return structured.model_dump()


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else "https://weworkremotely.com/categories/remote-full-stack-programming-jobs.rss"
    print(f"Processing RSS feed with OpenAI Agents SDK Agent: {url}\n")

    agent = RSSJobAgent()
    output = agent.process_feed_url(url, max_items=2)
    print(json.dumps(output.model_dump(), indent=2, ensure_ascii=False))
