"""
Pydantic Schemas for ATS Matching Evaluation.
Defines structured output comparing a candidate's resume with a target job description.
"""

from typing import List, Optional
from pydantic import BaseModel, Field


class KeywordMatch(BaseModel):
    """Keyword overlap and gap analysis."""
    matched_keywords: List[str] = Field(
        default_factory=list,
        description="Key terms, tools, methodologies, and technologies found in both the resume and the job posting",
    )
    missing_keywords: List[str] = Field(
        default_factory=list,
        description="Important keywords or requirements from the job posting that are missing or weak in the resume",
    )


class ExperienceEvaluation(BaseModel):
    """Analysis of candidate seniority and experience level against job requirements."""
    years_required: Optional[str] = Field(
        None,
        description="Years of experience or seniority tier expected by the job (e.g., '10+ years', '5-7 years', 'Senior')",
    )
    candidate_years: Optional[float] = Field(
        None,
        description="Estimated total years of relevant experience from candidate's resume",
    )
    meets_experience_criteria: bool = Field(
        description="True if the candidate generally meets or exceeds the required seniority and experience level",
    )
    commentary: str = Field(
        description="Analysis of how well the candidate's career progression and achievements align with this role",
    )


class LocationEvaluation(BaseModel):
    """Analysis of whether the candidate's location satisfies the job's location/timezone/work-authorization requirement."""
    job_location_requirement: Optional[str] = Field(
        None,
        description="The job's stated location, timezone, or work-authorization requirement (e.g. 'Remote (US only)', 'Worldwide')",
    )
    is_location_compatible: bool = Field(
        description="True if the candidate's location plausibly satisfies the job's location/timezone/work-authorization requirement",
    )
    commentary: str = Field(
        description="Explanation of any location, timezone, or work-authorization mismatch and its impact on fit",
    )


class ATSMatchResult(BaseModel):
    """
    Complete structured ATS matching evaluation between candidate resume and job description.
    """
    match_score: int = Field(
        description="Overall ATS compatibility match score from 0 to 100",
        ge=0,
        le=100,
    )
    fit_level: str = Field(
        description="High-level category: 'Strong Match' (80-100), 'Good Match' (65-79), 'Moderate Match' (50-64), or 'Low Match' (0-49)",
    )
    role_title: Optional[str] = Field(
        None,
        description="Target job title being matched",
    )
    company_name: Optional[str] = Field(
        None,
        description="Target company name if available",
    )
    executive_summary: str = Field(
        description="A concise 2-4 sentence executive summary evaluating the candidate's fit for this role",
    )
    matching_skills: List[str] = Field(
        default_factory=list,
        description="Skills, platforms, and qualifications from the resume that directly satisfy the job requirements",
    )
    missing_skills: List[str] = Field(
        default_factory=list,
        description="Skills, tools, or domain requirements from the job description not evident or weak in the resume",
    )
    experience_evaluation: ExperienceEvaluation
    location_evaluation: LocationEvaluation
    key_strengths: List[str] = Field(
        default_factory=list,
        description="Top 3-5 strongest selling points and competitive advantages of the candidate for this specific role",
    )
    gap_areas: List[str] = Field(
        default_factory=list,
        description="Critical gaps, missing qualifications, or areas likely to be questioned during interviews",
    )
    tailoring_recommendations: List[str] = Field(
        default_factory=list,
        description="Actionable advice on how to tailor the resume bullet points, skills list, and summary for this role",
    )
    interview_preparation_topics: List[str] = Field(
        default_factory=list,
        description="High-priority technical and behavioral interview topics/questions expected for this role",
    )
    keyword_analysis: KeywordMatch
