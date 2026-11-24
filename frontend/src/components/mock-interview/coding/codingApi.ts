/**
 * Phase 8.1/8.2: coding round types and API calls (review-only).
 *
 * Backend: /mock-interview/coding/* and /mock-interview/sessions/{id}/coding/*
 * (docs/mock-interview/API.md). Only public problem material ever arrives
 * here; code is submitted for a later review and is never run in Batch 1.
 * Code is never written to browser storage (drafts autosave to the server).
 */

import { api } from "@/lib/api"

export type CodingChoice = { value: string; label: string }

export type CodingProblemBrief = { key: string; title: string; difficulty: string; topics: string[] }

export type CodingCatalog = {
	languages: CodingChoice[]
	topics: CodingChoice[]
	difficulties: CodingChoice[]
	durations_minutes: number[]
	graded_submissions_per_problem: number
	hints_per_problem: Record<string, number>
	source_max_bytes: number
	review_only: boolean
	problems: CodingProblemBrief[]
}

export type CodingProblem = {
	key: string
	version: number
	title: string
	difficulty: string
	topics: string[]
	statement: string
	input_format: string
	output_format: string
	constraints: string
	examples: { input: string; output: string; explanation?: string | null }[]
	visible_tests: { input: string; output: string }[]
	starter: Record<string, string>
	time_limit_ms: number
	memory_limit_mb: number
	hints_revealed: string[]
}

/** Phase 8.3: a typed explanation question after "Done" (answered via /answer). */
export type CodingExplanationTurn = {
	turn_seq: number
	question: string
	answer_text: string | null
	answered_at: string | null
}

export type CodingAttempt = {
	seq: number
	status: "pending" | "working" | "explaining" | "auto_submitted" | "closed" | "abandoned"
	language: string | null
	draft_source: string | null
	draft_rev: number
	hints_used: number
	hints_available: number
	submissions_used: number
	submissions_remaining: number
	problem: CodingProblem
	explanation?: CodingExplanationTurn[]
}

export type CodingState = {
	session_id: number
	status: string
	languages: string[]
	review_only: boolean
	remaining_seconds: number | null
	graded_submissions_per_problem: number
	attempts: CodingAttempt[]
	/** Phase 8.3: AI hints, explanation questions and AI-estimate feedback are on. */
	ai_enabled?: boolean
}

// --- Phase 8.3: review-only feedback (no score of any kind) -------------------
export type CodingEvidence = { line: number | null; quote: string }

export type CodingEstimate = {
	key: "code_quality" | "reasoning" | "testing"
	label: string
	weight_percent: number
	kind: "ai_estimate"
	score: number | null
	assessed: boolean
	evidence: CodingEvidence[]
	rationale: string
	/** Why this individual AI estimate is uncertain (always present). */
	uncertainty: string
}

export type CodingProblemFeedback = {
	attempt_seq: number
	problem_title: string
	language: string | null
	status: "ai_estimated" | "not_submitted"
	verification: "not_verified"
	objective: { key: string; label: string; weight_percent: number; status: "not_verified" }[]
	estimates: CodingEstimate[]
	complexity: { time: string; space: string; justification: string; quote: CodingEvidence | null } | null
	observations: { line: number | null; text: string }[]
	consider_cases: string[]
	testing_notes: string[]
	hints_used: number
	explanation_answers: number
}

export type CodingFeedback = {
	session_id: number
	rubric_version: "coding-v1-unverified"
	review_only: true
	verification: "not_verified"
	uncertainty_note: string
	disclaimer: string
	provider: string | null
	created_at: string
	problems: CodingProblemFeedback[]
}

export type CodingSubmission = {
	id: number
	attempt_seq: number
	kind: "visible" | "custom" | "graded" | "auto"
	language: string
	status: "queued" | "running" | "completed" | "error" | "not_executed" | "rejected"
	verdict: string | null
	verification: "verified" | "not_verified"
	is_best: boolean
	created_at: string
}

export type CodingHint = {
	seq: number
	hint_number: number
	hint: string
	hints_used: number
	hints_available: number
	/** Phase 8.3: "ai" hints are tailored to the current code and shown once. */
	source?: "authored" | "ai"
}

export type CodingCreateChoices = { languages: string[]; topics: string[]; problem_key?: string }

const sessions = "/mock-interview/sessions"

export const codingApi = {
	catalogPath: "/mock-interview/coding/catalog",
	statePath: (id: number) => `${sessions}/${id}/coding`,
	state: (id: number) => api.get<CodingState>(`${sessions}/${id}/coding`),
	saveDraft: (id: number, seq: number, language: string, source: string, draftRev: number) =>
		api.put<{ seq: number; draft_rev: number; saved_at: string }>(
			`${sessions}/${id}/coding/attempts/${seq}/draft`,
			{ language, source, draft_rev: draftRev },
		),
	submit: (id: number, seq: number, language: string, source: string, idempotencyKey: string) =>
		api.post<CodingSubmission>(`${sessions}/${id}/coding/attempts/${seq}/submissions`, {
			language,
			source,
			idempotency_key: idempotencyKey,
		}),
	submissions: (id: number, seq: number) =>
		api.get<CodingSubmission[]>(`${sessions}/${id}/coding/attempts/${seq}/submissions`),
	hint: (id: number, seq: number) => api.post<CodingHint>(`${sessions}/${id}/coding/attempts/${seq}/hints`),
	done: (id: number, seq: number) => api.post<CodingState>(`${sessions}/${id}/coding/attempts/${seq}/done`),
	// Phase 8.3: review-only feedback (404 until generated; POST generates once).
	feedback: (id: number) => api.get<CodingFeedback>(`${sessions}/${id}/coding/feedback`),
	generateFeedback: (id: number) => api.post<CodingFeedback>(`${sessions}/${id}/coding/feedback`),
}
