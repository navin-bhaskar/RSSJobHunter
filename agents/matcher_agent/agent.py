"""
ATS Matcher Agent using OpenAI Agents SDK.
Compares structured candidate resume data against a target job description
and generates an in-depth, structured ATS matching evaluation and score.
"""

import json
import os
import sys
from typing import Any, Dict, Optional
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




from agents.resume_agent.schema import ATSResumeSchema
from .schema import ATSMatchResult


class MatcherAgent:
    """
    Agent that analyzes candidate resume details against a job description
    to produce structured ATS compatibility scores, strengths, gaps, and recommendations.
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

    def evaluate_match(
        self,
        resume_data: ATSResumeSchema | Dict[str, Any] | str,
        job_description: str,
        job_title: Optional[str] = None,
        company_name: Optional[str] = None,
        job_location: Optional[str] = None,
        is_remote: Optional[bool] = None,
    ) -> ATSMatchResult:
        """
        Executes ATS matching analysis between candidate resume and job description.

        Args:
            resume_data: Structured resume output (ATSResumeSchema instance, dict, or JSON str).
            job_description: Full job description text.
            job_title: Optional title of the position.
            company_name: Optional hiring company name.
            job_location: Optional location/timezone/work-authorization requirement stated
                by the posting (e.g. "Remote (US only)"), from RSSJobAgent's structured output.
            is_remote: Optional flag for whether the posting allows remote work at all.

        Returns:
            ATSMatchResult instance containing score (0-100), breakdown, and recommendations.
        """
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key or api_key == "your_openai_api_key_here":
            raise ValueError(
                "OpenAI API key is missing or invalid. Please configure OPENAI_API_KEY in your .env file."
            )

        # Format resume data as JSON string for LLM input
        if isinstance(resume_data, ATSResumeSchema):
            resume_json_str = json.dumps(resume_data.model_dump(), indent=2, ensure_ascii=False)
            candidate_location = resume_data.contact_info.location
        elif isinstance(resume_data, dict):
            resume_json_str = json.dumps(resume_data, indent=2, ensure_ascii=False)
            candidate_location = (resume_data.get("contact_info") or {}).get("location")
        else:
            resume_json_str = str(resume_data)
            candidate_location = None

        system_prompt = (
            "You are an elite Applicant Tracking System (ATS) algorithm and Principal Technical Recruiter. "
            "Your task is to conduct an unbiased, rigorous evaluation of a candidate's structured resume "
            "against the target job posting description.\n\n"
            "Scoring Guidelines (match_score 0-100):\n"
            "- 90-100: Exceptional fit; possesses all must-haves, direct seniority match, and deep stack overlap.\n"
            "- 75-89: Strong fit; satisfies 80%+ requirements, minor gaps in secondary nice-to-haves.\n"
            "- 60-74: Moderate fit; solid transferable foundation but missing several core technologies or domain depth.\n"
            "- <60: Significant skill/experience disconnect.\n\n"
            "Location/timezone/work-authorization fit is a significant scoring factor, not a minor nice-to-have: "
            "many jobs labeled 'remote' still require the candidate to be physically located in a specific "
            "country, region, or timezone band (for legal work authorization or working-hours overlap). If the "
            "job posting states or implies such a requirement and the candidate's location does not plausibly "
            "satisfy it, treat this as a material gap and reduce match_score accordingly, even if the "
            "candidate's skills are an otherwise excellent fit. If the job is open to any location, or no "
            "location requirement is stated, this should not penalize the score.\n\n"
            "Provide insightful, high-value feedback including matching skills, missing skills, "
            "experience seniority evaluation, location/timezone compatibility, and high-impact resume "
            "tailoring recommendations."
        )

        user_prompt = (
            f"=== TARGET JOB POSTING ===\n"
            f"Role Title: {job_title or 'Not specified'}\n"
            f"Company: {company_name or 'Not specified'}\n"
            f"Location/Timezone Requirement: {job_location or 'Not specified'}\n"
            f"Remote-eligible: {is_remote if is_remote is not None else 'Not specified'}\n\n"
            f"Job Description:\n{job_description}\n\n"
            f"=== CANDIDATE RESUME DATA (STRUCTURED ATS FORMAT) ===\n"
            f"Candidate Location: {candidate_location or 'Not specified'}\n\n"
            f"{resume_json_str}\n\n"
            f"Evaluate the candidate against this job description and produce the structured ATSMatchResult, "
            f"including an explicit location_evaluation comparing the candidate's location against the job's "
            f"location/timezone requirement above."
        )

        agent = Agent(
            name="ATSMatcherAgent",
            instructions=system_prompt,
            model=self.model,
            output_type=ATSMatchResult,
        )

        result = Runner.run_sync(
            starting_agent=agent,
            input=user_prompt,
        )

        parsed_output = result.final_output
        if not isinstance(parsed_output, ATSMatchResult):
            if isinstance(parsed_output, dict):
                parsed_output = ATSMatchResult.model_validate(parsed_output)
            elif isinstance(parsed_output, str):
                parsed_output = ATSMatchResult.model_validate_json(parsed_output)

        return parsed_output


def match_resume_to_job(
    resume_data: ATSResumeSchema | Dict[str, Any] | str,
    job_description: str,
    job_title: Optional[str] = None,
    company_name: Optional[str] = None,
    job_location: Optional[str] = None,
    is_remote: Optional[bool] = None,
) -> ATSMatchResult:
    """
    Convenience function to run the ATS Matcher Agent.
    """
    matcher = MatcherAgent()
    return matcher.evaluate_match(
        resume_data=resume_data,
        job_description=job_description,
        job_title=job_title,
        company_name=company_name,
        job_location=job_location,
        is_remote=is_remote,
    )


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python -m agents.matcher_agent.agent <resume_json_path> <job_description_txt_path>")
        sys.exit(1)

    with open(sys.argv[1], "r", encoding="utf-8") as f:
        sample_resume = f.read()
    with open(sys.argv[2], "r", encoding="utf-8") as f:
        sample_jd = f.read()

    res = match_resume_to_job(sample_resume, sample_jd)
    print("=== ATS Match Result ===")
    print(f"Match Score: {res.match_score}/100 ({res.fit_level})")
    print(f"Summary: {res.executive_summary}")
    print(f"Matching Skills ({len(res.matching_skills)}): {', '.join(res.matching_skills[:5])}")
    print(f"Missing Skills ({len(res.missing_skills)}): {', '.join(res.missing_skills[:5])}")
