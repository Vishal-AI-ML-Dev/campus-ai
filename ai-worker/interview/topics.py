"""Topic library, budgets and the deterministic fallback plan.

The library guides the LLM planner (it picks from / adapts these areas) and is
the fallback when the planner is unavailable, so an interview can always start.
It is a list of AREAS, not a list of questions: questions are always generated
turn by turn from the candidate's actual answers.
"""

from __future__ import annotations

from interview.schemas import CandidateProfile, InterviewConfig, PlanTopic

INTRO_TOPIC = PlanTopic(
    name="Introduction",
    area="introduction",
    focus="Background, education, interests and why this role",
    source="intro",
)

HR_AREAS: dict[str, tuple[str, str]] = {
    "strengths_weaknesses": ("Strengths and weaknesses", "Self-awareness with concrete examples"),
    "teamwork": ("Teamwork", "A STAR story about working in a team"),
    "leadership": ("Leadership", "Taking ownership or leading others (STAR)"),
    "conflict": ("Conflict resolution", "A disagreement and how it was resolved (STAR)"),
    "career_goals": ("Career goals", "Short and long-term goals and fit with the role"),
    "situational": ("Situational judgement", "A realistic workplace scenario"),
    "project_discussion": ("Project discussion", "Their role, decisions and learnings in a project"),
    "behavioural": ("Behavioural (STAR)", "Handling pressure, failure or deadlines"),
}

TECH_AREAS: dict[str, tuple[str, str]] = {
    "dsa": ("Data structures and algorithms", "Complexity, trade-offs, choosing structures"),
    "oop": ("Object-oriented programming", "Encapsulation, inheritance, polymorphism, design"),
    "dbms": ("DBMS", "Normalization, transactions, indexing"),
    "sql": ("SQL", "Joins, aggregation, query reasoning"),
    "os": ("Operating systems", "Processes, threads, scheduling, memory"),
    "cn": ("Computer networks", "TCP/IP, HTTP, DNS, latency"),
    "python": ("Python", "Language features, data model, idioms"),
    "java": ("Java", "JVM, collections, OOP in Java, concurrency basics"),
    "cpp": ("C++", "Memory, pointers, STL, RAII"),
    "ml": ("Machine learning", "Model choice, bias/variance, evaluation metrics"),
    "dl": ("Deep learning", "Neural network training, overfitting, architectures"),
    "genai": ("Generative AI", "How generative models work and their limits"),
    "llm_rag": ("LLMs and RAG", "Prompting, embeddings, retrieval, hallucination control"),
    "backend": ("Backend development", "APIs, databases, caching, scaling basics"),
    "frontend": ("Frontend development", "HTML/CSS/JS, rendering, state, accessibility"),
}

# Keywords (role / JD / verified skills) -> technical areas.
_KEYWORDS: dict[str, tuple[str, ...]] = {
    "python": ("python", "django", "flask", "fastapi", "pandas"),
    "java": ("java", "spring", "jvm"),
    "cpp": ("c++", "cpp", "embedded"),
    "sql": ("sql", "mysql", "postgres", "database", "data analyst", "analytics"),
    "dbms": ("dbms", "database", "postgres", "mysql", "mongodb", "backend"),
    "ml": ("machine learning", "ml ", "ml engineer", "data scien", "scikit", "sklearn"),
    "dl": ("deep learning", "pytorch", "tensorflow", "neural", "computer vision", "cv engineer"),
    "genai": ("genai", "generative", "gen ai"),
    "llm_rag": ("llm", "rag", "langchain", "prompt", "gpt", "retrieval"),
    "backend": ("backend", "back-end", "api", "node", "server", "full stack", "fullstack", "sde"),
    "frontend": ("frontend", "front-end", "react", "next.js", "angular", "vue", "ui developer", "full stack", "fullstack", "web"),
    "os": ("operating system", "linux", "systems", "sde", "software engineer"),
    "cn": ("network", "devops", "cloud", "sde", "software engineer"),
    "oop": ("software", "developer", "engineer", "sde", "java", "c++"),
    "dsa": ("software", "developer", "engineer", "sde", "programmer"),
}

_BUDGETS = {15: (6, 4), 30: (12, 7), 45: (18, 10)}


def budgets(duration_minutes: int) -> tuple[int, int]:
    """(question_budget, topic_count) for a duration; interpolated off-menu."""
    if duration_minutes in _BUDGETS:
        return _BUDGETS[duration_minutes]
    questions = max(3, min(30, round(duration_minutes / 2.5)))
    return questions, max(2, min(10, round(questions * 0.6)))


def library_for(interview_type: str) -> dict[str, tuple[str, str]]:
    return HR_AREAS if interview_type == "hr" else TECH_AREAS


def rank_tech_areas(text: str) -> list[str]:
    """Technical areas ordered by keyword hits in role/JD/skills text."""
    haystack = f" {text.lower()} "
    scores = {
        area: sum(haystack.count(k) for k in keys) for area, keys in _KEYWORDS.items()
    }
    ranked = sorted((a for a in scores if scores[a] > 0), key=lambda a: -scores[a])
    # Core CS fundamentals fill any gap, in campus-placement order.
    for core in ("dsa", "oop", "dbms", "os", "cn", "sql"):
        if core not in ranked:
            ranked.append(core)
    return ranked


def fallback_plan_topics(
    config: InterviewConfig, profile: CandidateProfile, jd_text: str
) -> list[PlanTopic]:
    """Deterministic topics used when the LLM planner is unavailable."""
    _, topic_count = budgets(config.duration_minutes)
    topics = [INTRO_TOPIC]
    if config.interview_type == "hr":
        order = list(HR_AREAS)
        if not profile.projects:
            order.remove("project_discussion")
            order.append("project_discussion")
        for area in order[: topic_count - 1]:
            name, focus = HR_AREAS[area]
            topics.append(PlanTopic(name=name, area=area, focus=focus, source="general"))
    else:
        signal = " ".join(
            [config.target_role, jd_text, " ".join(profile.verified_skills)]
        )
        for area in rank_tech_areas(signal)[: topic_count - 1]:
            name, focus = TECH_AREAS[area]
            topics.append(PlanTopic(name=name, area=area, focus=focus, source="role"))
    return topics
