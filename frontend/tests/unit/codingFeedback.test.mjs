// Phase 8.3 unit tests: explanation turns, AI hints and review-only feedback (no score anywhere).
// Run: npm run test:unit
import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { dirname, join } from "node:path"
import { describe, it } from "node:test"
import { fileURLToPath } from "node:url"
import {
	EXPLANATION_MAX_CHARS,
	estimateLabel,
	evidenceSource,
	hiddenAiHints,
	objectiveLabel,
	pendingExplanation,
} from "../../src/components/mock-interview/coding/codingFlow.ts"

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..")
const CODING = join(ROOT, "src", "components", "mock-interview", "coding")
/** Source without comments, so a comment that names a banned API is not a hit. */
const code = (text) => text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:"'`])\/\/.*$/gm, "$1")
const src = (f) => code(readFileSync(join(CODING, f), "utf8"))

describe("Phase 8.3: explanation turns", () => {
	const turns = [
		{ turn_seq: 3, question: "Why linear?", answer_text: "Because one pass.", answered_at: "x" },
		{ turn_seq: 4, question: "How tested?", answer_text: null, answered_at: null },
	]
	it("only an explaining problem has a pending question, oldest unanswered first", () => {
		assert.equal(pendingExplanation({ status: "explaining", explanation: turns })?.turn_seq, 4)
		assert.equal(pendingExplanation({ status: "closed", explanation: turns }), null)
		assert.equal(pendingExplanation({ status: "explaining", explanation: [turns[0]] }), null)
		assert.equal(pendingExplanation({ status: "explaining" }), null)
	})
	it("answers are limited like the server's answer schema", () => {
		assert.equal(EXPLANATION_MAX_CHARS, 5000)
	})
	it("explanations are typed only (decision B1-a): no speech in the panel", () => {
		const panel = src("ExplanationPanel.tsx")
		for (const banned of ["useSpeech", "VoiceAnswerBox", "SpeechRecognition", "getUserMedia", 'source: "stt"']) {
			assert.ok(!panel.includes(banned), banned)
		}
		assert.match(panel, /<textarea/)
	})
	it("answers go through the existing /answer route without a source (typed)", () => {
		const room = src("CodingRoom.tsx")
		assert.match(room, /mockInterviewApi\.answer\(sessionId, turnSeq, text\)/)
	})
})

describe("Phase 8.3: AI hints are shown once", () => {
	const attempt = (used, revealed) => ({ hints_used: used, problem: { hints_revealed: Array(revealed).fill("h") } })
	it("counts AI hints opened earlier that cannot be shown again", () => {
		assert.equal(hiddenAiHints(attempt(3, 3), 0), 0)
		assert.equal(hiddenAiHints(attempt(4, 3), 0), 1)
		assert.equal(hiddenAiHints(attempt(4, 3), 1), 0)
		assert.equal(hiddenAiHints(attempt(2, 3), 0), 0)
	})
	it("AI hint text is never written to browser storage", () => {
		for (const f of ["CodingRoom.tsx", "ProblemPanel.tsx", "CodingFeedback.tsx", "ExplanationPanel.tsx"]) {
			const s = src(f)
			assert.ok(!/localStorage|sessionStorage|indexedDB/.test(s), f)
		}
	})
})

describe("Phase 8.3: review-only feedback never shows a score", () => {
	it("AI categories are labelled AI estimates, or Not assessed", () => {
		assert.equal(estimateLabel({ score: 4, assessed: true }), "AI estimate: 4 / 5")
		assert.equal(estimateLabel({ score: null, assessed: false }), "Not assessed")
		assert.equal(estimateLabel({ score: 3, assessed: false }), "Not assessed")
		assert.equal(estimateLabel({ score: null, assessed: true }), "Not assessed")
	})
	it("objective categories are always not verified", () => {
		assert.match(objectiveLabel({ status: "not_verified" }), /^Not verified/)
		assert.match(objectiveLabel({ status: "anything" }), /^Not verified/)
	})
	it("evidence says where it comes from", () => {
		assert.equal(evidenceSource({ line: 12 }), "Your code, line 12")
		assert.equal(evidenceSource({ line: null }), "From your explanation")
	})
	it("the feedback view has no total, overall or percentage score and renders text only", () => {
		const view = src("CodingFeedback.tsx")
		for (const banned of [
			"overall_score",
			"problem_score",
			"session_score",
			"weight_percent *",
			"reduce(",
			"dangerouslySetInnerHTML",
			"/ 100",
			"%</",
		]) {
			assert.ok(!view.includes(banned), banned)
		}
		assert.match(view, /estimateLabel\(e\)/)
		assert.match(view, /\{e\.uncertainty\}/) // every individual AI estimate shows its uncertainty
		assert.match(view, /objectiveLabel\(o\)/)
		assert.match(view, /uncertainty_note/)
	})
	it("the feedback types carry no score field beyond the per-category estimate", () => {
		const api = readFileSync(join(CODING, "codingApi.ts"), "utf8")
		const feedbackTypes = api.slice(api.indexOf("export type CodingEvidence"), api.indexOf("export type CodingSubmission"))
		assert.ok(feedbackTypes.length > 0)
		assert.equal((feedbackTypes.match(/score/g) ?? []).length, 1, "only CodingEstimate.score")
	})
})
