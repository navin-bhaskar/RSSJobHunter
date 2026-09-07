"""
Pydantic Schemas for RSS Job Processing Agent.
Defines the structured output expected from OpenAI Agents SDK LLM parsing.
"""

from typing import List, Optional
from pydantic import BaseModel, Field


class StructuredJobListingSchema(BaseModel):
    """Structured ATS-friendly job details extracted by LLM from raw RSS entry."""
    universal_id: Optional[str] = Field(None, description="SHA-256 unique item hash")
    title: str = Field(description="Normalized clean job title (e.g. 'Senior Full Stack Engineer')")
    company: str = Field(description="Extracted hiring company name (e.g. 'Lemon.io', 'Airbnb')")
    location: str = Field(description="Location or timezone requirements (e.g. 'Remote (US/EU)', 'Worldwide')")
    is_remote: bool = Field(True, description="True if the job is 100% remote or allows remote work")
    employment_type: str = Field(
        "full_time",
        description="Job employment type: 'full_time', 'part_time', 'contract', 'freelance', or 'internship'"
    )
    experience_level: str = Field(
        "senior",
        description="Experience level: 'junior', 'mid', 'senior', 'lead', or 'executive'"
    )
    salary_range: Optional[str] = Field(None, description="Raw compensation string if mentioned (e.g. '$120,000 - $150,000')")
    min_salary_usd: Optional[float] = Field(None, description="Numeric minimum annual salary in USD if detectable")
    max_salary_usd: Optional[float] = Field(None, description="Numeric maximum annual salary in USD if detectable")
    required_skills: List[str] = Field(
        default_factory=list,
        description="List of key technical skills, languages, and frameworks (e.g. ['Python', 'React', 'AWS'])"
    )
    job_summary: str = Field("", description="Executive 2-sentence summary of the job responsibilities and company")
    clean_description: str = Field("", description="Clean plain-text job description free of HTML markup")
    canonical_url: str = Field("", description="Canonical job URL link")
    application_url: str = Field("", description="Direct application or job detail URL link")
    published_at: Optional[str] = Field(None, description="ISO-8601 publication timestamp")
    rss_source_id: Optional[int] = Field(None, description="Optional database reference ID for the RSS source feed")

    def to_dict(self) -> dict:
        """Converts model to dictionary."""
        return self.model_dump()

    def to_json(self, indent: int = 2) -> str:
        """Converts model to formatted JSON string."""
        return self.model_dump_json(indent=indent)


class StructuredFeedSchema(BaseModel):
    """Batch feed processing output containing list of structured job listings."""
    feed_title: str = Field(description="Title of the RSS feed")
    feed_url: str = Field(description="Source feed URL link")
    processed_at: str = Field(description="ISO-8601 processing timestamp")
    total_raw_items: int = Field(description="Total raw entries received")
    total_structured_items: int = Field(description="Total successfully parsed job listings")
    jobs: List[StructuredJobListingSchema] = Field(default_factory=list, description="List of structured job listings")

    def to_dict(self) -> dict:
        """Converts model to dictionary."""
        return self.model_dump()

    def to_json(self, indent: int = 2) -> str:
        """Converts model to formatted JSON string."""
        return self.model_dump_json(indent=indent)
