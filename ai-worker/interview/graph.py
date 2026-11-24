"""LangGraph workflows for the AI mock interview (stateless, one call per step).

    plan:  profile_loader -> resume_context_loader -> jd_analyzer -> planner
    turn:  interview_controller -(opening)-> question_generator
                               -(answer)--> answer_processor -> answer_evaluator
                                            -> follow_up_decider -> question_generator

The backend owns all persistence and replays the plan + transcript on every
call; nothing is remembered here between requests.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from interview import policy, prompts
from interview.company import filter_claims
from interview.llm_io import LLMUnavailable, invoke_structured
from interview.safety import clean_text, is_similar, quote_in, single_line
from interview.schemas import (
    MAX_JD_CHARS,
    MAX_QUESTION_CHARS,
    MAX_RESUME_CHARS,
    CandidateProfile,
    InterviewConfig,
    InterviewPlan,
    JDAnalysis,
    LLMClosing,
    LLMEvaluation,
    LLMJDAnalysis,
    LLMPlan,
    LLMQuestion,
    NextQuestion,
    PlanTopic,
    TranscriptTurn,
    TurnEvaluation,
)
from interview.topics import INTRO_TOPIC, budgets, fallback_plan_topics, library_for

_VALID_SOURCES = {"role", "resume", "jd", "general"}


class QuestionGenerationFailed(RuntimeError):
    """No usable question could be produced (the request must fail)."""


# =============================================================================
# Plan graph
# =============================================================================
class PlanState(TypedDict, total=False):
    config: InterviewConfig
    profile: CandidateProfile
    raw_resume: str | None
    raw_jd: str | None
    resume_text: str
    jd_text: str
    jd_analysis: JDAnalysis | None
    plan: InterviewPlan
    company: Any  # Phase 8.4: CompanyContext | None (generation only)


def profile_loader(state: PlanState) -> dict:
    """Normalize the verified profile (dedupe/trim skills, cap sizes)."""
    p = state.get("profile") or CandidateProfile()
    seen: set[str] = set()
    skills = []
    for s in p.verified_skills:
        s = single_line(s, 60)
        if s and s.lower() not in seen:
            seen.add(s.lower())
            skills.append(s)
    profile = CandidateProfile(
        verified_skills=skills[:40], projects=p.projects[:10], cgpa=p.cgpa
    )
    return {"profile": profile}


def resume_context_loader(state: PlanState) -> dict:
    return {"resume_text": clean_text(state.get("raw_resume"), MAX_RESUME_CHARS)}


def jd_analyzer(state: PlanState) -> dict:
    jd_text = clean_text(state.get("raw_jd"), MAX_JD_CHARS)
    if not jd_text:
        return {"jd_text": "", "jd_analysis": None}
    try:
        raw = invoke_structured(LLMJDAnalysis, prompts.jd_messages(jd_text), step="jd")
    except LLMUnavailable:
        return {"jd_text": jd_text, "jd_analysis": JDAnalysis()}
    # Keep only skills that actually appear in the JD (no invented requirements).
    skills = [
        single_line(s, 60) for s in raw.required_skills if quote_in(s, jd_text, min_len=1)
    ][:12]
    analysis = JDAnalysis(
        role_title=single_line(raw.role_title, 150),
        required_skills=skills,
        responsibilities=[single_line(r, 200) for r in raw.responsibilities][:6],
        seniority=single_line(raw.seniority, 50),
    )
    return {"jd_text": jd_text, "jd_analysis": analysis}


def _ground_topic(topic: Any, resume_text: str, jd_text: str, areas: set[str]) -> PlanTopic | None:
    name = single_line(getattr(topic, "name", ""), 100)
    area_raw = str(getattr(topic, "area", "")).strip().lower()
    if not name or "introduc" in name.lower() or area_raw == "introduction":
        return None  # Introduction is always added as topic 0
    source = str(getattr(topic, "source", "role")).strip().lower()
    if source not in _VALID_SOURCES:
        source = "role"
    grounding = single_line(getattr(topic, "grounding", ""), 300)
    origin = resume_text if source == "resume" else jd_text if source == "jd" else ""
    if source in ("resume", "jd") and not (grounding and quote_in(grounding, origin)):
        # The claimed quote is not in the candidate's text: never let an
        # unverifiable "you worked on X" reach a question.
        source, grounding = "role", ""
    if source not in ("resume", "jd"):
        grounding = ""
    area = single_line(getattr(topic, "area", ""), 100)
    return PlanTopic(
        name=name,
        area=area if area in areas else "",
        focus=single_line(getattr(topic, "focus", ""), 300),
        source=source,  # type: ignore[arg-type]
        grounding=grounding,
    )


def planner(state: PlanState) -> dict:
    config = state["config"]
    profile = state["profile"]
    resume_text = state.get("resume_text", "")
    jd_text = state.get("jd_text", "")
    analysis = state.get("jd_analysis")
    question_budget, topic_count = budgets(config.duration_minutes)
    library = library_for(config.interview_type)

    jd_summary = ""
    if analysis:
        jd_summary = "; ".join(
            filter(
                None,
                [
                    analysis.role_title,
                    ", ".join(analysis.required_skills),
                    "; ".join(analysis.responsibilities),
                ],
            )
        )

    topics: list[PlanTopic] = []
    planner_kind = "llm"
    try:
        raw = invoke_structured(
            LLMPlan,
            prompts.plan_messages(
                config=config,
                profile=profile,
                resume_text=resume_text,
                jd_summary=jd_summary,
                jd_text=jd_text,
                library=library,
                topic_count=topic_count - 1,
                company=state.get("company"),
            ),
            step="plan",
        )
        seen: set[str] = set()
        for t in raw.topics:
            grounded = _ground_topic(t, resume_text, jd_text, set(library))
            if grounded and grounded.name.lower() not in seen:
                seen.add(grounded.name.lower())
                topics.append(grounded)
            if len(topics) >= topic_count - 1:
                break
    except LLMUnavailable:
        topics = []

    if len(topics) < 2:
        planner_kind = "fallback"
        topics = fallback_plan_topics(config, profile, jd_text)[1:]

    plan = InterviewPlan(
        topics=[INTRO_TOPIC, *topics][:12],
        question_budget=question_budget,
        jd_analysis=analysis,
        planner=planner_kind,  # type: ignore[arg-type]
    )
    return {"plan": plan}


@lru_cache(maxsize=1)
def plan_graph():
    g = StateGraph(PlanState)
    g.add_node("profile_loader", profile_loader)
    g.add_node("resume_context_loader", resume_context_loader)
    g.add_node("jd_analyzer", jd_analyzer)
    g.add_node("planner", planner)
    g.add_edge(START, "profile_loader")
    g.add_edge("profile_loader", "resume_context_loader")
    g.add_edge("resume_context_loader", "jd_analyzer")
    g.add_edge("jd_analyzer", "planner")
    g.add_edge("planner", END)
    return g.compile()


# =============================================================================
# Turn graph
# =============================================================================
class TurnState(TypedDict, total=False):
    config: InterviewConfig
    plan: InterviewPlan
    profile: CandidateProfile
    resume_text: str
    jd_text: str
    transcript: list[TranscriptTurn]
    remaining_seconds: int
    company: Any  # Phase 8.4: CompanyContext | None (generation only)
    # computed
    phase: str  # opening | answer
    level: str
    processed: policy.ProcessedAnswer
    evaluation: TurnEvaluation | None
    action: str
    topic: PlanTopic | None
    end_reason: str | None
    question: NextQuestion | None
    closing_message: str | None


def interview_controller(state: TurnState) -> dict:
    """Opening vs. answer processing; current difficulty level."""
    config = state["config"]
    # Every replayed turn is untrusted (answers come from the candidate, and
    # older questions were model output): sanitize before any prompt sees it.
    transcript = [
        t.model_copy(
            update={
                "question": clean_text(t.question, MAX_QUESTION_CHARS + 400),
                "topic": single_line(t.topic or "", 100) or None,
                "answer": None if t.answer is None else clean_text(t.answer, 5_000),
            }
        )
        for t in (state.get("transcript") or [])
    ]
    return {
        "transcript": transcript,
        "phase": "opening" if not transcript else "answer",
        "level": policy.current_level(config.difficulty, transcript),
        "resume_text": clean_text(state.get("resume_text"), MAX_RESUME_CHARS),
        "jd_text": clean_text(state.get("jd_text"), MAX_JD_CHARS),
        "evaluation": None,
        "action": "opening",
        "topic": INTRO_TOPIC if not transcript else None,
    }


def answer_processor(state: TurnState) -> dict:
    last = state["transcript"][-1]
    return {"processed": policy.process_answer(last.answer)}


def answer_evaluator(state: TurnState) -> dict:
    processed: policy.ProcessedAnswer = state["processed"]
    if processed.dont_know:
        return {"evaluation": policy.dont_know_evaluation()}
    last = state["transcript"][-1]
    topic = policy.topic_named(state["plan"], last.topic)
    answer = clean_text(processed.text, 5_000)
    try:
        raw = invoke_structured(
            LLMEvaluation,
            prompts.evaluation_messages(
                config=state["config"],
                question=last.question,
                answer=answer,
                topic=topic,
                level=state["level"],
            ),
            step="evaluate",
        )
    except LLMUnavailable:
        return {"evaluation": policy.heuristic_evaluation(processed)}

    coverage = raw.coverage.strip().lower()
    evidence = single_line(raw.evidence, 300)
    if evidence and not quote_in(evidence, answer, min_len=1):
        evidence = ""  # the model "quoted" something the candidate never said
    evaluation = TurnEvaluation(
        coverage=coverage if coverage in ("full", "partial", "none") else "partial",  # type: ignore[arg-type]
        depth=max(1, min(5, int(raw.depth))),
        missing_points=[single_line(m, 200) for m in raw.missing_points][:3],
        evidence=evidence,
        follow_up_recommended=bool(raw.follow_up_recommended),
        follow_up_angle=single_line(raw.follow_up_angle, 300),
        difficulty_signal=max(-1, min(1, int(raw.difficulty_signal))),
        source="llm",
    )
    return {"evaluation": evaluation}


def follow_up_decider(state: TurnState) -> dict:
    transcript = state["transcript"]
    evaluation = state["evaluation"] or policy.heuristic_evaluation(state["processed"])
    # The level for the NEXT question includes this answer's signal.
    evaluated = [*transcript[:-1], transcript[-1].model_copy(update={"evaluation": evaluation})]
    level = policy.current_level(state["config"].difficulty, evaluated)
    decision = policy.decide(
        plan=state["plan"],
        transcript=transcript,
        evaluation=evaluation,
        remaining_seconds=state["remaining_seconds"],
        difficulty=state["config"].difficulty,
    )
    return {
        "level": level,
        "action": decision.action,
        "topic": decision.topic,
        "end_reason": decision.end_reason,
    }


def _last_paragraph(text: str) -> str:
    parts = [p for p in text.split("\n\n") if p.strip()]
    return parts[-1] if parts else text


def _closing_fallback(language: str) -> str:
    if language == "hi":
        return "इंटरव्यू में भाग लेने के लिए धन्यवाद। आपकी फीडबैक रिपोर्ट अलग से उपलब्ध होगी।"
    if language == "hinglish":
        return "Interview dene ke liye thank you! Aapki feedback report alag se available hogi."
    return "Thank you for taking this practice interview. Your feedback report will be available separately."


def question_generator(state: TurnState) -> dict:
    config = state["config"]
    action = state["action"]
    transcript = state.get("transcript") or []

    if action == "end":
        try:
            raw = invoke_structured(
                LLMClosing,
                prompts.closing_messages(config=config, end_reason=state.get("end_reason") or ""),
                step="closing",
            )
            message = clean_text(raw.message, 800) or _closing_fallback(config.language)
        except LLMUnavailable:
            message = _closing_fallback(config.language)
        return {"question": None, "closing_message": message}

    topic: PlanTopic = state["topic"] or INTRO_TOPIC
    previous = [_last_paragraph(t.question) for t in transcript]
    evaluation = state.get("evaluation")
    angle = evaluation.follow_up_angle if evaluation else ""
    remaining_minutes = max(0, state["remaining_seconds"] // 60)

    avoid = list(previous)
    company = state.get("company")
    chosen: tuple[str, str] | None = None
    for _attempt in range(2):
        try:
            raw = invoke_structured(
                LLMQuestion,
                prompts.question_messages(
                    config=config,
                    profile=state.get("profile") or CandidateProfile(),
                    resume_text=state.get("resume_text", ""),
                    jd_text=state.get("jd_text", ""),
                    transcript=transcript,
                    action=action,
                    topic=topic,
                    level=state["level"],
                    follow_up_angle=angle,
                    avoid=avoid,
                    remaining_minutes=remaining_minutes,
                    company=company,
                ),
                step="question",
            )
        except LLMUnavailable as exc:
            raise QuestionGenerationFailed(str(exc)) from exc
        question = clean_text(raw.question, MAX_QUESTION_CHARS)
        preamble = clean_text(raw.preamble, 400)
        if company is not None:
            # Phase 8.4 mandatory claim filter: no "Company X asks ..." text.
            question = filter_claims(question, company.name)
            preamble = filter_claims(preamble, company.name)
        if not question:
            continue
        chosen = (preamble, question)
        if not any(is_similar(question, p) for p in previous):
            break
        avoid.append(question)  # repeat: ask again once, then accept
    if chosen is None:
        raise QuestionGenerationFailed("question: empty model output")

    preamble, question = chosen
    text = f"{preamble}\n\n{question}" if preamble else question
    return {
        "question": NextQuestion(
            seq=len(transcript) + 1,
            topic=topic.name,
            is_follow_up=(action == "follow_up"),
            text=text[: MAX_QUESTION_CHARS + 400],
        ),
        "closing_message": None,
    }


def _route_after_controller(state: TurnState) -> str:
    return "question_generator" if state["phase"] == "opening" else "answer_processor"


@lru_cache(maxsize=1)
def turn_graph():
    g = StateGraph(TurnState)
    g.add_node("interview_controller", interview_controller)
    g.add_node("answer_processor", answer_processor)
    g.add_node("answer_evaluator", answer_evaluator)
    g.add_node("follow_up_decider", follow_up_decider)
    g.add_node("question_generator", question_generator)
    g.add_edge(START, "interview_controller")
    g.add_conditional_edges(
        "interview_controller",
        _route_after_controller,
        ["question_generator", "answer_processor"],
    )
    g.add_edge("answer_processor", "answer_evaluator")
    g.add_edge("answer_evaluator", "follow_up_decider")
    g.add_edge("follow_up_decider", "question_generator")
    g.add_edge("question_generator", END)
    return g.compile()
