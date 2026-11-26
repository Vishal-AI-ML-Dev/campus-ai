// Phase 6.5 unit tests: report and history integration for video interviews.
// The report itself is mode-independent (backend parity: tests/test_mock_interview_report_parity.py);
// the frontend only adds the D9 note and the history label. Run: npm run test:unit
import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { dirname, join } from "node:path"
import { describe, it } from "node:test"
import { fileURLToPath } from "node:url"

const MI = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "src", "components", "mock-interview")
const read = (f) => readFileSync(join(MI, f), "utf8")

describe("Phase 6.5: video report integration", () => {
	it("history labels voice and video; text gets no label (unchanged)", () => {
		const h = read("InterviewHistory.tsx")
		assert.match(h, /\{item\.mode === "voice" \? " · Voice" : item\.mode === "video" \? " · Video" : ""\}/)
	})

	it("the D9 camera note is shown for video interviews only", () => {
		const f = read("InterviewFinished.tsx")
		const notes = f.match(/session\.mode === "video"/g) ?? []
		assert.equal(notes.length, 1)
		assert.match(f, /not recorded, uploaded or\s+analysed/)
		assert.match(f, /based only on the answer text you\s+submitted/)
	})

	it("the report panel itself has no mode-specific behaviour", () => {
		const r = read("InterviewReport.tsx")
		assert.doesNotMatch(r, /\bmode\b|video|camera/i)
	})

	it("report API helpers are the same for every mode (no video-specific endpoint or field)", () => {
		const api = read("mockInterviewApi.ts")
		assert.match(api, /getReport: \(id: number\) => api\.get<InterviewReport>\(`\$\{base\}\/\$\{id\}\/report`\)/)
		assert.match(api, /generateReport: \(id: number\) => api\.post<InterviewReport>\(`\$\{base\}\/\$\{id\}\/report`\)/)
		assert.doesNotMatch(api, /report[^\n]*(video|camera|frame)/i)
	})
})
