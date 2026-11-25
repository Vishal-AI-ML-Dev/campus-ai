// Phase 10.5: frontend error reports are content-free and off by default.
// Run: npm run test:unit
import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { dirname, join } from "node:path"
import { describe, it } from "node:test"
import { fileURLToPath } from "node:url"
import {
	MAX_REPORTS_PER_PAGE,
	buildReport,
	errorName,
	normaliseRoute,
	reportingEnabled,
} from "../../src/components/observability/clientErrors.ts"

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..")

describe("client error reports (Phase 10.5)", () => {
	it("is off unless the flag is exactly 'true'", () => {
		for (const v of [undefined, "", "false", "TRUE", "1", "yes"]) assert.equal(reportingEnabled(v), false)
		assert.equal(reportingEnabled("true"), true)
	})

	it("normalises routes: ids replaced, no query or fragment", () => {
		assert.equal(normaliseRoute("/dashboard/mock-interview/42?token=abc#x"), "/dashboard/mock-interview/:id")
		assert.equal(normaliseRoute("/a/123e4567-e89b-12d3-a456-426614174000/b"), "/a/:id/b")
		assert.equal(normaliseRoute("/students/priya.sharma@example.com"), "/students/:x")
		assert.equal(normaliseRoute(""), "/")
	})

	it("never carries the error message, stack or other fields", () => {
		const secret = "Priya Sharma answered: my phone is 9876543210"
		const err = new TypeError(secret)
		const report = buildReport("error", err, "/dashboard/mentor?q=" + secret, "v1.2.3", "abc123")
		assert.deepEqual(Object.keys(report).sort(), ["digest", "kind", "name", "release", "route"])
		assert.equal(report.name, "TypeError")
		assert.ok(!JSON.stringify(report).includes("Priya"))
		assert.ok(!JSON.stringify(report).includes("9876543210"))
	})

	it("rejects odd names, releases and digests", () => {
		assert.equal(errorName("a string reason with data"), "string")
		const odd = new Error("x")
		odd.name = "<script>alert(1)</script>"
		assert.equal(errorName(odd), "Error")
		const r = buildReport("render", odd, "/", "bad release!", "dig-est!")
		assert.equal(r.release, "")
		assert.equal(r.digest, "")
	})

	it("caps reports per page and is mounted only behind the flag", () => {
		assert.ok(MAX_REPORTS_PER_PAGE <= 10)
		const layout = readFileSync(join(ROOT, "src", "app", "layout.tsx"), "utf8")
		assert.match(layout, /CLIENT_ERRORS_ON \? <ClientErrorReporter \/> : null/)
		const reporter = readFileSync(join(ROOT, "src", "components", "observability", "ClientErrorReporter.tsx"), "utf8")
		assert.ok(!/\.message|\.stack/.test(reporter), "reporter must not read message/stack")
	})
})
