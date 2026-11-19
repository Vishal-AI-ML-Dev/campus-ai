/**
 * AI Mock Interview (Phase 3, text) - types + API calls.
 *
 * Backend: /mock-interview/* (see docs/mock-interview/API.md). Every call is
 * safe to retry: start/next/finish/abandon are idempotent and an answer is
 * keyed by its question's `turn_seq`, so re-sending the same text is a no-op.
 */

import { api, ApiError } from "@/lib/api"

export type Choice = { value: string; label: string }

export type ResumeOption = {
	id: number
	title: string
	target_role: string | null
	is_primary: boolean
	created_at: string
}

export type InterviewOptions = {
	interview_types: Choice[]
	difficulties: Choice[]
	durations_minutes: number[]
	languages: Choice[]
	resumes: ResumeOption[]
	job_description_max_chars: number
	answer_max_chars: number
}

export type InterviewTurn = {
	seq: number
	topic: string | null
	question: string
	is_follow_up: boolean
	answer_text: string | null
	answered_at: string | null
}

export type InterviewSession = {
	id: number
	interview_type: "hr" | "technical" | string
	mode: string
	status: "setup" | "in_progress" | "evaluating" | "completed" | "abandoned" | "failed"
	target_role: string | null
	company_name: string | null
	difficulty: string
	language: string
	duration_minutes: number
	created_at: string
	started_at: string | null
	completed_at: string | null
	resume_id: number | null
	has_job_description: boolean
	awaiting: "start" | "answer" | "next_question" | "none"
	current_turn_seq: number | null
	remaining_seconds: number | null
	progress: {
		answered: number
		question_budget: number | null
		topics_total: number | null
		topics_covered: number
	}
	turns: InterviewTurn[]
	closing_message: string | null
	end_reason: string | null
}

export type CreateInterviewInput = {
	interview_type: string
	difficulty: string
	duration_minutes: number
	target_role: string
	company_name?: string
	resume_id?: number
	job_description?: string
	language: string
}

const base = "/mock-interview/sessions"

export const mockInterviewApi = {
	create: (input: CreateInterviewInput) =>
		api.post<InterviewSession>(base, input as unknown as Record<string, unknown>),
	get: (id: number) => api.get<InterviewSession>(`${base}/${id}`),
	start: (id: number) => api.post<InterviewSession>(`${base}/${id}/start`),
	answer: (id: number, turnSeq: number, answer: string) =>
		api.post<InterviewSession>(`${base}/${id}/answer`, { turn_seq: turnSeq, answer }),
	next: (id: number) => api.post<InterviewSession>(`${base}/${id}/next`),
	finish: (id: number) => api.post<InterviewSession>(`${base}/${id}/finish`),
	abandon: (id: number) => api.post<InterviewSession>(`${base}/${id}/abandon`),
}

/** A readable message for any failure, including offline / dropped connections. */
export function describeError(err: unknown, fallback: string): string {
	if (err instanceof ApiError) return err.message
	if (err instanceof TypeError) return "Network problem. Check your connection and try again."
	return fallback
}

export function labelFor(choices: Choice[] | undefined, value: string): string {
	return choices?.find((c) => c.value === value)?.label ?? value
}

export const TYPE_LABELS: Record<string, string> = { hr: "HR", technical: "Technical" }
export const DIFFICULTY_LABELS: Record<string, string> = {
	easy: "Beginner",
	medium: "Intermediate",
	hard: "Advanced",
}
export const LANGUAGE_LABELS: Record<string, string> = {
	en: "English",
	hi: "Hindi",
	hinglish: "Hinglish",
}

/** Per-tab draft of an unsent answer (survives a refresh; best-effort). */
export function draftKey(sessionId: number, seq: number): string {
	return `mock-interview:draft:${sessionId}:${seq}`
}

export function readDraft(key: string): string {
	try {
		return window.sessionStorage.getItem(key) ?? ""
	} catch {
		return ""
	}
}

export function writeDraft(key: string, value: string): void {
	try {
		if (value) window.sessionStorage.setItem(key, value)
		else window.sessionStorage.removeItem(key)
	} catch {
		// storage blocked (private mode etc.): drafts are a convenience only
	}
}
