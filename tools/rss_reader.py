"""
RSS Feed Reader Tool for fetching, parsing, and filtering RSS, Atom, and JSON feeds.
Designed for generic feed aggregation, content analysis, and AI Agent integrations.
"""

from datetime import datetime, timezone
import hashlib
import json
import logging
from typing import Any, Dict, List, Optional
import time
from urllib.parse import urlparse

from bs4 import BeautifulSoup
import feedparser
import requests


def get_universal_id(guid_or_url: str) -> str:
    """Generates a deterministic SHA-256 universal unique ID hash for a feed item GUID or URL."""
    if not guid_or_url:
        return ""
    return hashlib.sha256(guid_or_url.strip().encode("utf-8")).hexdigest()


try:
    from curl_cffi import requests as curl_requests
    HAS_CURL_CFFI = True
except ImportError:
    HAS_CURL_CFFI = False

logger = logging.getLogger(__name__)

# Standard browser headers to ensure RSS fetch requests pass generic user-agent filters
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/atom+xml, application/json, application/xml, text/xml, */*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


class RSSReaderError(Exception):
    """Custom exception raised for RSS fetching and parsing errors."""
    pass


class RSSReader:
    """Generic RSS, Atom, and JSON feed reader utility for fetching, parsing, and formatting feed data."""

    def __init__(self, timeout: int = 15, headers: Optional[Dict[str, str]] = None):
        self.timeout = timeout
        self.headers = headers or DEFAULT_HEADERS

    def fetch_feed(self, url: str, max_items: Optional[int] = None) -> Dict[str, Any]:
        """
        Fetches an RSS, Atom, or JSON feed from a given URL and parses its contents.

        Args:
            url: The HTTP/HTTPS URL of the RSS/Atom/JSON feed.
            max_items: Optional maximum number of feed items to return.

        Returns:
            Dictionary containing feed metadata and list of parsed items.
        """
        if not url or not isinstance(url, str):
            raise ValueError("A valid URL string must be provided.")

        parsed_url = urlparse(url)
        if not parsed_url.scheme or not parsed_url.netloc:
            raise ValueError(f"Invalid URL structure: '{url}'")

        content, status_code = self._get_url_content(url)

        # Check for Cloudflare/bot challenge response
        if status_code == 403 or (b"challenge-running" in content or b"Just a moment..." in content):
            raise RSSReaderError(
                f"Access forbidden (HTTP {status_code} / Bot Protection) when accessing '{url}'. "
                "The server requires automated bot verification or a specialized API key."
            )

        # Check if response is JSON (JSON Feed specification standard or generic JSON array)
        try:
            json_data = json.loads(content.decode("utf-8"))
            if isinstance(json_data, (dict, list)):
                return self.parse_json_feed(json_data, source_url=url, max_items=max_items)
        except Exception:
            pass  # Fall through to XML / RSS feedparser

        return self.parse_content(content, source_url=url, max_items=max_items)

    def _get_url_content(self, url: str) -> tuple[bytes, int]:
        """Attempts fetching URL content via standard HTTP requests, with browser impersonation fallback if available."""
        status_code = 0
        content = b""

        # 1. Try standard HTTP requests
        try:
            response = requests.get(url, headers=self.headers, timeout=self.timeout)
            status_code = response.status_code
            content = response.content
            if response.ok:
                return content, status_code
        except requests.RequestException as e:
            logger.warning(f"Standard requests failed for '{url}': {e}")

        # 2. Try browser TLS impersonation (curl_cffi) if available
        if HAS_CURL_CFFI:
            try:
                c_resp = curl_requests.get(url, headers=self.headers, timeout=self.timeout, impersonate="chrome")
                status_code = c_resp.status_code
                content = c_resp.content
                if c_resp.ok:
                    return content, status_code
            except Exception as ce:
                logger.warning(f"curl_cffi failed for '{url}': {ce}")

        if status_code and status_code != 200:
            raise RSSReaderError(f"HTTP {status_code} error fetching feed from '{url}'")

        if not content:
            raise RSSReaderError(f"Failed to retrieve content from feed URL '{url}'")

        return content, status_code

    def parse_json_feed(
        self,
        json_data: Dict[str, Any] | List[Any],
        source_url: Optional[str] = None,
        max_items: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Parses JSON Feed spec standard (https://jsonfeed.org) or generic JSON feed/API structures.

        Note on Cloudflare-protected feeds:
        Some RSS XML endpoints (e.g., 'https://remotive.com/remote-jobs/feed/software-development')
        are guarded by Cloudflare JS Bot Challenges which block automated RSS fetchers with HTTP 403.
        For such services, passing their public open JSON API endpoint
        (e.g., 'https://remotive.com/api/remote-jobs?category=software-dev') into this function
        allows fetching and normalizing the job postings into the exact same standardized RSS dictionary format.
        """
        if isinstance(json_data, list):
            feed_title = "JSON Feed"
            feed_description = ""
            feed_link = source_url or ""
            raw_items = json_data
            feed_image = None
        else:
            feed_title = json_data.get("title", "JSON Feed")
            feed_description = json_data.get("description", "")
            feed_link = json_data.get("home_page_url", json_data.get("link", source_url or ""))
            feed_image = json_data.get("icon", json_data.get("favicon"))
            # Extracts items from standard 'items' array or job board API keys like 'jobs' or 'entries'
            raw_items = json_data.get("items", json_data.get("entries", json_data.get("jobs", [])))

        items: List[Dict[str, Any]] = []
        limit = len(raw_items) if max_items is None else min(max_items, len(raw_items))

        for item in raw_items[:limit]:
            if not isinstance(item, dict):
                continue

            raw_summary = item.get("summary", item.get("description", item.get("content_text", "")))
            clean_summary = self._clean_html(str(raw_summary))

            author_val = item.get("author", item.get("author_detail", ""))
            author_str = author_val.get("name", "") if isinstance(author_val, dict) else str(author_val)

            tags = item.get("tags", item.get("categories", []))
            categories = tags if isinstance(tags, list) else [str(tags)]

            item_id = str(item.get("id", item.get("url", item.get("guid", ""))))
            items.append({
                "id": item_id,
                "universal_id": get_universal_id(item_id),
                "title": item.get("title", "Untitled Item"),
                "link": item.get("url", item.get("link", "")),
                "published": item.get("date_published", item.get("publication_date", item.get("published", ""))),
                "published_iso": item.get("date_published", item.get("publication_date", item.get("published", ""))),
                "author": author_str,
                "summary": clean_summary,
                "content": self._clean_html(item.get("content_html", clean_summary)),
                "categories": categories,
                "enclosures": [],
            })

        return {
            "feed_title": feed_title,
            "feed_description": feed_description,
            "feed_link": feed_link,
            "feed_image": feed_image,
            "source_url": source_url,
            "total_items": len(items),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "items": items,
        }

    def parse_content(
        self,
        content: str | bytes,
        source_url: Optional[str] = None,
        max_items: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Parses raw RSS/Atom XML string or bytes content.

        Args:
            content: Raw XML string or bytes.
            source_url: Optional source URL for metadata reference.
            max_items: Optional maximum number of feed items to return.

        Returns:
            Structured dictionary of feed information and items.
        """
        try:
            parsed = feedparser.parse(content)
        except Exception as e:
            raise RSSReaderError(f"Failed to parse RSS content: {str(e)}") from e

        if parsed.bozo and parsed.bozo_exception:
            logger.warning(f"Feedparser warning for {source_url or 'content'}: {parsed.bozo_exception}")

        feed_info = parsed.get("feed", {})
        entries = parsed.get("entries", [])

        feed_title = feed_info.get("title", "Untitled Feed")
        feed_description = self._clean_html(feed_info.get("description", feed_info.get("subtitle", "")))
        feed_link = feed_info.get("link", source_url or "")
        feed_image = feed_info.get("image", {}).get("href") if feed_info.get("image") else None

        items: List[Dict[str, Any]] = []
        limit = len(entries) if max_items is None else min(max_items, len(entries))

        for entry in entries[:limit]:
            items.append(self._parse_entry(entry))

        return {
            "feed_title": feed_title,
            "feed_description": feed_description,
            "feed_link": feed_link,
            "feed_image": feed_image,
            "source_url": source_url,
            "total_items": len(items),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "items": items,
        }

    def _parse_entry(self, entry: Any) -> Dict[str, Any]:
        """Extracts and normalizes fields from a single feed entry."""
        guid = entry.get("id", entry.get("guid", entry.get("link", "")))
        title = entry.get("title", "Untitled Post").strip()
        link = entry.get("link", "")
        author = entry.get("author", entry.get("author_detail", {}).get("name", ""))

        # Summary and Content extraction
        raw_summary = entry.get("summary", entry.get("description", ""))
        clean_summary = self._clean_html(raw_summary)

        # Full content if available (Atom feeds / Content encoded)
        full_content = ""
        if "content" in entry and isinstance(entry["content"], list) and len(entry["content"]) > 0:
            full_content = self._clean_html(entry["content"][0].get("value", ""))

        # Publication date handling
        published_raw = entry.get("published", entry.get("updated", ""))
        published_iso = self._format_date(entry)

        # Categories / Tags
        categories = []
        tags = entry.get("tags", [])
        if tags:
            for tag in tags:
                term = tag.get("term") or tag.get("label")
                if term and term not in categories:
                    categories.append(term)

        # Enclosures (e.g. podcasts, attachments)
        enclosures = []
        for enc in entry.get("enclosures", []):
            if isinstance(enc, dict):
                enclosures.append({
                    "url": enc.get("href", enc.get("url", "")),
                    "type": enc.get("type", ""),
                    "length": enc.get("length", "")
                })

        return {
            "id": guid,
            "universal_id": get_universal_id(guid),
            "title": title,
            "link": link,
            "published": published_raw,
            "published_iso": published_iso,
            "author": author,
            "summary": clean_summary,
            "content": full_content or clean_summary,
            "categories": categories,
            "enclosures": enclosures,
        }

    @staticmethod
    def _clean_html(html_text: str) -> str:
        """Strips HTML tags and normalizes whitespace."""
        if not html_text:
            return ""
        try:
            soup = BeautifulSoup(html_text, "html.parser")
            text = soup.get_text(separator=" ", strip=True)
            return " ".join(text.split())
        except Exception:
            return html_text.strip()

    @staticmethod
    def _format_date(entry: Any) -> Optional[str]:
        """Converts feed entry date to ISO-8601 string if parsed date tuple exists."""
        parsed_tuple = entry.get("published_parsed") or entry.get("updated_parsed")
        if parsed_tuple:
            try:
                dt = datetime.fromtimestamp(time.mktime(parsed_tuple), tz=timezone.utc)
                return dt.isoformat()
            except Exception:
                pass
        return None


# Convenience functions suitable for OpenAI / AI Agent tool registration

def fetch_rss_feed(url: str, max_items: Optional[int] = None) -> Dict[str, Any]:
    """
    Fetch and parse an RSS, Atom, or JSON feed from a given URL. Suitable as an AI Agent tool.

    Args:
        url: URL of the RSS/Atom/JSON feed.
        max_items: Optional limit on the number of feed items to return.

    Returns:
        Structured dictionary containing feed metadata and list of items.
    """
    reader = RSSReader()
    return reader.fetch_feed(url, max_items=max_items)


def parse_rss(content: str, max_items: Optional[int] = None) -> Dict[str, Any]:
    """
    Parse RSS XML text content directly. Suitable as an AI Agent tool.

    Args:
        content: Raw XML string of an RSS feed.
        max_items: Optional limit on the number of feed items to return.

    Returns:
        Structured dictionary containing feed metadata and list of items.
    """
    reader = RSSReader()
    return reader.parse_content(content, max_items=max_items)


def get_rss_job_items(
    url: str,
    keyword: Optional[str] = None,
    max_items: Optional[int] = None
) -> List[Dict[str, Any]]:
    """
    Fetch RSS feed items and optionally filter them by a keyword search in title or summary.

    Args:
        url: RSS feed URL.
        keyword: Optional search keyword (case-insensitive) to filter items.
        max_items: Optional max items limit.

    Returns:
        List of matching feed item dictionaries.
    """
    feed_data = fetch_rss_feed(url, max_items=None)
    items = feed_data.get("items", [])

    if keyword:
        kw_lower = keyword.lower()
        items = [
            item for item in items
            if kw_lower in item["title"].lower()
            or kw_lower in item["summary"].lower()
            or any(kw_lower in cat.lower() for cat in item.get("categories", []))
        ]

    if max_items is not None:
        items = items[:max_items]

    return items


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m tool.rss_reader <rss_feed_url> [max_items]")
        sys.exit(1)

    feed_url = sys.argv[1]
    limit_arg = int(sys.argv[2]) if len(sys.argv) > 2 else 5

    print(f"Fetching RSS feed from: {feed_url} (max_items={limit_arg})...\n")
    try:
        data = fetch_rss_feed(feed_url, max_items=limit_arg)
        print(f"=== {data['feed_title']} ===")
        print(f"Description : {data['feed_description']}")
        print(f"Feed Link   : {data['feed_link']}")
        print(f"Fetched Items: {data['total_items']}\n")

        for idx, item in enumerate(data["items"], 1):
            print(f"[{idx}] {item['title']}")
            print(f"    Link: {item['link']}")
            print(f"    Date: {item['published_iso'] or item['published']}")
            if item.get("author"):
                print(f"    Author/Company: {item['author']}")
            if item.get("categories"):
                print(f"    Tags: {', '.join(item['categories'])}")
            summary_preview = item['summary'][:150] + ("..." if len(item['summary']) > 150 else "")
            print(f"    Summary: {summary_preview}\n")
    except Exception as err:
        print(f"Error reading RSS feed: {err}", file=sys.stderr)
        sys.exit(1)
