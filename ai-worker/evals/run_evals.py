"""AI eval harness for the Campus AI worker.

Runs a small, fixed battery of evaluation cases against the worker's REAL LLM
features (mentor / resume / ATS / proof scoring) and checks each output with
deterministic, tolerant assertions. This is the "eval harness" half of the §7
observability item (the other half is Langfuse tracing in tracing.py).

Gated like the Postgres RLS suite: it needs a real LLM key, so if the configured
provider has no API key set, the whole run is SKIPPED with exit code 0 (so CI
without secrets stays green). With a key, ANY hard-check failure exits non-zero.

Run (from the ai-worker folder):
    uv run python evals/run_evals.py

Optionally trace every eval call to Langfuse by also setting the LANGFUSE_* env
vars (see tracing.py) - the calls go through the same get_chat_model().

Location:
    E:\\campus-ai\\ai-worker\\evals\\run_evals.py
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone

# Make the worker modules importable when run as `python evals/run_evals.py`
# from the ai-worker folder.
_HERE = os.path.dirname(os.path.abspath(__file__))
_WORKER_ROOT = os.path.dirname(_HERE)
if _WORKER_ROOT not in sys.path:
    sys.path.insert(0, _WORKER_ROOT)

from config import settings  # noqa: E402


@dataclass
class CheckResult:
    passed: bool
    detail: str


@dataclass
class EvalResult:
    name: str
    feature: str
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(c.passed for c in self.checks)


def _have_llm_key() -> bool:
    provider = settings.LLM_PROVIDER.strip().lower()
    if provider == "groq":
        return bool(settings.GROQ_API_KEY)
    if provider == "gemini":
        return bool(settings.GOOGLE_API_KEY)
    return False


def _check(cond: bool, detail: str) -> CheckResult:
    return CheckResult(passed=bool(cond), detail=detail)


# --- Individual evals -------------------------------------------------------
def eval_mentor_grounding() -> EvalResult:
    from mentor import MentorChatRequest, MentorProfile, mentor_chat

    res = EvalResult("mentor: grounded skills, no hallucination", "mentor")
    profile = MentorProfile(verified_skills=["Python"], cgpa=None)
    out = mentor_chat(
        MentorChatRequest(profile=profile, question="What are my verified skills?")
    )
    answer = out.answer.lower()
    res.checks.append(_check("python" in answer, "mentions verified skill 'Python'"))
    # The profile has ONLY Python; these must not be claimed as the student's.
    forbidden = ["java", "c++", "rust", "kubernetes", "tensorflow"]
    leaked = [w for w in forbidden if w in answer]
    res.checks.append(_check(not leaked, f"no hallucinated skills (leaked={leaked})"))
    return res


def eval_mentor_honesty() -> EvalResult:
    from mentor import MentorChatRequest, MentorProfile, mentor_chat

    res = EvalResult("mentor: honest about missing CGPA", "mentor")
    profile = MentorProfile(verified_skills=["Python"], cgpa=None)
    out = mentor_chat(
        MentorChatRequest(profile=profile, question="What is my exact CGPA?")
    )
    answer = out.answer.lower()
    honest_markers = [
        "no ", "not ", "don't", "do not", "isn't", "no data",
        "unavailable", "haven't", "not available", "no cgpa",
    ]
    res.checks.append(
        _check(
            any(m in answer for m in honest_markers),
            "admits CGPA is not available rather than inventing one",
        )
    )
    return res


def eval_ats_scoring() -> EvalResult:
    from resume import AtsScoreRequest, ats_score

    res = EvalResult("ats: valid score + keyword match", "resume")
    resume_text = (
        "# Aarav Sharma\n"
        "Skills: Python, FastAPI, PostgreSQL\n"
        "Projects: Built a REST API with FastAPI and Python.\n"
    )
    jd = (
        "Looking for a backend intern skilled in Python, FastAPI, Docker, "
        "and Kubernetes. REST API experience required."
    )
    out = ats_score(AtsScoreRequest(resume_text=resume_text, job_description=jd))
    res.checks.append(_check(0 <= out.score <= 100, f"score in 0-100 (={out.score})"))
    matched = [k.lower() for k in out.matched_keywords]
    missing = [k.lower() for k in out.missing_keywords]
    res.checks.append(
        _check(any("python" in m for m in matched), "matched includes Python")
    )
    res.checks.append(
        _check(
            any("docker" in m for m in missing)
            or any("kubernetes" in m for m in missing),
            "missing includes Docker/Kubernetes",
        )
    )
    res.checks.append(_check(len(out.suggestions) >= 1, "returns >=1 suggestion"))
    return res


def eval_resume_no_invention() -> EvalResult:
    from resume import ResumeDraftRequest, ResumeProfile, draft_resume

    res = EvalResult("resume: uses only verified facts", "resume")
    profile = ResumeProfile(full_name="Test Student", verified_skills=["Python"])
    out = draft_resume(ResumeDraftRequest(profile=profile))
    md = out.markdown.lower()
    res.checks.append(_check("test student" in md, "includes the student's name"))
    res.checks.append(_check("python" in md, "includes the verified skill"))
    res.checks.append(_check(len(out.markdown) > 80, "produced a non-trivial resume"))
    return res


def eval_proof_scoring() -> EvalResult:
    from schemas import ProofScoreRequest
    from scoring import score_proof

    res = EvalResult("proof: strong >= weak, valid verdict", "scoring")
    strong = score_proof(
        ProofScoreRequest(
            claim_type="project",
            title="Campus AI placement platform",
            evidence_url="https://github.com/example/campus-ai",
            evidence_note=(
                "Full FastAPI + Postgres repo with 40 commits, a test suite, "
                "and a live deployed demo."
            ),
        )
    )
    weak = score_proof(
        ProofScoreRequest(
            claim_type="skill",
            title="Machine Learning",
            evidence_url=None,
            evidence_note=None,
        )
    )
    verdicts = {"likely_genuine", "needs_review", "likely_weak"}
    res.checks.append(
        _check(strong.verdict in verdicts, f"strong verdict valid ({strong.verdict})")
    )
    res.checks.append(
        _check(weak.verdict in verdicts, f"weak verdict valid ({weak.verdict})")
    )
    res.checks.append(
        _check(
            0 <= strong.score <= 100 and 0 <= weak.score <= 100,
            f"scores in 0-100 (strong={strong.score}, weak={weak.score})",
        )
    )
    res.checks.append(
        _check(
            strong.score >= weak.score,
            f"strong proof scores >= weak ({strong.score} >= {weak.score})",
        )
    )
    return res


def eval_ats_ranking() -> EvalResult:
    from resume import AtsScoreRequest, ats_score

    res = EvalResult("ats: strong resume outscores weak (same JD)", "resume")
    jd = (
        "Backend intern: strong Python, FastAPI, PostgreSQL, REST APIs and "
        "Docker. Testing and Git required."
    )
    strong_resume = (
        "# Aarav Sharma\n"
        "Skills: Python, FastAPI, PostgreSQL, Docker, Git, pytest\n"
        "Experience: Built and deployed a FastAPI + PostgreSQL REST API, "
        "containerised with Docker, covered by a pytest suite.\n"
    )
    weak_resume = (
        "# Priya Verma\n"
        "Skills: MS Word, PowerPoint\n"
        "Hobbies: painting and travel.\n"
    )
    strong = ats_score(
        AtsScoreRequest(resume_text=strong_resume, job_description=jd)
    )
    weak = ats_score(AtsScoreRequest(resume_text=weak_resume, job_description=jd))
    res.checks.append(
        _check(
            0 <= strong.score <= 100 and 0 <= weak.score <= 100,
            f"scores in range (strong={strong.score}, weak={weak.score})",
        )
    )
    res.checks.append(
        _check(
            strong.score > weak.score,
            f"strong resume outscores weak ({strong.score} > {weak.score})",
        )
    )
    res.checks.append(
        _check(
            isinstance(strong.matched_keywords, list)
            and isinstance(strong.missing_keywords, list),
            "structured keyword lists present",
        )
    )
    return res


def eval_offline_parsing() -> EvalResult:
    """Deterministic checks that need NO API key (safe to run in CI)."""
    from resume import AtsResult, _coerce_ats_dict, _extract_json

    res = EvalResult("offline: ATS JSON parsing + coercion", "resume")
    parsed = _extract_json(
        "here you go ```json\n"
        '{"score": "88.4", "verdict": "good", "matched_keywords": ["Python"], '
        '"missing_keywords": [], "suggestions": ["add Docker"]}\n'
        "``` cheers"
    )
    res.checks.append(
        _check(
            parsed.get("verdict") == "good",
            "extracts JSON object from fenced/prose text",
        )
    )
    model = AtsResult.model_validate(_coerce_ats_dict(parsed))
    res.checks.append(
        _check(model.score == 88, "coerces string/float score to clamped int")
    )
    res.checks.append(
        _check(
            model.matched_keywords == ["Python"],
            "keyword lists preserved as list[str]",
        )
    )
    junk = _coerce_ats_dict({"score": "oops", "matched_keywords": "notalist"})
    res.checks.append(
        _check(
            junk["score"] == 0 and junk["matched_keywords"] == [],
            "junk score/lists coerced safely",
        )
    )
    return res


# Offline evals need no API key; LLM evals make real provider calls.
OFFLINE_EVALS = [eval_offline_parsing]
LLM_EVALS = [
    eval_mentor_grounding,
    eval_mentor_honesty,
    eval_ats_scoring,
    eval_ats_ranking,
    eval_resume_no_invention,
    eval_proof_scoring,
]


def _run(fn) -> EvalResult:
    try:
        r = fn()
    except Exception as exc:  # noqa: BLE001 - a crashing case is a failure
        r = EvalResult(fn.__name__, "error")
        r.checks.append(CheckResult(False, f"raised: {exc}"))
    verdict = "PASS" if r.passed else "FAIL"
    print(f"[{verdict}] {r.name}")
    for c in r.checks:
        mark = "  ok" if c.passed else "  XX"
        print(f"{mark} - {c.detail}")
    print()
    return r


def _write_report(results: list[EvalResult]) -> str:
    report = {
        "provider": settings.LLM_PROVIDER,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "passed": sum(1 for r in results if r.passed),
        "total": len(results),
        "cases": [
            {
                "name": r.name,
                "feature": r.feature,
                "passed": r.passed,
                "checks": [
                    {"passed": c.passed, "detail": c.detail} for c in r.checks
                ],
            }
            for r in results
        ],
    }
    path = os.path.join(_HERE, "last_report.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    return path


def main() -> int:
    results: list[EvalResult] = []

    # 1) Offline structural checks - always run, no secrets needed.
    print(f"Running {len(OFFLINE_EVALS)} offline eval case(s)...\n")
    for fn in OFFLINE_EVALS:
        results.append(_run(fn))

    # 2) Real LLM evals - only when the provider key is configured.
    if _have_llm_key():
        print(
            f"Running {len(LLM_EVALS)} LLM eval cases via "
            f"provider={settings.LLM_PROVIDER} (real calls, may take ~1-2 min)...\n"
        )
        for fn in LLM_EVALS:
            results.append(_run(fn))
    else:
        print(
            f"SKIPPED {len(LLM_EVALS)} LLM evals: no API key for "
            f"LLM_PROVIDER={settings.LLM_PROVIDER!r}. Set the provider key in "
            ".env to run them.\n"
        )

    passed = sum(1 for r in results if r.passed)
    total = len(results)
    print(f"==== {passed}/{total} eval cases passed ====")

    if "--report" in sys.argv or os.getenv("EVAL_REPORT"):
        path = _write_report(results)
        print(f"Report written to {path}")

    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
