// Phase 9 closure L2: legacy TPO applicant pages never offer decisions on withdrawn rows.
// Run: npm run test:unit
import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { dirname, join } from "node:path"
import { describe, it } from "node:test"
import { fileURLToPath } from "node:url"
import {
	LEGACY_DECISIONS,
	WITHDRAWN_FINAL_NOTE,
	isWithdrawn,
	legacyDecisionDisabled,
} from "../../src/lib/legacyApplicationActions.ts"

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..")
const read = (rel) => readFileSync(join(ROOT, rel), "utf8")
const LEGACY_PAGES = ["src/app/dashboard/drives/[id]/page.tsx", "src/app/dashboard/applications/page.tsx"]

describe("L2: legacy decision rules", () => {
	it("withdrawn disables every decision", () => {
		for (const target of LEGACY_DECISIONS) assert.equal(legacyDecisionDisabled("withdrawn", target), true)
		assert.equal(isWithdrawn("withdrawn"), true)
	})

	it("applied keeps all three decisions available (unchanged)", () => {
		for (const target of LEGACY_DECISIONS) assert.equal(legacyDecisionDisabled("applied", target), false)
	})

	it("shortlisted / selected / rejected keep the existing same-status rule only", () => {
		const expected = {
			shortlisted: { shortlisted: true, selected: false, rejected: false },
			selected: { shortlisted: false, selected: true, rejected: false },
			rejected: { shortlisted: false, selected: false, rejected: true },
		}
		for (const [current, row] of Object.entries(expected)) {
			assert.equal(isWithdrawn(current), false)
			for (const [target, disabled] of Object.entries(row)) {
				assert.equal(legacyDecisionDisabled(current, target), disabled, `${current} -> ${target}`)
			}
		}
	})

	it("the final-withdrawal note says it is final", () => {
		assert.match(WITHDRAWN_FINAL_NOTE, /final/i)
	})
})

describe("L2: wiring in both legacy TPO pages", () => {
	const component = read("src/components/applications/LegacyDecisionButtons.tsx")

	it("buttons use native disabled (not focusable or activatable by keyboard) and describe the reason", () => {
		assert.match(component, /disabled=\{legacyDecisionDisabled\(status, target\)\}/)
		assert.match(component, /aria-describedby=\{withdrawn \? noteId : undefined\}/)
		assert.match(component, /type="button"/)
		// The click handler re-checks the rule, so onDecide never runs for a disabled decision.
		assert.match(component, /if \(!legacyDecisionDisabled\(status, target\)\) onDecide\(target\)/)
	})

	for (const page of LEGACY_PAGES) {
		it(`${page} uses the shared buttons and refuses withdrawn rows in decide()`, () => {
			const src = read(page)
			assert.match(src, /<LegacyDecisionButtons/)
			assert.match(src, /if \(isWithdrawn\(applicant\.status\)\) return/)
			// No leftover inline decision buttons with the old same-status-only rule.
			assert.doesNotMatch(src, /disabled=\{applicant\.status === "(shortlisted|selected|rejected)"\}/)
			// Read-only information stays: status badge and verified snapshot.
			assert.match(src, /\{applicant\.status\}/)
			assert.match(src, /Verified skills \{applicant\.verified_skills\}|Verified\s+skills \{applicant\.verified_skills\}/)
		})
	}
})
