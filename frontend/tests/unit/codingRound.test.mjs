// Phase 8.2 unit tests: coding round logic (review-only) and static guards for the coding UI.
// Run: npm run test:unit
import assert from "node:assert/strict"
import { readdirSync, readFileSync } from "node:fs"
import { dirname, join } from "node:path"
import { describe, it } from "node:test"
import { fileURLToPath } from "node:url"
import {
	EDITOR_GRAMMAR,
	LANGUAGE_LABELS,
	attemptOpen,
	codeAfterLanguageSwitch,
	codingBuildEnabled,
	codingStage,
	isCodingSession,
	newIdempotencyKey,
	sizeLabel,
	submissionStatusText,
	submitBlockedReason,
	submittedAnnouncement,
	utf8Bytes,
	visibleInterviewTypes,
} from "../../src/components/mock-interview/coding/codingFlow.ts"

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..")
const CODING = join(ROOT, "src", "components", "mock-interview", "coding")
const MI = join(ROOT, "src", "components", "mock-interview")
const read = (dir, f) => readFileSync(join(dir, f), "utf8")
/** Source without comments, so a comment that names a banned API is not a hit. */
const code = (text) => text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:"'`])\/\/.*$/gm, "$1")
const codingFiles = readdirSync(CODING)
	.filter((f) => /\.(ts|tsx)$/.test(f))
	.map((f) => ({ name: f, src: code(read(CODING, f)) }))
const MAX = 64 * 1024

describe("Phase 8.2: build flag and routing", () => {
	it("coding needs both build flags, exactly 'true'", () => {
		assert.equal(codingBuildEnabled({}), false)
		assert.equal(codingBuildEnabled({ NEXT_PUBLIC_MOCK_INTERVIEW_CODING_ENABLED: "true" }), false)
		assert.equal(codingBuildEnabled({ NEXT_PUBLIC_MOCK_INTERVIEW_ENABLED: "true" }), false)
		assert.equal(
			codingBuildEnabled({ NEXT_PUBLIC_MOCK_INTERVIEW_ENABLED: "true", NEXT_PUBLIC_MOCK_INTERVIEW_CODING_ENABLED: "TRUE" }),
			false,
		)
		assert.equal(
			codingBuildEnabled({ NEXT_PUBLIC_MOCK_INTERVIEW_ENABLED: "true", NEXT_PUBLIC_MOCK_INTERVIEW_CODING_ENABLED: "true" }),
			true,
		)
	})

	it("the setup form only narrows the server's interview types", () => {
		const server = [{ value: "hr", label: "HR" }, { value: "technical", label: "Technical" }, { value: "coding", label: "Coding" }]
		assert.deepEqual(visibleInterviewTypes(server, false).map((t) => t.value), ["hr", "technical"])
		assert.deepEqual(visibleInterviewTypes(server, true).map((t) => t.value), ["hr", "technical", "coding"])
		// The build flag can never add what the server did not offer.
		assert.deepEqual(visibleInterviewTypes(server.slice(0, 2), true).map((t) => t.value), ["hr", "technical"])
		assert.deepEqual(visibleInterviewTypes(undefined, true), [])
	})

	it("coding sessions get their own screens; stage follows the server status", () => {
		assert.equal(isCodingSession({ interview_type: "coding" }), true)
		assert.equal(isCodingSession({ interview_type: "hr" }), false)
		assert.equal(codingStage({ status: "setup" }), "instructions")
		assert.equal(codingStage({ status: "in_progress" }), "room")
		for (const s of ["completed", "abandoned", "failed", "evaluating"]) assert.equal(codingStage({ status: s }), "finished")
		const page = read(join(ROOT, "src", "app", "dashboard", "mock-interview"), "page.tsx")
		assert.ok(page.indexOf("isCodingSession(session)") < page.indexOf('session.mode === "video"'), "coding is routed before video")
	})

	it("every language has a label and an editor grammar", () => {
		for (const l of ["python313", "cpp17", "java21", "c11"]) {
			assert.ok(LANGUAGE_LABELS[l], l)
			assert.ok(["python", "cpp", "java"].includes(EDITOR_GRAMMAR[l]), l)
		}
	})
})

describe("Phase 8.2: submit rules mirror the server (review-only)", () => {
	const open = { status: "working", submissions_remaining: 2 }

	it("sizes are UTF-8 bytes, like the server", () => {
		assert.equal(utf8Bytes("abc"), 3)
		assert.equal(utf8Bytes("é"), 2)
		assert.equal(sizeLabel(2048, MAX), "2.0 KB of 64 KB")
	})

	it("submit is blocked for closed problems, no submissions left, empty or oversized code, or while busy", () => {
		assert.equal(submitBlockedReason(open, "print(1)", MAX, false), null)
		assert.match(submitBlockedReason({ ...open, status: "closed" }, "x", MAX, false), /closed/)
		assert.match(submitBlockedReason({ ...open, submissions_remaining: 0 }, "x", MAX, false), /No graded submissions left/)
		assert.match(submitBlockedReason(open, "  \n", MAX, false), /Write some code/)
		assert.match(submitBlockedReason(open, "é".repeat(MAX / 2 + 1), MAX, false), /too large/)
		assert.ok(submitBlockedReason(open, "x", MAX, true))
		assert.equal(attemptOpen({ status: "pending" }), true)
		assert.equal(attemptOpen({ status: "abandoned" }), false)
	})

	it("a submission never shows a score or pass/fail in review-only mode", () => {
		const text = submissionStatusText({ status: "not_executed", verification: "not_verified", kind: "graded" })
		assert.match(text, /not run \(review-only\)/)
		assert.doesNotMatch(text, /pass|fail|score|correct|accepted|\d+\s*\/\s*\d+/i)
		assert.match(submissionStatusText({ status: "not_executed", verification: "not_verified", kind: "auto" }), /automatically/)
		assert.match(submissionStatusText({ status: "error", verification: "not_verified", kind: "graded" }), /saved/)
		assert.match(submittedAnnouncement(1), /1 graded submission left/)
		assert.match(submittedAnnouncement(2), /not run in review-only mode/)
	})

	it("switching language keeps edited code and swaps an untouched starter", () => {
		assert.deepEqual(codeAfterLanguageSwitch("A", "A", "B"), { code: "B", replaced: true })
		assert.deepEqual(codeAfterLanguageSwitch("  ", "A", "B"), { code: "B", replaced: true })
		assert.deepEqual(codeAfterLanguageSwitch("mine", "A", "B"), { code: "mine", replaced: false })
	})

	it("request keys match the server's format", () => {
		const re = /^[A-Za-z0-9_-]{8,64}$/
		assert.match(newIdempotencyKey(), re)
		assert.match(newIdempotencyKey(() => "ab"), re)
		assert.match(newIdempotencyKey(() => "x".repeat(200)), re)
		assert.match(newIdempotencyKey(() => "a b!c@d#e$f%g"), re)
	})
})

describe("Phase 8.2: coding UI guards", () => {
	it("covers the coding components", () => {
		for (const f of ["CodingRoom.tsx", "CodeEditor.tsx", "ProblemPanel.tsx", "SubmissionHistory.tsx", "ReviewOnlyNotice.tsx",
			"CodingInstructions.tsx", "CodingFinished.tsx", "CodingWorkspace.tsx", "CodingSetupFields.tsx", "codingApi.ts", "codingFlow.ts"]) {
			assert.ok(codingFiles.some((x) => x.name === f), f)
		}
	})

	it("code is never written to browser storage", () => {
		for (const f of codingFiles) assert.doesNotMatch(f.src, /localStorage|sessionStorage|indexedDB|document\.cookie/, f.name)
	})

	it("no proctoring: nothing watches visibility, focus, paste or the clipboard (P8-D15)", () => {
		const banned = [/visibilitychange/, /addEventListener\(\s*["'](paste|copy|cut|blur|focusout|beforeunload)/,
			/onPaste|onCopy|onCut/, /navigator\.clipboard/, /document\.hidden/, /hasFocus\(/, /getUserMedia|getDisplayMedia/]
		for (const f of codingFiles) for (const re of banned) assert.doesNotMatch(f.src, re, `${f.name}: ${re}`)
	})

	it("nothing in the coding UI runs code or fakes a result", () => {
		for (const f of codingFiles) {
			assert.doesNotMatch(f.src, /\beval\(|new Function\(|WebAssembly|pyodide|new Worker\(/i, f.name)
		}
		const room = codingFiles.find((x) => x.name === "CodingRoom.tsx").src
		// Run exists only as a disabled button explaining why (B1-c, P8-D8).
		assert.match(room, /disabled\s+aria-describedby="coding-run-why"[\s\S]{0,200}>\s*Run\s*</)
		assert.doesNotMatch(room, /runs`|\/runs/)
		const api = codingFiles.find((x) => x.name === "codingApi.ts").src
		assert.doesNotMatch(api, /\/runs|custom_input|hidden|expected_complexity|reference/)
	})

	it("CodeMirror is loaded lazily (dynamic import only) with a textarea fallback", () => {
		for (const f of codingFiles) assert.doesNotMatch(f.src, /^\s*import[^(]*["']@codemirror\//m, f.name)
		const editor = codingFiles.find((x) => x.name === "CodeEditor.tsx").src
		assert.match(editor, /import\("@codemirror\/view"\)/)
		assert.match(editor, /<textarea/)
		assert.match(editor, /status === "failed"/)
		const others = readdirSync(MI).filter((f) => /\.(ts|tsx)$/.test(f)).map((f) => code(read(MI, f)))
		for (const src of others) assert.doesNotMatch(src, /@codemirror\//)
	})

	it("accessibility: keyboard exit, live announcements, tabs, timer, end control, focus on finish", () => {
		const room = codingFiles.find((x) => x.name === "CodingRoom.tsx").src
		assert.match(room, /press Esc, then Tab/)
		assert.match(room, /className="sr-only" aria-live="polite"/)
		assert.match(room, /role="tablist"/)
		assert.match(room, /role="tab"/)
		assert.match(room, /aria-selected=\{tab === t\.id\}/)
		assert.match(room, /e\.key !== "ArrowRight" && e\.key !== "ArrowLeft"/)
		assert.match(room, /<Countdown /)
		assert.match(room, /<EndInterviewControl busy=\{busy !== null\} ending=\{busy === "finish"\} onConfirm=\{finish\} \/>/)
		assert.match(room, /min-h-\[44px\]/)
		assert.match(codingFiles.find((x) => x.name === "CodingFinished.tsx").src, /ref=\{headingRef\} tabIndex=\{-1\}/)
		assert.match(codingFiles.find((x) => x.name === "SubmissionHistory.tsx").src, /<caption className="sr-only">/)
	})

	it("drafts autosave to the server", () => {
		const room = codingFiles.find((x) => x.name === "CodingRoom.tsx").src
		assert.match(room, /AUTOSAVE_IDLE_MS/)
		assert.match(room, /codingApi\.saveDraft\(/)
	})
})
