"""
Renders a structured ATSResumeSchema resume to a single-column, ATS-friendly PDF.
Uses a Jinja2 HTML/CSS template converted to PDF via xhtml2pdf (pure Python, no
native binary dependencies), so it installs cleanly on any platform.
"""

import html
from pathlib import Path
from typing import Union

from jinja2 import Environment, BaseLoader
from xhtml2pdf import pisa

from agents.resume_agent.schema import ATSResumeSchema


class ResumePDFError(Exception):
    """Raised when resume PDF rendering fails."""


_TEMPLATE_HTML = """
<html>
<head>
<style>
    @page { size: letter; margin: 0.6in; }
    body { font-family: Helvetica, Arial, sans-serif; font-size: 10pt; color: #1a1a1a; }
    h1 { font-size: 18pt; margin: 0 0 4px 0; }
    .contact-line { font-size: 9pt; color: #333333; margin-bottom: 12px; }
    h2 {
        font-size: 12pt;
        text-transform: uppercase;
        border-bottom: 1px solid #333333;
        padding-bottom: 2px;
        margin: 14px 0 6px 0;
    }
    .entry { margin-bottom: 8px; }
    .entry-title { font-size: 10.5pt; font-weight: bold; }
    .entry-meta { font-size: 9pt; color: #444444; margin-bottom: 2px; }
    ul { margin: 2px 0 0 0; padding-left: 16px; }
    li { margin-bottom: 2px; }
    .skills-row { margin-bottom: 3px; }
    .skills-label { font-weight: bold; }
</style>
</head>
<body>

<h1>{{ resume.contact_info.full_name }}</h1>
<div class="contact-line">
    {{ [resume.contact_info.email, resume.contact_info.phone, resume.contact_info.location,
        resume.contact_info.linkedin_url, resume.contact_info.github_url, resume.contact_info.portfolio_url]
       | select | join(' &nbsp;|&nbsp; ') | safe }}
</div>

{% if resume.professional_summary %}
<h2>Professional Summary</h2>
<p>{{ resume.professional_summary }}</p>
{% endif %}

{% if resume.skills.technical_skills or resume.skills.tools_and_platforms or resume.skills.databases or resume.skills.soft_skills %}
<h2>Skills</h2>
{% if resume.skills.technical_skills %}
<div class="skills-row"><span class="skills-label">Technical:</span> {{ resume.skills.technical_skills | join(', ') }}</div>
{% endif %}
{% if resume.skills.tools_and_platforms %}
<div class="skills-row"><span class="skills-label">Tools &amp; Platforms:</span> {{ resume.skills.tools_and_platforms | join(', ') }}</div>
{% endif %}
{% if resume.skills.databases %}
<div class="skills-row"><span class="skills-label">Databases:</span> {{ resume.skills.databases | join(', ') }}</div>
{% endif %}
{% if resume.skills.soft_skills %}
<div class="skills-row"><span class="skills-label">Soft Skills:</span> {{ resume.skills.soft_skills | join(', ') }}</div>
{% endif %}
{% endif %}

{% if resume.work_experience %}
<h2>Work Experience</h2>
{% for job in resume.work_experience %}
<div class="entry">
    <div class="entry-title">{{ job.job_title }} &mdash; {{ job.company }}</div>
    <div class="entry-meta">
        {{ [job.location, [job.start_date, (job.end_date or ('Present' if job.is_current else None))] | select | join(' - ')] | select | join(' &nbsp;|&nbsp; ') | safe }}
    </div>
    {% if job.responsibilities %}
    <ul>
        {% for point in job.responsibilities %}
        <li>{{ point }}</li>
        {% endfor %}
    </ul>
    {% endif %}
</div>
{% endfor %}
{% endif %}

{% if resume.education %}
<h2>Education</h2>
{% for edu in resume.education %}
<div class="entry">
    <div class="entry-title">{{ edu.degree }} &mdash; {{ edu.institution }}</div>
    <div class="entry-meta">
        {{ [edu.location, edu.graduation_date, (('GPA: ' ~ edu.gpa) if edu.gpa else None)] | select | join(' &nbsp;|&nbsp; ') | safe }}
    </div>
    {% if edu.highlights %}
    <ul>
        {% for point in edu.highlights %}
        <li>{{ point }}</li>
        {% endfor %}
    </ul>
    {% endif %}
</div>
{% endfor %}
{% endif %}

{% if resume.projects %}
<h2>Projects</h2>
{% for project in resume.projects %}
<div class="entry">
    <div class="entry-title">{{ project.name }}</div>
    <div class="entry-meta">{{ project.description }}</div>
    {% if project.technologies %}
    <div class="entry-meta">Technologies: {{ project.technologies | join(', ') }}</div>
    {% endif %}
</div>
{% endfor %}
{% endif %}

{% if resume.certifications %}
<h2>Certifications</h2>
<ul>
    {% for cert in resume.certifications %}
    <li>{{ cert.name }}{% if cert.issuer %} &mdash; {{ cert.issuer }}{% endif %}{% if cert.issue_date %} ({{ cert.issue_date }}){% endif %}</li>
    {% endfor %}
</ul>
{% endif %}

{% if resume.languages %}
<h2>Languages</h2>
<div>
{%- for lang in resume.languages -%}
    {{ lang.language }}{% if lang.proficiency %} ({{ lang.proficiency }}){% endif %}{% if not loop.last %}, {% endif %}
{%- endfor -%}
</div>
{% endif %}

</body>
</html>
"""

_env = Environment(loader=BaseLoader(), autoescape=True)
_template = _env.from_string(_TEMPLATE_HTML)


def render_resume_pdf(resume: ATSResumeSchema, output_path: Union[str, Path]) -> str:
    """
    Renders an ATSResumeSchema into a single-column, ATS-friendly PDF file.

    Args:
        resume: Structured resume data (typically the output of TailorAgent.tailor_resume).
        output_path: Destination file path for the generated PDF. Parent directories
            are created automatically.

    Returns:
        The output path as a string.

    Raises:
        ResumePDFError: If PDF generation fails.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    html_content = _template.render(resume=resume)

    with open(output_path, "wb") as pdf_file:
        result = pisa.CreatePDF(src=html_content, dest=pdf_file)

    if result.err:
        raise ResumePDFError(f"Failed to render resume PDF to '{output_path}' ({result.err} error(s)).")

    return str(output_path)
