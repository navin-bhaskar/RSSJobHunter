"""
RSS Job Processing Agent package using OpenAI Agents SDK.
"""

from .agent import RSSJobAgent, process_rss_feed
from .schema import StructuredJobListingSchema, StructuredFeedSchema

# Aliases for convenience / backward compatibility
StructuredJobListing = StructuredJobListingSchema
StructuredFeedOutput = StructuredFeedSchema

__all__ = [
    "RSSJobAgent",
    "StructuredJobListingSchema",
    "StructuredFeedSchema",
    "StructuredJobListing",
    "StructuredFeedOutput",
    "process_rss_feed",
]

