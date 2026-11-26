// Phase 9.2-9.4 unit tests: Placement Hub logic, privacy and flag guards.
// Run: npm run test:unit
import assert from "node:assert/strict"
import { readFileSync, readdirSync, statSync } from "node:fs"
import { dirname, join } from "node:path"
import { describe, it } from "node:test"
import { fileURLToPath } from "node:url"
import {
	APP_MOVES,
	BULK_MAX,
	CAMPAIGN_MOVES,
	DRIVE_MOVES,
	bulkCandidates,
	importFileProblem,
	isoToLocalInput,
	localInputToIso,
	noteRequired,
	parsePracticePrefill,
	parseStatusCounts,
	parseYears,
	ratio,
	toggleId,
} from "../../src/components/placement-hub/placementLogic.ts"

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..")
const code = (text) => text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:"'`])\/\/.*$/gm, "$1")
function walk(dir) {
	const out = []
	for (const f of readdirSync(join(ROOT, dir))) {
		const rel = `${dir}/${f}`
		if (statSync(join(ROOT, rel)).isDirectory()) out.push(...walk(rel))
		else if (/\.(ts|tsx)$/.test(f)) out.push({ name: rel, src: code(readFileSync(join(ROOT, rel), "utf8")) })
	}
	return out
}
const hubFiles = [...walk("src/components/placement-hub"), ...walk("src/app/dashboard/placement-hub")]

describe("Phase 9.2: status rules mirror the server", () => {
	it("withdrawn is never a staff move and is final", () => {
		assert.deepEqual(APP_MOVES.withdrawn, [])
		for (const moves of Object.values(APP_MOVES)) assert.ok(!moves.includes("withdrawn"))
		assert.deepEqual(APP_MOVES.applied, ["shortlisted", "rejected", "selected"])
		assert.ok(noteRequired("rejected") && noteRequired("selected") && !noteRequired("applied"))
	})
	it("drive lifecycle terminal states have no moves", () => {
		assert.deepEqual(DRIVE_MOVES.completed, [])
		assert.deepEqual(DRIVE_MOVES.cancelled, [])
		assert.deepEqual(DRIVE_MOVES.draft, ["published", "cancelled"])
		assert.deepEqual(CAMPAIGN_MOVES.archived, [])
	})
	it("bulk candidates split by allowed transition and only include selected rows", () => {
		const rows = [
			{ application_id: 1, status: "applied" },
			{ application_id: 2, status: "selected" },
			{ application_id: 3, status: "withdrawn" },
			{ application_id: 4, status: "applied" },
		]
		const { valid, skipped } = bulkCandidates(rows, new Set([1, 2, 3]), "shortlisted")
		assert.deepEqual(valid.map((r) => r.application_id), [1])
		assert.deepEqual(skipped.map((r) => r.application_id), [2, 3])
		assert.equal(BULK_MAX, 200)
		assert.deepEqual([...toggleId(new Set([1]), 1)], [])
		assert.deepEqual([...toggleId(new Set([1]), 2)].sort(), [1, 2])
	})
	it("status-count header parsing ignores junk", () => {
		assert.deepEqual(parseStatusCounts("applied:2,shortlisted:1,bogus:3,selected:x"), { applied: 2, shortlisted: 1 })
		assert.deepEqual(parseStatusCounts(null), {})
	})
})

describe("Phase 9.2: import and form helpers", () => {
	it("client file checks (server re-validates)", () => {
		assert.equal(importFileProblem(null), "Choose a .csv or .xlsx file.")
		assert.match(importFileProblem({ name: "a.xls", size: 10 }), /Only/)
		assert.match(importFileProblem({ name: "a.csv", size: 0 }), /empty/)
		assert.match(importFileProblem({ name: "a.XLSX", size: 3 * 1024 * 1024 }), /2 MB/)
		assert.equal(importFileProblem({ name: "marks.csv", size: 10 }), null)
	})
	it("graduation years and datetime conversions", () => {
		assert.deepEqual(parseYears("2028, 2027 2027"), [2027, 2028])
		assert.equal(parseYears("27"), null)
		assert.equal(parseYears("2001,2002,2003,2004,2005,2006,2007"), null)
		assert.deepEqual(parseYears(""), [])
		const iso = localInputToIso("2026-11-30T10:00")
		assert.ok(iso && iso.endsWith("Z"))
		assert.equal(isoToLocalInput(iso), "2026-11-30T10:00")
		assert.equal(localInputToIso(""), null)
		assert.equal(ratio(1, 3), "33.3%")
		assert.equal(ratio(1, 0), "—")
	})
})

describe("Phase 9.4: practice prefill is bounded", () => {
	it("parses and cleans the backend link parameters", () => {
		const p = parsePracticePrefill("?prep_role=Data+Analyst&prep_company=Acme+%26+Co&prep_profile=12")
		assert.deepEqual(p, { role: "Data Analyst", company: "Acme & Co", profileId: 12 })
	})
	it("rejects bad ids, strips control characters and caps length", () => {
		assert.equal(parsePracticePrefill("?prep_profile=-1").profileId, null)
		assert.equal(parsePracticePrefill("?prep_profile=1e3").profileId, null)
		assert.equal(parsePracticePrefill("?prep_role=a%0Ab").role, "a b")
		assert.equal(parsePracticePrefill(`?prep_role=${"x".repeat(400)}`).role.length, 150)
		assert.deepEqual(parsePracticePrefill(""), { role: "", company: "", profileId: null })
	})
	it("the setup form only reads the prefill behind the placement build flag", () => {
		const form = code(readFileSync(join(ROOT, "src/components/mock-interview/InterviewSetupForm.tsx"), "utf8"))
		assert.match(form, /NEXT_PUBLIC_PLACEMENT_MANAGEMENT_ENABLED !== "true"/)
	})
})

describe("Phase 9.3: faculty privacy (P9-D7) in the UI layer", () => {
	it("no placement-hub file reads interview scores, reports, transcripts or answers", () => {
		const forbidden = /overall_score|category_scores|\/report\b|transcript|answer_text|recording|turn_eval|strengths_json/
		for (const f of hubFiles) assert.ok(!forbidden.test(f.src), f.name)
	})
	it("the progress type carries completion fields only", () => {
		const api = hubFiles.find((f) => f.name.endsWith("placementApi.ts")).src
		const block = api.slice(api.indexOf("export type ProgressRow"), api.indexOf("export type MyCampaign"))
		const keys = [...block.matchAll(/^\s*(\w+):/gm)].map((m) => m[1]).sort()
		assert.deepEqual(keys, ["assigned_at", "completed_at", "completion", "full_name", "section_name", "student_id"])
	})
})

describe("Phase 9.2: flag-off safety", () => {
	it("the build flag defaults off and the gate makes no request without it", () => {
		const api = hubFiles.find((f) => f.name.endsWith("placementApi.ts")).src
		assert.match(api, /NEXT_PUBLIC_PLACEMENT_MANAGEMENT_ENABLED === "true"/)
		assert.match(api, /const ask = PLACEMENT_HUB_BUILD_ENABLED && allowedRole/)
		assert.match(api, /if \(!ask\) return/)
	})
	it("no hub file renders HTML from data", () => {
		for (const f of hubFiles) assert.ok(!/dangerouslySetInnerHTML|innerHTML\s*=/.test(f.src), f.name)
	})
})
