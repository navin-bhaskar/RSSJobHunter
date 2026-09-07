"""
Agents package for RSS Job Hunter.
"""

from .rss_agent import (
    RSSJobAgent,
    StructuredJobListingSchema,
    StructuredFeedSchema,
    StructuredJobListing,
    StructuredFeedOutput,
    process_rss_feed,
)
from .resume_agent import ResumeAgent, parse_resume_ats
from .matcher_agent import MatcherAgent, match_resume_to_job

__all__ = [
    "RSSJobAgent",
    "StructuredJobListingSchema",
    "StructuredFeedSchema",
    "StructuredJobListing",
    "StructuredFeedOutput",
    "process_rss_feed",
    "ResumeAgent",
    "parse_resume_ats",
    "MatcherAgent",
    "match_resume_to_job",
]

