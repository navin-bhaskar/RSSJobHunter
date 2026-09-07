"""
Tools package for RSS Job Hunter.
"""

from .pdf_reader import PDFReader, PDFReaderError, read_pdf, read_pdf_pages, get_pdf_info
from .job_extractor import (
    JobExtractor,
    JobDetails,
    JobExtractorError,
    extract_job_description,
    get_job_description_text,
)
from .rss_reader import (
    RSSReader,
    RSSReaderError,
    fetch_rss_feed,
    parse_rss,
    get_rss_job_items,
    get_universal_id,
)

__all__ = [
    "PDFReader",
    "PDFReaderError",
    "read_pdf",
    "read_pdf_pages",
    "get_pdf_info",
    "JobExtractor",
    "JobDetails",
    "JobExtractorError",
    "extract_job_description",
    "get_job_description_text",
    "RSSReader",
    "RSSReaderError",
    "fetch_rss_feed",
    "parse_rss",
    "get_rss_job_items",
    "get_universal_id",
]
