"""
Resume Parsing Agent using OpenAI Agents SDK and Structured Outputs.
Extracts ATS-standard structured resume details from PDF resumes with SHA-256 caching.
"""

import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
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
        model_settings_cls = _sdk.ModelSettings
        return agent_cls, runner_cls, model_settings_cls
    finally:
        sys.path = orig_path
        if saved_agents_mod is not None:
            sys.modules["agents"] = saved_agents_mod

Agent, Runner, ModelSettings = _get_openai_agents_sdk()

# Bounds each individual LLM call attempt; the openai client's own default (600s,
# 2 retries) otherwise leaves a stalled network call looking hung for up to ~20 minutes.
MODEL_CALL_TIMEOUT_SECONDS = 60




from tools.pdf_reader import read_pdf
from .schema import ATSResumeSchema
from .cache import ResumeCache



class ResumeAgent:
    """Agent that parses resume PDFs into structured ATS data using OpenAI Agents SDK."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        cache_dir: str | Path = ".cache"
    ):
        load_dotenv()

        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.model = model or os.getenv("OPENAI_MODEL", "gpt-4o")
        self.base_url = base_url or os.getenv("OPENAI_BASE_URL")

        if self.api_key and self.api_key != "your_openai_api_key_here":
            os.environ["OPENAI_API_KEY"] = self.api_key
        if self.base_url:
            os.environ["OPENAI_BASE_URL"] = self.base_url

        self.cache = ResumeCache(Path(cache_dir) / "resume_cache.json")

    def parse_resume(
        self,
        pdf_path: Optional[str | Path] = None,
        force_refresh: bool = False
    ) -> Tuple[ATSResumeSchema, bool]:
        """
        Parses a PDF resume file and returns structured ATS details using OpenAI Agents SDK.

        Args:
            pdf_path: Path to PDF resume file. If None, checks RESUME env var or 'resume.pdf'.
            force_refresh: If True, bypasses cache and re-runs LLM extraction.

        Returns:
            Tuple of (ATSResumeSchema, from_cache: bool)
        """
        if pdf_path is None:
            pdf_path = os.getenv("RESUME_PATH") or os.getenv("RESUME", "resume.pdf")

        file_path = Path(pdf_path)
        if not file_path.exists():
            raise FileNotFoundError(f"Resume PDF file not found at: {file_path}")

        # Compute SHA-256 hash of PDF file
        file_hash = self.cache.calculate_file_hash(file_path)

        # Check cache if not forcing refresh
        if not force_refresh:
            cached_data = self.cache.get(file_hash)
            if cached_data:
                parsed_schema = ATSResumeSchema.model_validate(cached_data)
                return parsed_schema, True

        # Extract text from PDF using tool.pdf_reader
        raw_text = read_pdf(str(file_path))
        if not raw_text.strip():
            raise ValueError(f"Extracted empty text from PDF: {file_path}")

        # Ensure API key is configured before calling Runner
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key or api_key == "your_openai_api_key_here":
            raise ValueError(
                "OpenAI API key is missing or invalid. "
                "Please configure OPENAI_API_KEY in your .env file or environment."
            )

        # Define Agent using OpenAI Agents SDK
        system_prompt = (
            "You are an expert ATS (Applicant Tracking System) Resume Parser. "
            "Your task is to analyze the candidate's resume text and extract all relevant "
            "details into a structured format following the output schema provided. "
            "Ensure accuracy, retain exact technology names, dates, companies, and roles."
        )

        agent = Agent(
            name="ATSResumeParserAgent",
            instructions=system_prompt,
            model=self.model,
            output_type=ATSResumeSchema,
            model_settings=ModelSettings(timeout=MODEL_CALL_TIMEOUT_SECONDS),
        )

        # Execute agent synchronously via OpenAI Agents SDK Runner
        result = Runner.run_sync(
            starting_agent=agent,
            input=f"Parse the following resume text:\n\n{raw_text}"
        )

        # Extract structured output from agent result
        parsed_output = result.final_output
        if not isinstance(parsed_output, ATSResumeSchema):
            if isinstance(parsed_output, dict):
                parsed_output = ATSResumeSchema.model_validate(parsed_output)
            elif isinstance(parsed_output, str):
                parsed_output = ATSResumeSchema.model_validate_json(parsed_output)

        # Store result in cache
        self.cache.set(
            file_hash=file_hash,
            file_name=file_path.name,
            data=parsed_output.model_dump()
        )

        return parsed_output, False


def parse_resume_ats(
    pdf_path: Optional[str | Path] = None,
    force_refresh: bool = False
) -> Dict[str, Any]:
    """
    Convenience function to parse a resume PDF and return dictionary output along with metadata.
    """
    agent = ResumeAgent()
    parsed_schema, from_cache = agent.parse_resume(pdf_path=pdf_path, force_refresh=force_refresh)
    result = parsed_schema.model_dump()
    result["_metadata"] = {
        "from_cache": from_cache,
        "model_used": agent.model
    }
    return result


if __name__ == "__main__":
    import sys
    import json

    target_file = sys.argv[1] if len(sys.argv) > 1 else None
    agent = ResumeAgent()
    try:
        data, cached = agent.parse_resume(pdf_path=target_file)
        status = "Cached result (no LLM call)" if cached else f"Freshly generated via OpenAI Agents SDK ({agent.model})"
        print(f"=== Resume Parsed ({status}) ===")
        print(json.dumps(data.model_dump(), indent=2))
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
