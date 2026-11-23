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
}

export type CodingState = {
	session_id: number
	status: string
	languages: string[]
	review_only: boolean
	remaining_seconds: number | null
	graded_submissions_per_problem: number
	attempts: CodingAttempt[]
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
}
