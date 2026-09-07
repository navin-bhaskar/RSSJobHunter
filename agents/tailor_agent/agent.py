"""
Resume Tailoring Agent using OpenAI Agents SDK.
Rewrites a candidate's structured resume to emphasize the skills, experience,
and language most relevant to a specific target job posting, without
fabricating any facts.
"""

import os
import sys
from typing import Optional
from dotenv import load_dotenv


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


class TailorAgent:
    """
    Agent that rewrites a candidate's structured resume to target a specific
    job posting, emphasizing relevant skills and experience while preserving
    all factual details (names, companies, dates, degrees, titles).
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

    def tailor_resume(
        self,
        resume_data: ATSResumeSchema,
        job_description: str,
        job_title: Optional[str] = None,
        company_name: Optional[str] = None,
    ) -> ATSResumeSchema:
        """
        Rewrites a structured resume to target the given job posting.

        Args:
            resume_data: The candidate's base structured resume (ATSResumeSchema).
            job_description: Full text of the target job posting.
            job_title: Optional title of the target position.
            company_name: Optional hiring company name.

        Returns:
            A new ATSResumeSchema with professional_summary, work_experience
            responsibilities, and skills ordering rewritten to target the job,
            with all factual details preserved unchanged.
        """
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key or api_key == "your_openai_api_key_here":
            raise ValueError(
                "OpenAI API key is missing or invalid. Please configure OPENAI_API_KEY in your .env file."
            )

        resume_json_str = resume_data.model_dump_json(indent=2)

        system_prompt = (
            "You are an elite resume writer and ATS optimization specialist. "
            "Your task is to rewrite a candidate's structured resume to target a specific "
            "job posting, following the exact same output schema as the input.\n\n"
            "Rules:\n"
            "- NEVER fabricate or alter facts: contact_info, company names, job titles, "
            "employment dates, education, and certifications must remain exactly as given.\n"
            "- Rewrite `professional_summary` to highlight the 2-3 qualifications most "
            "relevant to this specific job posting.\n"
            "- Rewrite each work_experience entry's `responsibilities` bullet points to "
            "emphasize achievements and technologies relevant to the target job, using "
            "the job posting's own terminology where truthfully applicable. Do not invent "
            "responsibilities the candidate did not have.\n"
            "- Reorder `skills` categories (technical_skills, tools_and_platforms, "
            "databases, soft_skills) so the skills most relevant to this job appear first "
            "within each list. Do not add skills not present in the original resume.\n"
            "- Leave `education`, `projects`, `certifications`, `languages`, and "
            "`total_years_experience` unchanged unless correcting an obvious extraction error."
        )

        user_prompt = (
            f"=== TARGET JOB POSTING ===\n"
            f"Role Title: {job_title or 'Not specified'}\n"
            f"Company: {company_name or 'Not specified'}\n\n"
            f"Job Description:\n{job_description}\n\n"
            f"=== CANDIDATE'S BASE RESUME (STRUCTURED ATS FORMAT) ===\n"
            f"{resume_json_str}\n\n"
            f"Rewrite this resume to target the job posting above, following the rules exactly."
        )

        agent = Agent(
            name="ResumeTailorAgent",
            instructions=system_prompt,
            model=self.model,
            output_type=ATSResumeSchema,
        )

        result = Runner.run_sync(
            starting_agent=agent,
            input=user_prompt,
        )

        parsed = result.final_output
        if not isinstance(parsed, ATSResumeSchema):
            if isinstance(parsed, dict):
                parsed = ATSResumeSchema.model_validate(parsed)
            elif isinstance(parsed, str):
                parsed = ATSResumeSchema.model_validate_json(parsed)

        return parsed


def tailor_resume_for_job(
    resume_data: ATSResumeSchema,
    job_description: str,
    job_title: Optional[str] = None,
    company_name: Optional[str] = None,
) -> ATSResumeSchema:
    """
    Convenience function to run the Resume Tailor Agent.
    """
    agent = TailorAgent()
    return agent.tailor_resume(
        resume_data=resume_data,
        job_description=job_description,
        job_title=job_title,
        company_name=company_name,
    )


if __name__ == "__main__":
    import json as _json

    if len(sys.argv) < 3:
        print("Usage: python -m agents.tailor_agent.agent <resume_json_path> <job_description_txt_path>")
        sys.exit(1)

    with open(sys.argv[1], "r", encoding="utf-8") as f:
        sample_resume = ATSResumeSchema.model_validate(_json.load(f))
    with open(sys.argv[2], "r", encoding="utf-8") as f:
        sample_jd = f.read()

    tailored = tailor_resume_for_job(sample_resume, sample_jd)
    print("=== Tailored Resume ===")
    print(tailored.model_dump_json(indent=2))
