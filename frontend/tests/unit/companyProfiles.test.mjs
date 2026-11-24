// Phase 8.4 unit tests: company context (student) and company profiles (admin/TPO).
// Run: npm run test:unit
import assert from "node:assert/strict"
import { existsSync, readFileSync, readdirSync } from "node:fs"
import { dirname, join } from "node:path"
import { describe, it } from "node:test"
import { fileURLToPath } from "node:url"
import {
	EVALUATION_NOTE,
	NO_COMPANY,
	choiceValue,
	companyCreateFields,
	parseChoice,
	sourceText,
} from "../../src/components/mock-interview/company/companyFlow.ts"
import { draftError, emptyDraft, toInput } from "../../src/components/interview-company-profiles/profileDraft.ts"

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..")
const code = (text) => text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:"'`])\/\/.*$/gm, "$1")
const dirFiles = (dir) =>
	readdirSync(join(ROOT, dir))
		.filter((f) => /\.(ts|tsx)$/.test(f))
		.map((f) => ({ name: `${dir}/${f}`, src: code(readFileSync(join(ROOT, dir, f), "utf8")) }))
const companyUi = [
	...dirFiles("src/components/mock-interview/company"),
	...dirFiles("src/components/interview-company-profiles"),
	...dirFiles("src/app/dashboard/interview-company-profiles"),
]

describe("Phase 8.4: sources are plain text, never links", () => {
	it("a public source shows label and domain only", () => {
		assert.equal(
			sourceText({ kind: "public_url", label: "Careers page", domain: "example.com" }),
			"Public page: Careers page (example.com)",
		)
		assert.equal(sourceText({ kind: "jd", label: "JD from the TPO" }), "Job description: JD from the TPO")
		assert.equal(sourceText({ kind: "drive", label: "Placement drive #3" }), "Placement drive: Placement drive #3")
		assert.ok(!sourceText({ kind: "public_url", label: "x", domain: "a.com", url: "https://a.com" }).includes("https"))
	})
	it("no company component renders a link, an iframe or HTML from data", () => {
		for (const f of companyUi) {
			for (const banned of ["<a ", "<Link", "href={", "<iframe", "dangerouslySetInnerHTML", "window.open", "fetch("]) {
				assert.ok(!f.src.includes(banned), `${f.name}: ${banned}`)
			}
		}
	})
	it("there is no company-fit or competency score anywhere in the UI", () => {
		for (const f of companyUi) assert.ok(!/fit_score|fitScore|company_fit|companyFit|competency_score|competencyScore/.test(f.src), f.name)
		assert.match(EVALUATION_NOTE, /no company-fit score/)
	})
})

describe("Phase 8.4: student picker", () => {
	it("encodes and parses the choice", () => {
		assert.equal(choiceValue(NO_COMPANY), "")
		assert.equal(choiceValue({ profileId: 7, templateKey: null, focusSkills: [] }), "p:7")
		assert.equal(choiceValue({ profileId: null, templateKey: "qa_sdet", focusSkills: [] }), "t:qa_sdet")
		assert.deepEqual(parseChoice("p:7"), { profileId: 7, templateKey: null, focusSkills: [] })
		assert.deepEqual(parseChoice("t:qa_sdet"), { profileId: null, templateKey: "qa_sdet", focusSkills: [] })
		assert.deepEqual(parseChoice("p:abc"), NO_COMPANY)
		assert.deepEqual(parseChoice(""), NO_COMPANY)
	})
	it("adds create fields only when a focus was chosen (request otherwise unchanged)", () => {
		assert.deepEqual(companyCreateFields(NO_COMPANY), {})
		assert.deepEqual(companyCreateFields({ profileId: null, templateKey: null, focusSkills: ["SQL"] }), {})
		assert.deepEqual(companyCreateFields({ profileId: 3, templateKey: null, focusSkills: [" SQL ", ""] }), {
			company_profile_id: 3,
			focus_skills: ["SQL"],
		})
		assert.deepEqual(companyCreateFields({ profileId: null, templateKey: "data_analyst", focusSkills: [] }), {
			role_template_key: "data_analyst",
		})
	})
	it("the picker is not offered for coding rounds", () => {
		const form = code(readFileSync(join(ROOT, "src/components/mock-interview/InterviewSetupForm.tsx"), "utf8"))
		assert.match(form, /\{coding \? null : \(\s*<CompanyContextPicker/)
	})
})

describe("Phase 8.4: admin/TPO profiles page", () => {
	it("lives outside /dashboard/mock-interview (camera policy never applies)", () => {
		assert.ok(existsSync(join(ROOT, "src/app/dashboard/interview-company-profiles/page.tsx")))
		assert.ok(!existsSync(join(ROOT, "src/app/dashboard/mock-interview/company-profiles")))
	})
	it("the nav entry is for admins and TPOs only", () => {
		const api = code(readFileSync(join(ROOT, "src/components/interview-company-profiles/companyProfilesApi.ts"), "utf8"))
		assert.match(api, /role === "admin" \|\| role === "tpo"/)
	})
	it("draft validation and request shape", () => {
		const d = { ...emptyDraft(), name: "Acme", roleTitle: "SDE" }
		assert.equal(draftError(d), null)
		assert.equal(draftError({ ...d, name: " " }), "Enter the company name.")
		assert.match(
			draftError({ ...d, sources: [{ kind: "public_url", label: "Page", url: "javascript:alert(1)", driveId: "" }] }),
			/http\(s\)/,
		)
		assert.match(draftError({ ...d, sources: [{ kind: "drive", label: "Drive", url: "", driveId: "" }] }), /drive/)
		const input = toInput({
			...d,
			focusSkills: "SQL, sql, REST APIs",
			competencies: "Ownership\nCommunication",
			sources: [
				{ kind: "public_url", label: "Page", url: " https://a.com ", driveId: "" },
				{ kind: "drive", label: "Drive", url: "", driveId: "4" },
				{ kind: "jd", label: "JD", url: "ignored", driveId: "9" },
			],
		})
		assert.deepEqual(input.focus_skills, [{ name: "SQL", weight: 2 }, { name: "REST APIs", weight: 2 }])
		assert.deepEqual(input.competencies, ["Ownership", "Communication"])
		assert.deepEqual(input.sources, [
			{ kind: "public_url", label: "Page", url: "https://a.com" },
			{ kind: "drive", label: "Drive", drive_id: 4 },
			{ kind: "jd", label: "JD" },
		])
	})
	it("profiles are archived, never deleted", () => {
		const api = code(readFileSync(join(ROOT, "src/components/interview-company-profiles/companyProfilesApi.ts"), "utf8"))
		assert.ok(!/api\.delete|"DELETE"/.test(api))
	})
})
