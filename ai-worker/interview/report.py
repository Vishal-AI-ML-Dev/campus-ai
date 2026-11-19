"""Final interview report (Phase 4): stateless LangGraph, one call per report.

    report_preparer -> answer_scorer -> report_aggregator -> report_writer

Only the transcript is used (no resume, JD or profile): the report judges what
the candidate said. The LLM proposes scores and text; ``rubric`` decides what
counts (verified evidence quotes, zeros for missing answers, brevity cap,
deterministic aggregation).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from interview import policy, prompts, rubric as rb
from interview.llm_io import LLMUnavailable, invoke_structured
from interview.safety import clean_text, normalize, single_line
from interview.schemas import (
    MAX_ANSWER_CHARS,
    MAX_QUESTION_CHARS,
    CategoryResult,
    InterviewConfig,
    InterviewPlan,
    LLMAnswerScore,
    LLMAnswerScores,
    LLMReportSummary,
    QuestionReport,
    ReportItem,
    ReportTurn,
)

SCORE_CHUNK = 4
SCORE_PARALLEL = 4
SCORING_ANSWER_CHARS = 2_000
SUMMARY_ANSWER_CHARS = 600


class ReportGenerationFailed(RuntimeError):
    """A scoring call failed: no (partial) report is produced. Retryable."""


class ReportNotAssessable(RuntimeError):
    """The model returned no evidence-backed score for any answer."""


class ReportState(TypedDict, total=False):
    config: InterviewConfig
    plan: InterviewPlan | None
    transcript: list[ReportTurn]
    # computed
    rubric: rb.Rubric
    turns: list[dict]  # sanitized: seq, topic, focus, question, answer, kind
    questions: list[QuestionReport]
    categories: list[CategoryResult]
    overall_score: float
    strengths: list[ReportItem]
    weaknesses: list[ReportItem]
    recommendations: list[str]


def report_preparer(state: ReportState) -> dict:
    """Sanitize every turn and classify it (no_answer / dont_know / to_score)."""
    plan = state.get("plan")
    turns = []
    for t in state["transcript"]:
        topic = single_line(t.topic or "", 100) or "General"
        focus = policy.topic_named(plan, topic).focus if plan else ""
        answer = None if t.answer is None else clean_text(t.answer, MAX_ANSWER_CHARS)
        if answer is None:
            kind = "no_answer"
        elif policy.process_answer(answer).dont_know:
            kind = "dont_know"
        else:
            kind = "to_score"
        turns.append(
            {
                "seq": t.seq,
                "topic": topic,
                "focus": single_line(focus, 300),
                "question": clean_text(t.question, MAX_QUESTION_CHARS + 400),
                "answer": answer or "",
                "kind": kind,
            }
        )
    return {"rubric": rb.rubric_for(state["config"].interview_type), "turns": turns}


def _score_chunk(config: InterviewConfig, rubric: rb.Rubric, chunk: list[dict]) -> dict[int, LLMAnswerScore]:
    answers = [
        {**t, "answer": clean_text(t["answer"], SCORING_ANSWER_CHARS)} for t in chunk
    ]
    try:
        raw = invoke_structured(
            LLMAnswerScores,
            prompts.scoring_messages(config=config, rubric=rubric, answers=answers),
            step="report_score",
        )
    except LLMUnavailable as exc:
        # All or nothing: a report is final, so a transient provider error
        # must not silently drop answers and skew the scores.
        raise ReportGenerationFailed("report_score") from exc
    wanted = {t["seq"] for t in chunk}
    out: dict[int, LLMAnswerScore] = {}
    for item in raw.answers:
        if item.seq in wanted and item.seq not in out:
            out[item.seq] = item  # first entry per seq wins; others are ignored
    return out


def answer_scorer(state: ReportState) -> dict:
    config, rubric, turns = state["config"], state["rubric"], state["turns"]
    to_score = [t for t in turns if t["kind"] == "to_score"]
    chunks = [to_score[i : i + SCORE_CHUNK] for i in range(0, len(to_score), SCORE_CHUNK)]
    raw: dict[int, LLMAnswerScore] = {}
    if chunks:
        with ThreadPoolExecutor(max_workers=min(SCORE_PARALLEL, len(chunks))) as pool:
            for part in pool.map(lambda c: _score_chunk(config, rubric, c), chunks):
                raw.update(part)

    questions: list[QuestionReport] = []
    for t in turns:
        if t["kind"] == "no_answer":
            questions.append(rb.zero_report(t["seq"], rubric, status="no_answer", note=rb.NO_ANSWER_NOTE))
        elif t["kind"] == "dont_know":
            questions.append(rb.zero_report(t["seq"], rubric, status="dont_know", note=rb.DONT_KNOW_NOTE))
        else:
            questions.append(
                rb.verify_answer_score(raw.get(t["seq"]), seq=t["seq"], answer=t["answer"], rubric=rubric)
            )
    if to_score and not any(q.status == "scored" for q in questions):
        raise ReportNotAssessable("no answer could be scored")
    return {"questions": questions}


def report_aggregator(state: ReportState) -> dict:
    categories, overall = rb.aggregate(state["questions"], state["rubric"])
    return {"categories": categories, "overall_score": overall}


def _verified_items(items, answers: dict[int, str]) -> list[ReportItem]:
    out: list[ReportItem] = []
    for item in items:
        answer = answers.get(item.seq)
        quote = single_line(item.quote, 300)
        text = single_line(item.text, 300)
        if not (answer and text and quote):
            continue
        if not rb.evidence_ok(quote, answer):
            continue  # cites something the candidate never said (or a non-quote)
        out.append(ReportItem(text=text, seq=item.seq, evidence=quote))
    return out


def report_writer(state: ReportState) -> dict:
    config, rubric = state["config"], state["rubric"]
    categories = state["categories"]
    base_strengths, base_weaknesses = rb.baseline_items(categories)
    scored_seqs = {q.seq for q in state["questions"] if q.status == "scored"}
    answers = {t["seq"]: t["answer"] for t in state["turns"] if t["seq"] in scored_seqs}

    llm_strengths: list[ReportItem] = []
    llm_weaknesses: list[ReportItem] = []
    recommendations: list[str] = []
    if answers:  # nothing to write about when no answer was scored
        try:
            raw = invoke_structured(
                LLMReportSummary,
                prompts.summary_messages(
                    config=config,
                    categories=categories,
                    answers=[
                        {
                            "seq": t["seq"],
                            "question": t["question"],
                            "answer": clean_text(t["answer"], SUMMARY_ANSWER_CHARS),
                        }
                        for t in state["turns"]
                        if t["seq"] in answers
                    ],
                ),
                step="report_summary",
            )
            llm_strengths = _verified_items(raw.strengths, answers)
            llm_weaknesses = _verified_items(raw.weaknesses, answers)
            seen: set[str] = set()
            for rec in raw.recommendations:
                rec = single_line(rec, 300)
                if rec and normalize(rec) not in seen:
                    seen.add(normalize(rec))
                    recommendations.append(rec)
        except LLMUnavailable:
            pass

    return {
        "strengths": rb.merge_items(llm_strengths, base_strengths),
        "weaknesses": rb.merge_items(llm_weaknesses, base_weaknesses),
        "recommendations": recommendations[: rb.MAX_RECOMMENDATIONS]
        or rb.fallback_recommendations(categories, rubric),
    }


@lru_cache(maxsize=1)
def report_graph():
    g = StateGraph(ReportState)
    g.add_node("report_preparer", report_preparer)
    g.add_node("answer_scorer", answer_scorer)
    g.add_node("report_aggregator", report_aggregator)
    g.add_node("report_writer", report_writer)
    g.add_edge(START, "report_preparer")
    g.add_edge("report_preparer", "answer_scorer")
    g.add_edge("answer_scorer", "report_aggregator")
    g.add_edge("report_aggregator", "report_writer")
    g.add_edge("report_writer", END)
    return g.compile()
