/**
 * Phase 8.2: coding round logic (no React; unit-tested in Node).
 *
 * The server decides and enforces everything (gates, limits, problem access,
 * time); these helpers only decide what to show. Batch 1 is review-only
 * (decision P8-D8): code is saved and submitted, never run, so nothing here
 * may present a pass/fail result or a score for a submission.
 */

export type CodingLanguage = "python313" | "cpp17" | "java21" | "c11"

export const LANGUAGE_LABELS: Record<string, string> = {
	python313: "Python 3.13",
	cpp17: "C++17",
	java21: "Java 21",
	c11: "C11",
}

/** Which CodeMirror grammar each language uses (C shares the C/C++ grammar). */
export const EDITOR_GRAMMAR: Record<string, "python" | "cpp" | "java"> = {
	python313: "python",
	cpp17: "cpp",
	java21: "java",
	c11: "cpp",
}

/** True only when BOTH build flags are exactly "true". Default: false. */
export function codingBuildEnabled(env: {
	NEXT_PUBLIC_MOCK_INTERVIEW_ENABLED?: string
	NEXT_PUBLIC_MOCK_INTERVIEW_CODING_ENABLED?: string
}): boolean {
	return (
		env.NEXT_PUBLIC_MOCK_INTERVIEW_ENABLED === "true" &&
		env.NEXT_PUBLIC_MOCK_INTERVIEW_CODING_ENABLED === "true"
	)
}

type Choice = { value: string; label: string }

/** The server's interview types minus "coding" when this build has no coding UI. */
export function visibleInterviewTypes(types: Choice[] | undefined, codingAvailable: boolean): Choice[] {
	return (types ?? []).filter((t) => t.value !== "coding" || codingAvailable)
}

export function isCodingSession(session: { interview_type: string }): boolean {
	return session.interview_type === "coding"
}

export type CodingStage = "instructions" | "room" | "finished"

export function codingStage(session: { status: string }): CodingStage {
	if (session.status === "setup") return "instructions"
	if (session.status === "in_progress") return "room"
	return "finished"
}

/** Size as the server measures it (UTF-8 bytes, not characters). */
export function utf8Bytes(text: string): number {
	return new TextEncoder().encode(text).length
}

export function sizeLabel(bytes: number, max: number): string {
	const kb = (n: number) => (n / 1024).toFixed(n < 10 * 1024 ? 1 : 0)
	return `${kb(bytes)} KB of ${kb(max)} KB`
}

type AttemptLike = { status: string; submissions_remaining: number }

export function attemptOpen(attempt: { status: string }): boolean {
	return attempt.status === "pending" || attempt.status === "working"
}

/** Why Submit is unavailable, or null when it may be pressed. */
export function submitBlockedReason(
	attempt: AttemptLike,
	code: string,
	maxBytes: number,
	busy: boolean,
): string | null {
	if (!attemptOpen(attempt)) return "This problem is closed."
	if (attempt.submissions_remaining <= 0) return "No graded submissions left for this problem."
	if (!code.trim()) return "Write some code first."
	if (utf8Bytes(code) > maxBytes) return "Code is too large."
	if (busy) return "Please wait…"
	return null
}

/**
 * What a submission shows. Never a score or pass/fail in review-only mode:
 * the code was not run, and nothing is simulated (P8-D8).
 */
export function submissionStatusText(sub: { status: string; verification: string; kind: string }): string {
	const what = sub.kind === "auto" ? "Saved automatically when time ended" : "Submitted"
	if (sub.status === "not_executed") return `${what} · not run (review-only)`
	if (sub.status === "queued" || sub.status === "running") return `${what} · waiting to run`
	if (sub.status === "error") return `${what} · could not run; your code is saved`
	if (sub.status === "rejected") return `${what} · not accepted right now; your code is saved`
	return `${what} · ${sub.verification === "verified" ? "checked" : "not verified"}`
}

/** Announcement after a successful submit (aria-live, polite). */
export function submittedAnnouncement(remaining: number): string {
	const left = remaining === 1 ? "1 graded submission left" : `${remaining} graded submissions left`
	return `Submission saved. It is not run in review-only mode. ${left} for this problem.`
}

/**
 * Switching language: replace the code with the new starter only when the
 * student has not changed the current starter (or the editor is empty).
 */
export function codeAfterLanguageSwitch(
	code: string,
	oldStarter: string | undefined,
	newStarter: string | undefined,
): { code: string; replaced: boolean } {
	const untouched = !code.trim() || code === oldStarter
	if (untouched && newStarter !== undefined) return { code: newStarter, replaced: true }
	return { code, replaced: false }
}

/** A retry-safe request key (the server accepts [A-Za-z0-9_-]{8,64}). */
export function newIdempotencyKey(random: () => string = defaultRandom): string {
	const key = random().replace(/[^A-Za-z0-9_-]/g, "").slice(0, 64)
	return key.length >= 8 ? key : `${key}${"x".repeat(8 - key.length)}`
}

function defaultRandom(): string {
	const c = globalThis.crypto as Crypto | undefined
	if (c && typeof c.randomUUID === "function") return c.randomUUID()
	return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`
}

/** Autosave after this much idle time (ms), and never more than once in flight. */
export const AUTOSAVE_IDLE_MS = 10_000

// ---------------------------------------------------------------------------
// Phase 8.3: explanation turns and review-only feedback
// ---------------------------------------------------------------------------

type ExplanationTurnLike = { turn_seq: number; answer_text: string | null }

/** The explanation question waiting for an answer, if any (oldest first). */
export function pendingExplanation<T extends ExplanationTurnLike>(
	attempt: { status: string; explanation?: T[] },
): T | null {
	if (attempt.status !== "explaining") return null
	return (attempt.explanation ?? []).find((t) => t.answer_text === null) ?? null
}

/** Personalised AI hints are shown once and not stored: how many were opened
 * earlier but cannot be shown again (after a refresh). */
export function hiddenAiHints(
	attempt: { hints_used: number; problem: { hints_revealed: string[] } },
	shownThisVisit: number,
): number {
	return Math.max(0, attempt.hints_used - attempt.problem.hints_revealed.length - shownThisVisit)
}

/** Answer box limit (matches the server's answer schema). */
export const EXPLANATION_MAX_CHARS = 5_000

/**
 * How one AI-estimate category is shown. Review-only feedback has NO overall,
 * problem or session score (P8-D7): each category stands alone, labelled as an
 * AI estimate, or "Not assessed" when there was no evidence.
 */
export function estimateLabel(e: { score: number | null; assessed: boolean }): string {
	if (!e.assessed || e.score === null) return "Not assessed"
	return `AI estimate: ${e.score} / 5`
}

/** Objective categories in review-only mode are never scored. */
export function objectiveLabel(o: { status: string }): string {
	return o.status === "not_verified" ? "Not verified (code was not run)" : "Not verified"
}

/** Where a piece of evidence comes from, for screen readers and sighted users. */
export function evidenceSource(e: { line: number | null }): string {
	return e.line === null ? "From your explanation" : `Your code, line ${e.line}`
}
