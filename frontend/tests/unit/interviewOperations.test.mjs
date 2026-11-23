// Phase 7.8: Interview Operations UI - display logic + source guards.
// Run: npm run test:unit
import assert from "node:assert/strict"
import { readdirSync, readFileSync } from "node:fs"
import { dirname, join, relative } from "node:path"
import { describe, it } from "node:test"
import { fileURLToPath } from "node:url"
import {
	barHeights,
	canEditControls,
	canViewOps,
	describeCost,
	formatCount,
	parseLimitInput,
	quotaMessage,
	tokenUsageHint,
	validateLimit,
} from "../../src/components/interview-operations/opsLogic.ts"

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..")
const OPS_DIR = join(ROOT, "src", "components", "interview-operations")
const PAGE = join(ROOT, "src", "app", "dashboard", "interview-operations", "page.tsx")
const QUOTA = join(ROOT, "src", "components", "mock-interview", "RemainingQuota.tsx")

function code(text) {
	return text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:"'`])\/\/.*$/gm, "$1")
}
const files = [...readdirSync(OPS_DIR).map((n) => join(OPS_DIR, n)), PAGE, QUOTA].map((p) => ({
	name: relative(ROOT, p).replaceAll("\\", "/"),
	src: code(readFileSync(p, "utf8")),
}))

describe("Phase 7.8: cost display (decision P7-D9)", () => {
	it("never shows a number when pricing is not configured", () => {
		const d = describeCost({ status: "pricing_not_configured", currency: null, total_micros: null,
			priced_calls: 0, unpriced_calls: 12, price_versions: [] })
		assert.equal(d.headline, "Not configured")
		assert.doesNotMatch(d.headline + d.detail, /\d/)
		assert.equal(describeCost(null).headline, "Unavailable")
	})
	it("formats an estimate from micro-units and says it is an estimate (TEST-ONLY numbers)", () => {
		const d = describeCost({ status: "estimated", currency: "USD", total_micros: 4500,
			priced_calls: 3, unpriced_calls: 0, price_versions: ["test-fixture-1"] })
		assert.equal(d.headline, "USD 0.0045 (estimate)")
		assert.match(d.detail, /test-fixture-1/)
	})
	it("marks partial and mixed-currency results instead of hiding them", () => {
		assert.match(describeCost({ status: "partial", currency: "USD", total_micros: 2_500_000,
			priced_calls: 2, unpriced_calls: 1, price_versions: ["v"] }).headline, /^At least USD 2\.50/)
		assert.equal(describeCost({ status: "mixed_currency", currency: null, total_micros: null,
			priced_calls: 2, unpriced_calls: 0, price_versions: [] }).headline, "Not shown")
	})
	it("shows unknown counts as unknown, not 0", () => {
		assert.equal(formatCount(null), "unknown")
		assert.equal(formatCount(0), "0")
	})
})

describe("Phase 7.8: roles (decision P7-D3)", () => {
	it("admin and TPO view; only admin with server permission edits", () => {
		assert.ok(canViewOps("admin") && canViewOps("tpo"))
		for (const r of ["student", "teacher", "recruiter", undefined]) assert.equal(canViewOps(r), false)
		assert.equal(canEditControls("admin", true), true)
		assert.equal(canEditControls("admin", false), false)
		assert.equal(canEditControls("tpo", true), false)
	})
	it("the controls form is gated by canEditControls and the API has a single PUT", () => {
		const panel = files.find((f) => f.name.endsWith("ControlsPanel.tsx")).src
		assert.match(panel, /canEditControls\(role, c\.can_edit\)/)
		const puts = files.flatMap((f) => [...f.src.matchAll(/api\.put/g)].map(() => f.name))
		assert.deepEqual(puts, ["src/components/interview-operations/opsApi.ts"])
	})
})

describe("Phase 7.8: limits and quota text", () => {
	const platform = { student_sessions_per_day: 10, tenant_sessions_per_month: 100, tenant_tokens_per_month: null }
	it("parses limits strictly", () => {
		assert.equal(parseLimitInput(""), null)
		assert.equal(parseLimitInput(" 7 "), 7)
		for (const bad of ["0", "-1", "1.5", "abc", "1e3"]) assert.equal(parseLimitInput(bad), "invalid")
	})
	it("refuses values above the platform limit (institutes only tighten)", () => {
		assert.equal(validateLimit("student_sessions_per_day", "10", platform), null)
		assert.match(validateLimit("student_sessions_per_day", "11", platform), /at most 10/)
		assert.equal(validateLimit("tenant_tokens_per_month", "999999999", platform), null)
	})
	it("tells the student only their own situation", () => {
		const base = { available: true, reason: null, daily_limit: 10, used_today: 3, remaining_today: 7,
			voice_allowed: true, video_allowed: true }
		assert.equal(quotaMessage(base), "You can start 7 more practice interviews today.")
		assert.equal(quotaMessage({ ...base, remaining_today: 1 }), "You can start 1 more practice interview today.")
		assert.match(quotaMessage({ ...base, available: false, reason: "paused" }), /paused/)
		assert.match(quotaMessage({ ...base, available: false, reason: "daily_limit", remaining_today: 0 }), /all 10/)
		assert.match(quotaMessage({ ...base, available: false, reason: "institute_limit" }), /institute/)
		assert.equal(quotaMessage(null), null)
	})
	it("scales chart bars safely", () => {
		assert.deepEqual(barHeights([0, 0]), [0, 0])
		assert.deepEqual(barHeights([1, 2, 4]), [25, 50, 100])
	})

	it("says when token totals leave out unknown or partial usage (spec 15.12 a)", () => {
		assert.equal(tokenUsageHint(0, 0), "Provider-reported counts")
		assert.equal(tokenUsageHint(undefined, undefined), "Provider-reported counts")
		assert.equal(tokenUsageHint(2, 0), "2 call(s) with unknown usage (not counted)")
		assert.equal(tokenUsageHint(0, 3), "3 partly reported (not counted)")
		assert.equal(tokenUsageHint(2, 3), "2 call(s) with unknown usage · 3 partly reported (not counted)")
	})
})

describe("Phase 7.8: privacy and placement guards", () => {
	it("never reads interview content fields", () => {
		const content = /\.(answer_text|answer|question|questions|transcript|job_description|resume_text|plan_json|plan|strengths|weaknesses|recommendations)\b/
		for (const f of files) assert.doesNotMatch(f.src, content, f.name)
	})
	it("lives outside /dashboard/mock-interview (the camera policy never applies)", () => {
		assert.ok(!PAGE.replaceAll("\\", "/").includes("/dashboard/mock-interview/"))
	})
	it("is behind its own default-off build flag", () => {
		const apiSrc = files.find((f) => f.name.endsWith("opsApi.ts")).src
		assert.match(apiSrc, /NEXT_PUBLIC_MOCK_INTERVIEW_OPS_ENABLED === "true"/)
		assert.match(apiSrc, /NEXT_PUBLIC_MOCK_INTERVIEW_ENABLED === "true" &&/)
	})
})
