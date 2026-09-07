"""
Pydantic Schemas for ATS-compliant Resume Details.
Defines the structured output expected from LLM resume parsing.
"""

from typing import List, Optional
from pydantic import BaseModel, Field


class ContactInfo(BaseModel):
    full_name: str = Field(description="Full legal name of the candidate")
    email: Optional[str] = Field(None, description="Primary email address")
    phone: Optional[str] = Field(None, description="Contact phone number")
    location: Optional[str] = Field(None, description="City, State, Country or Remote preference")
    linkedin_url: Optional[str] = Field(None, description="LinkedIn profile URL")
    github_url: Optional[str] = Field(None, description="GitHub profile URL")
    portfolio_url: Optional[str] = Field(None, description="Personal website or portfolio URL")


class WorkExperience(BaseModel):
    job_title: str = Field(description="Job title / role name")
    company: str = Field(description="Company or organization name")
    location: Optional[str] = Field(None, description="Job location")
    start_date: Optional[str] = Field(None, description="Start date (e.g. 'Jan 2020' or '2020-01')")
    end_date: Optional[str] = Field(None, description="End date or 'Present'")
    is_current: bool = Field(False, description="Whether candidate currently works here")
    responsibilities: List[str] = Field(default_factory=list, description="Key responsibilities and bullet points")
    technologies_used: List[str] = Field(default_factory=list, description="Tech stack and tools used in this role")


class Education(BaseModel):
    degree: str = Field(description="Degree obtained or pursued (e.g., Bachelor of Science in Computer Science)")
    institution: str = Field(description="University / College / Institution name")
    location: Optional[str] = Field(None, description="Institution location")
    graduation_date: Optional[str] = Field(None, description="Graduation date or expected date")
    gpa: Optional[str] = Field(None, description="GPA or grade score if specified")
    highlights: List[str] = Field(default_factory=list, description="Honors, relevant coursework, or achievements")


class CategorizedSkills(BaseModel):
    technical_skills: List[str] = Field(default_factory=list, description="Programming languages, frameworks, libraries")
    tools_and_platforms: List[str] = Field(default_factory=list, description="IDEs, Git, Docker, AWS, OS, tools")
    databases: List[str] = Field(default_factory=list, description="SQL & NoSQL databases")
    soft_skills: List[str] = Field(default_factory=list, description="Leadership, communication, problem-solving, etc.")


class Project(BaseModel):
    name: str = Field(description="Project title")
    description: str = Field(description="Short summary of the project")
    technologies: List[str] = Field(default_factory=list, description="Technologies and frameworks used")
    url: Optional[str] = Field(None, description="Project repository or live demo URL")


class Certification(BaseModel):
    name: str = Field(description="Certification title")
    issuer: Optional[str] = Field(None, description="Issuing authority or organization")
    issue_date: Optional[str] = Field(None, description="Date issued")
    credential_id: Optional[str] = Field(None, description="Credential ID or validation URL")


class LanguageProficiency(BaseModel):
    language: str = Field(description="Language name")
    proficiency: Optional[str] = Field(None, description="Proficiency level e.g. Native, Fluent, Intermediate")


class ATSResumeSchema(BaseModel):
    """
    Complete structured ATS resume model extracted by LLM.
    """
    contact_info: ContactInfo
    professional_summary: Optional[str] = Field(None, description="Executive profile summary or objective")
    total_years_experience: Optional[float] = Field(None, description="Estimated total years of professional experience")
    work_experience: List[WorkExperience] = Field(default_factory=list)
    education: List[Education] = Field(default_factory=list)
    skills: CategorizedSkills = Field(default_factory=CategorizedSkills)
    projects: List[Project] = Field(default_factory=list)
    certifications: List[Certification] = Field(default_factory=list)
    languages: List[LanguageProficiency] = Field(default_factory=list)
    ats_keywords: List[str] = Field(default_factory=list, description="Extracted ATS keywords and domain tags")
