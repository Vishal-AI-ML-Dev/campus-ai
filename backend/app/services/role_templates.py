"""Generic role templates for company-context interviews (Phase 8.4, spec 7.4).

Code data, not a table. Written by us from general role knowledge (approved
source 4, decision P8-D10): NO company content, no claims about any
employer's process. A template only steers which skills and competencies the
interview asks about; evaluation is never affected (P8-D11).
"""

from __future__ import annotations

from typing import TypedDict


class RoleTemplate(TypedDict):
    key: str
    label: str
    description: str
    # Skills a generic interview for this role usually covers (generation only).
    focus_skills: list[str]
    # Behavioural competencies for HR questions.
    competencies: list[str]
    # Coding-bank topics that fit the role (for later coding use; 8.7).
    coding_topics: list[str]


ROLE_TEMPLATES: dict[str, RoleTemplate] = {
    "sde_fresher": {
        "key": "sde_fresher",
        "label": "Software Engineer (fresher)",
        "description": "Programming fundamentals, data structures, problem solving and basic system knowledge.",
        "focus_skills": ["Data structures", "Algorithms", "Object-oriented programming", "SQL", "Git",
                         "Operating systems basics", "Computer networks basics"],
        "competencies": ["Problem solving", "Learning ability", "Teamwork", "Communication"],
        "coding_topics": ["arrays", "strings", "hashing", "two_pointers", "sorting_searching", "recursion"],
    },
    "frontend_developer": {
        "key": "frontend_developer",
        "label": "Frontend Developer",
        "description": "Web fundamentals, JavaScript, UI frameworks, accessibility and performance.",
        "focus_skills": ["HTML", "CSS", "JavaScript", "TypeScript", "React", "Accessibility",
                         "Web performance", "REST APIs"],
        "competencies": ["Attention to detail", "User empathy", "Collaboration", "Communication"],
        "coding_topics": ["arrays", "strings", "hashing", "stacks_queues"],
    },
    "backend_developer": {
        "key": "backend_developer",
        "label": "Backend Developer",
        "description": "APIs, databases, data modelling, reliability and security basics.",
        "focus_skills": ["REST APIs", "SQL", "Database design", "Python", "Java", "Caching",
                         "Authentication", "Testing"],
        "competencies": ["Ownership", "Problem solving", "Collaboration", "Communication"],
        "coding_topics": ["hashing", "sorting_searching", "graphs", "greedy"],
    },
    "data_analyst": {
        "key": "data_analyst",
        "label": "Data Analyst",
        "description": "SQL, spreadsheets, statistics, visualisation and explaining insights.",
        "focus_skills": ["SQL", "Excel", "Statistics", "Data visualisation", "Python", "Data cleaning"],
        "competencies": ["Analytical thinking", "Business understanding", "Storytelling", "Attention to detail"],
        "coding_topics": ["arrays", "hashing", "sorting_searching"],
    },
    "qa_sdet": {
        "key": "qa_sdet",
        "label": "QA / SDET",
        "description": "Testing principles, test design, automation and debugging.",
        "focus_skills": ["Test case design", "Automation testing", "Selenium", "API testing", "SQL",
                         "Bug reporting", "Java", "Python"],
        "competencies": ["Attention to detail", "Curiosity", "Communication", "Collaboration"],
        "coding_topics": ["strings", "arrays", "stacks_queues"],
    },
    "business_analyst": {
        "key": "business_analyst",
        "label": "Business Analyst",
        "description": "Requirements, stakeholder communication, process mapping and data-driven decisions.",
        "focus_skills": ["Requirements gathering", "Process mapping", "SQL", "Excel", "Documentation",
                         "Stakeholder management"],
        "competencies": ["Communication", "Stakeholder management", "Structured thinking", "Negotiation"],
        "coding_topics": ["arrays", "hashing"],
    },
}


def get(key: str | None) -> RoleTemplate | None:
    return ROLE_TEMPLATES.get(key or "")


def public_list() -> list[dict]:
    return [
        {
            "key": t["key"],
            "label": t["label"],
            "description": t["description"],
            "focus_skills": list(t["focus_skills"]),
            "competencies": list(t["competencies"]),
        }
        for t in ROLE_TEMPLATES.values()
    ]
