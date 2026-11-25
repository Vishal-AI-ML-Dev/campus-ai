// Phase 6.1 unit tests: video build flag, setup-form mode filtering, the
// route-scoped camera Permissions-Policy, and static privacy guards.
// Run: npm run test:unit   (Node >= 22.18 / 24: .ts modules via built-in type stripping)
import assert from "node:assert/strict"
import { readdirSync, readFileSync } from "node:fs"
import { dirname, join } from "node:path"
import { describe, it } from "node:test"
import { fileURLToPath } from "node:url"
import {
	VIDEO_UI_READY,
	videoBuildEnabled,
	visibleModes,
} from "../../src/components/mock-interview/mockInterviewModes.ts"
import nextConfig, {
	allowCameraForSelf,
	buildHeaderRules,
	buildSecurityHeaders,
	MOCK_INTERVIEW_CAMERA_ROUTES,
	mockInterviewCameraEnabled,
} from "../../next.config.ts"

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..")
const MI_DIR = join(ROOT, "src", "components", "mock-interview")

const ON = { NEXT_PUBLIC_MOCK_INTERVIEW_ENABLED: "true", NEXT_PUBLIC_MOCK_INTERVIEW_VIDEO_ENABLED: "true" }
const TEXT = { value: "text", label: "Text" }
const VOICE = { value: "voice", label: "Voice" }
const VIDEO = { value: "video", label: "Video" }

function policyOf(headers) {
	return headers.find((h) => h.key === "Permissions-Policy")?.value
}

function directives(policy) {
	return Object.fromEntries(
		policy.split(",").map((d) => d.trim().split("=", 2).map((x) => x.trim())),
	)
}

// --- Build flag -----------------------------------------------------------------
describe("video build flag", () => {
	it("defaults to off", () => {
		assert.equal(videoBuildEnabled({}), false)
		assert.equal(mockInterviewCameraEnabled({}), false)
	})

	it("needs both build flags to be exactly 'true'", () => {
		assert.equal(videoBuildEnabled(ON), true)
		for (const env of [
			{ NEXT_PUBLIC_MOCK_INTERVIEW_VIDEO_ENABLED: "true" },
			{ NEXT_PUBLIC_MOCK_INTERVIEW_ENABLED: "true" },
			{ ...ON, NEXT_PUBLIC_MOCK_INTERVIEW_ENABLED: "false" },
			{ ...ON, NEXT_PUBLIC_MOCK_INTERVIEW_VIDEO_ENABLED: "TRUE" },
			{ ...ON, NEXT_PUBLIC_MOCK_INTERVIEW_VIDEO_ENABLED: "1" },
			{ ...ON, NEXT_PUBLIC_MOCK_INTERVIEW_VIDEO_ENABLED: "" },
		]) {
			assert.equal(videoBuildEnabled(env), false, JSON.stringify(env))
			assert.equal(mockInterviewCameraEnabled(env), false, JSON.stringify(env))
		}
	})

	it("is not set in the test environment", () => {
		assert.notEqual(process.env.NEXT_PUBLIC_MOCK_INTERVIEW_VIDEO_ENABLED, "true")
	})

	it("Phase 6.3: the video UI exists, but video stays hidden unless the build flag is on", () => {
		assert.equal(VIDEO_UI_READY, true)
		// MOCK_INTERVIEW_VIDEO_AVAILABLE = videoBuildEnabled(env) && VIDEO_UI_READY: the build flag still decides.
		assert.equal(videoBuildEnabled({}) && VIDEO_UI_READY, false)
		assert.equal(videoBuildEnabled(ON) && VIDEO_UI_READY, true)
	})
})

// --- Setup-form modes -----------------------------------------------------------
describe("visibleModes", () => {
	it("drops video unless the video UI is available", () => {
		assert.deepEqual(visibleModes([TEXT, VOICE, VIDEO], false), [TEXT, VOICE])
		assert.deepEqual(visibleModes([TEXT, VIDEO], false), [TEXT])
	})

	it("keeps video when available", () => {
		assert.deepEqual(visibleModes([TEXT, VOICE, VIDEO], true), [TEXT, VOICE, VIDEO])
	})

	it("leaves text and voice exactly as the server sent them", () => {
		for (const avail of [true, false]) {
			assert.deepEqual(visibleModes([TEXT], avail), [TEXT])
			assert.deepEqual(visibleModes([TEXT, VOICE], avail), [TEXT, VOICE])
		}
	})

	it("never adds a mode the server did not offer", () => {
		assert.deepEqual(visibleModes([TEXT], true), [TEXT])
		assert.deepEqual(visibleModes(undefined, true), [])
		assert.deepEqual(visibleModes([], true), [])
	})
})

// --- Permissions-Policy ---------------------------------------------------------
describe("camera Permissions-Policy", () => {
	it("flag off: one global rule, identical to the existing headers", () => {
		for (const env of [{}, { NEXT_PUBLIC_MOCK_INTERVIEW_ENABLED: "true" }]) {
			const rules = buildHeaderRules(env, false)
			assert.equal(rules.length, 1)
			assert.equal(rules[0].source, "/:path*")
			assert.deepEqual(rules[0].headers, buildSecurityHeaders(env, false))
			assert.equal(directives(policyOf(rules[0].headers)).camera, "()")
		}
	})

	it("flag on: camera allowed only on the mock-interview route", () => {
		const rules = buildHeaderRules(ON, false)
		assert.deepEqual(
			rules.map((r) => r.source),
			["/:path*", ...MOCK_INTERVIEW_CAMERA_ROUTES],
		)
		// Global rule unchanged: camera still blocked everywhere else.
		assert.deepEqual(rules[0].headers, buildSecurityHeaders(ON, false))
		assert.equal(directives(policyOf(rules[0].headers)).camera, "()")
		for (const rule of rules.slice(1)) {
			assert.ok(rule.source.startsWith("/dashboard/mock-interview"))
			assert.deepEqual(rule.headers.map((h) => h.key), ["Permissions-Policy"])
			assert.equal(directives(policyOf(rule.headers)).camera, "(self)")
		}
	})

	it("keeps the existing microphone and geolocation policy on the route", () => {
		const global = directives(policyOf(buildSecurityHeaders(ON, false)))
		for (const rule of buildHeaderRules(ON, false).slice(1)) {
			const route = directives(policyOf(rule.headers))
			assert.equal(route.microphone, global.microphone)
			assert.equal(route.geolocation, global.geolocation)
			assert.deepEqual(Object.keys(route).sort(), Object.keys(global).sort())
		}
	})

	it("the video flag alone never changes the global microphone policy", () => {
		const MASTER = { NEXT_PUBLIC_MOCK_INTERVIEW_ENABLED: "true" }
		const VIDEO_ONLY = { NEXT_PUBLIC_MOCK_INTERVIEW_VIDEO_ENABLED: "true" }
		// Master off: adding the video flag changes nothing (the whole policy).
		assert.equal(policyOf(buildSecurityHeaders(VIDEO_ONLY, false)), policyOf(buildSecurityHeaders({}, false)))
		// Master on: adding the video flag changes nothing either.
		assert.equal(policyOf(buildSecurityHeaders(ON, false)), policyOf(buildSecurityHeaders(MASTER, false)))
		assert.equal(directives(policyOf(buildHeaderRules(ON, false)[0].headers)).microphone, "(self)")
		assert.equal(directives(policyOf(buildHeaderRules(VIDEO_ONLY, false)[0].headers)).microphone, "()")
	})

	it("master interview flag off: the microphone stays blocked", () => {
		for (const env of [
			{},
			{ NEXT_PUBLIC_MOCK_INTERVIEW_ENABLED: "false" },
			{ NEXT_PUBLIC_MOCK_INTERVIEW_ENABLED: "TRUE" },
			{ NEXT_PUBLIC_MOCK_INTERVIEW_ENABLED: "1" },
			{ NEXT_PUBLIC_MOCK_INTERVIEW_ENABLED: "" },
			{ NEXT_PUBLIC_MOCK_INTERVIEW_VIDEO_ENABLED: "true" },
		]) {
			const policy = directives(policyOf(buildSecurityHeaders(env, false)))
			assert.equal(policy.microphone, "()", JSON.stringify(env))
			assert.equal(policy.camera, "()", JSON.stringify(env))
			assert.equal(policy.geolocation, "()", JSON.stringify(env))
		}
	})

	it("master interview flag on: the microphone is allowed for the same origin only", () => {
		const MASTER = { NEXT_PUBLIC_MOCK_INTERVIEW_ENABLED: "true" }
		for (const env of [MASTER, ON]) {
			const policy = directives(policyOf(buildSecurityHeaders(env, false)))
			assert.equal(policy.microphone, "(self)", JSON.stringify(env))
			// The camera stays blocked globally; only the route rule may allow it (video flag).
			assert.equal(policy.camera, "()", JSON.stringify(env))
			assert.equal(policy.geolocation, "()", JSON.stringify(env))
		}
		assert.equal(policyOf(buildSecurityHeaders(MASTER, false)), "camera=(), microphone=(self), geolocation=()")
		// Master alone: no camera route rule.
		assert.equal(buildHeaderRules(MASTER, false).length, 1)
	})

	it("the microphone flag changes only the Permissions-Policy header", () => {
		const off = buildSecurityHeaders({}, false).filter((h) => h.key !== "Permissions-Policy")
		const on = buildSecurityHeaders({ NEXT_PUBLIC_MOCK_INTERVIEW_ENABLED: "true" }, false).filter(
			(h) => h.key !== "Permissions-Policy",
		)
		assert.deepEqual(on, off)
	})

	it("allowCameraForSelf changes only the camera directive", () => {
		assert.equal(
			allowCameraForSelf("camera=(), microphone=(self), geolocation=()"),
			"camera=(self), microphone=(self), geolocation=()",
		)
		assert.equal(
			allowCameraForSelf("camera=(), microphone=(), geolocation=()"),
			"camera=(self), microphone=(), geolocation=()",
		)
		// Not fooled by a directive whose name only starts with "camera".
		assert.equal(allowCameraForSelf("camera-x=(), camera=()"), "camera-x=(), camera=(self)")
	})

	it("route rules never touch the CSP or other security headers", () => {
		for (const rule of buildHeaderRules(ON, false).slice(1)) {
			assert.ok(!rule.headers.some((h) => h.key === "Content-Security-Policy"))
		}
	})

	it("next.config headers() uses the same rules (flag off in this environment)", async () => {
		const rules = await nextConfig.headers()
		assert.deepEqual(rules, buildHeaderRules(process.env))
		assert.equal(rules.length, 1)
	})
})

// --- Static privacy guards ------------------------------------------------------
describe("mock-interview privacy guards", () => {
	const sources = readdirSync(MI_DIR)
		.filter((f) => /\.(ts|tsx)$/.test(f))
		.map((f) => [f, readFileSync(join(MI_DIR, f), "utf8")])

	it("found the mock-interview sources", () => {
		assert.ok(sources.length >= 10)
	})

	it("never records, captures or encodes media", () => {
		const banned = /\b(MediaRecorder|captureStream|toDataURL|toBlob|drawImage|getImageData|ImageCapture|getDisplayMedia)\b/
		for (const [file, text] of sources) assert.doesNotMatch(text, banned, file)
	})

	it("never imports the face-recognition features", () => {
		for (const [file, text] of sources) {
			assert.doesNotMatch(text, /components\/face|face-enroll|face-attendance|faceApi/, file)
		}
	})
})
