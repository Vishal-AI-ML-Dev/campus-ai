// Phase 6.3 unit tests: video interview flow (stage, camera lifecycle, mic/camera
// separation, duplicate-action guard) with fake media APIs. No camera hardware.
// Run: npm run test:unit
import assert from "node:assert/strict"
import { readdirSync, readFileSync } from "node:fs"
import { dirname, join } from "node:path"
import { describe, it } from "node:test"
import { fileURLToPath } from "node:url"
import { CameraController, lobbyReady } from "../../src/components/mock-interview/cameraMedia.ts"
import {
	cameraStatusText,
	createActionGuard,
	isTerminalStatus,
	micStatusText,
	releaseCameraIfEnded,
	videoStage,
} from "../../src/components/mock-interview/videoInterviewFlow.ts"
import { allTracks, makeEnv, MemStorage } from "./helpers/fakeMedia.mjs"

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..")
const MI_DIR = join(ROOT, "src", "components", "mock-interview")
const src = Object.fromEntries(
	readdirSync(MI_DIR)
		.filter((f) => /\.(ts|tsx)$/.test(f))
		.map((f) => [f, readFileSync(join(MI_DIR, f), "utf8")]),
)
const page = readFileSync(join(ROOT, "src", "app", "dashboard", "mock-interview", "page.tsx"), "utf8")

const session = (status, awaiting) => ({ status, awaiting })

// --- Stage ----------------------------------------------------------------------------
describe("videoStage", () => {
	it("maps the session to lobby, room or finished (same rules as text/voice)", () => {
		assert.equal(videoStage(session("setup", "start")), "lobby")
		assert.equal(videoStage(session("in_progress", "start")), "lobby")
		assert.equal(videoStage(session("in_progress", "answer")), "room")
		assert.equal(videoStage(session("in_progress", "next_question")), "room")
		for (const s of ["completed", "abandoned", "failed", "evaluating"]) {
			assert.equal(videoStage(session(s, "none")), "finished", s)
			assert.equal(isTerminalStatus(s), true)
		}
		assert.equal(isTerminalStatus("setup"), false)
		assert.equal(isTerminalStatus("in_progress"), false)
	})
})

// --- Lobby -> room: one stream -----------------------------------------------------------
describe("camera across the video interview", () => {
	it("lobby readiness needs the camera AND the acknowledgement", async () => {
		const env = makeEnv()
		const c = new CameraController(env)
		assert.equal(lobbyReady(c.getSnapshot(), true), false)
		await c.start()
		assert.equal(lobbyReady(c.getSnapshot(), false), false)
		assert.equal(lobbyReady(c.getSnapshot(), true), true)
	})

	it("the lobby's stream continues into the room without reopening", async () => {
		const env = makeEnv()
		const c = new CameraController(env) // one owner for the whole session
		c.attach()
		await c.start() // lobby click
		const lobbyStream = c.getSnapshot().stream
		// Server says the interview started: the stage changes, the owner stays.
		assert.equal(videoStage(session("in_progress", "answer")), "room")
		assert.equal(releaseCameraIfEnded(c, "in_progress"), false)
		assert.equal(c.getSnapshot().stream, lobbyStream)
		assert.equal(c.getSnapshot().status, "on")
		assert.equal(env.mediaDevices.calls.length, 1)
		assert.equal(lobbyStream.getVideoTracks()[0].readyState, "live")
	})

	for (const status of ["completed", "abandoned", "failed", "evaluating"]) {
		it(`a ${status} session stops every track`, async () => {
			const env = makeEnv()
			const c = new CameraController(env)
			await c.start()
			assert.equal(releaseCameraIfEnded(c, status), true)
			assert.equal(c.getSnapshot().status, "off")
			assert.ok(allTracks(env.mediaDevices).every((t) => t.readyState === "ended"))
		})
	}

	it("camera failure after the start: the interview stays in the room", async () => {
		const env = makeEnv()
		const c = new CameraController(env)
		await c.start()
		c.getSnapshot().stream.getVideoTracks()[0].fire("ended") // unplugged / taken by another app
		const s = c.getSnapshot()
		assert.equal(s.status, "error")
		assert.equal(s.error.code, "device-lost")
		// Nothing about the session changes; the room keeps rendering (D7).
		assert.equal(videoStage(session("in_progress", "answer")), "room")
		assert.match(cameraStatusText(s), /continues without it/)
		// The student can bring it back later.
		assert.equal(await c.start(), true)
	})

	it("refresh: a new owner starts with the camera OFF and never requests it", async () => {
		const storage = new MemStorage()
		const before = makeEnv({ localStorage: storage })
		const c1 = new CameraController(before)
		await c1.start()
		const chosen = c1.getSnapshot().selectedDeviceId
		// Page reload: a brand-new controller (same device storage).
		const after = makeEnv({ localStorage: storage })
		const c2 = new CameraController(after)
		c2.attach()
		await c2.refreshDevices()
		assert.equal(c2.getSnapshot().status, "off")
		assert.equal(c2.getSnapshot().stream, null)
		assert.equal(after.mediaDevices.calls.length, 0)
		assert.equal(c2.getSnapshot().selectedDeviceId, chosen) // only the remembered camera choice survives
		assert.match(cameraStatusText(c2.getSnapshot()), /Camera off/)
	})

	it("unmount of the owner stops every track", async () => {
		const env = makeEnv()
		const c = new CameraController(env)
		c.attach()
		await c.start()
		c.detach()
		assert.ok(allTracks(env.mediaDevices).every((t) => t.readyState === "ended"))
	})
})

// --- Microphone vs camera ----------------------------------------------------------------
describe("microphone and camera are separate", () => {
	it("mic status texts", () => {
		const base = { supported: true, listening: false, blocked: false, muted: false }
		assert.match(micStatusText(base), /ready/)
		assert.equal(micStatusText({ ...base, listening: true }), "Listening…")
		assert.equal(micStatusText({ ...base, muted: true, listening: true }), "Microphone muted")
		assert.match(micStatusText({ ...base, blocked: true }), /blocked/)
		assert.match(micStatusText({ ...base, supported: false }), /Type your answers/)
	})

	it("camera status texts never block the interview", () => {
		assert.match(cameraStatusText({ status: "on", error: null, notice: null }), /preview only/)
		assert.match(cameraStatusText({ status: "on", error: null, notice: "paused" }), /paused/)
		assert.match(cameraStatusText({ status: "starting", error: null, notice: null }), /Starting/)
		assert.match(cameraStatusText({ status: "error", error: null, notice: null }), /continues without it/)
		assert.match(cameraStatusText({ status: "off", error: null, notice: null }), /continues without it/)
	})

	it("muting the mic stops dictation through VoiceAnswerBox and never touches the camera", () => {
		const box = src["VoiceAnswerBox.tsx"]
		assert.match(box, /if \(listening && \(busy \|\| limitHit \|\| micMuted\)\) stop\(\)/)
		assert.match(box, /if \(micMuted\) return/)
		assert.match(box, /micMuted = false/) // default: voice interviews unchanged
		const room = src["VideoInterviewRoom.tsx"]
		assert.match(room, /micMuted=\{micMuted\}/)
		const toggle = room.match(/onClick=\{\(\) => setMicMuted\(\(m\) => !m\)\}/)
		assert.ok(toggle, "mic toggle only flips micMuted")
		// The voice room does not pass the new props.
		assert.doesNotMatch(src["InterviewRoom.tsx"], /micMuted|onMicStateChange/)
	})

	it("no second microphone stream: nothing but cameraMedia.ts calls getUserMedia, always audio: false", () => {
		for (const [f, text] of Object.entries(src)) {
			if (f !== "cameraMedia.ts") assert.doesNotMatch(text, /getUserMedia\s*\(/, f)
			assert.doesNotMatch(text, /audio:\s*true/, f)
		}
	})
})

// --- Duplicate prevention -------------------------------------------------------------------
describe("createActionGuard", () => {
	it("runs one action at a time; a double click is ignored", async () => {
		const g = createActionGuard()
		let calls = 0
		let release
		const slow = () =>
			new Promise((r) => {
				calls += 1
				release = r
			})
		const first = g.run("finish", slow)
		const second = g.run("finish", slow)
		const other = g.run("answer", slow)
		assert.equal(g.current, "finish")
		assert.equal(await second, undefined)
		assert.equal(await other, undefined)
		release("done")
		assert.equal(await first, "done")
		assert.equal(calls, 1)
		assert.equal(g.current, null)
	})

	it("is released after a failure", async () => {
		const g = createActionGuard()
		await assert.rejects(g.run("start", async () => { throw new Error("503") }))
		assert.equal(g.current, null)
		assert.equal(await g.run("start", async () => 1), 1)
	})

	it("every video action goes through the guard", () => {
		const room = src["VideoInterviewRoom.tsx"]
		for (const name of ["answer", "next", "finish"]) assert.match(room, new RegExp(`guard\\.run\\("${name}"`))
		assert.match(src["VideoInterviewWorkspace.tsx"], /guard\.run\(kind/)
	})
})

// --- Wiring --------------------------------------------------------------------------------
describe("wiring", () => {
	it("useCamera() is called once, in the workspace", () => {
		// Call sites (`= useCamera()`), not the definition or comments.
		const callers = Object.entries(src).filter(([, t]) => /=\s*useCamera\(\)/.test(t)).map(([f]) => f)
		assert.deepEqual(callers, ["VideoInterviewWorkspace.tsx"])
		assert.doesNotMatch(page, /useCamera/)
	})

	it("the camera starts only from clicks, never on mount or refresh", () => {
		for (const f of ["VideoInterviewWorkspace.tsx", "VideoInterviewRoom.tsx", "VideoLobby.tsx"]) {
			const t = src[f]
			const starts = [...t.matchAll(/camera\.start\(\)/g)].map((m) => t.slice(Math.max(0, m.index - 60), m.index))
			for (const ctx of starts) assert.match(ctx, /onClick=\{\(\) =>/, `${f}: ${ctx}`)
		}
	})

	it("terminal statuses release the camera in the workspace; finish and cancel release it at once", () => {
		const w = src["VideoInterviewWorkspace.tsx"]
		assert.match(w, /releaseCameraIfEnded\(\{ stop: stopCamera \}, session\.status\)/)
		assert.match(w, /if \(kind === "cancel"\) camera\.stop\(\)/)
		assert.match(src["VideoInterviewRoom.tsx"], /camera\.stop\(\) \/\/ released at once/)
	})

	it("page routes only video sessions to the video workspace; text/voice keep their screens", () => {
		assert.match(page, /if \(session\.mode === "video"\)\s*return <VideoInterviewWorkspace/)
		assert.match(page, /return <InterviewInstructions session=\{session\} onChange=\{setLocal\} \/>/)
		assert.match(page, /return <InterviewRoom session=\{session\} onChange=\{setLocal\} \/>/)
	})

	it("the setup form still sends voice exactly as before and video only when chosen", () => {
		const form = src["InterviewSetupForm.tsx"]
		assert.match(form, /\.\.\.\(voice \? \{ mode: "voice" as const \} : video \? \{ mode: "video" as const \} : \{\}\)/)
		assert.match(form, /visibleModes\(opts\.modes, MOCK_INTERVIEW_VIDEO_AVAILABLE\)/)
	})

	it("the finished view carries the D9 note for video only", () => {
		const fin = src["InterviewFinished.tsx"]
		assert.match(fin, /session\.mode === "video"/)
		assert.match(fin, /not recorded, uploaded or\s+analysed/)
	})

	it("the video UI sends nothing camera-related to the server", () => {
		for (const f of ["VideoInterviewWorkspace.tsx", "VideoInterviewRoom.tsx", "VideoLobby.tsx", "useCamera.ts", "cameraMedia.ts"]) {
			assert.doesNotMatch(src[f], /fetch\(|api\.post|api\.get/, f)
		}
		const calls = [...src["VideoInterviewRoom.tsx"].matchAll(/mockInterviewApi\.(\w+)\(/g)].map((m) => m[1])
		assert.deepEqual([...new Set(calls)].sort(), ["answer", "finish", "get", "next"])
	})
})

// --- Phase 6.4 ---------------------------------------------------------------------------
describe("Phase 6.4: microphone status between questions", () => {
	const base = { supported: true, listening: false, blocked: false, muted: false }

	it("is not stale while no answer box is on screen", () => {
		// e.g. awaiting next_question: the last reported state said "ready" / "Listening…".
		assert.equal(micStatusText({ ...base, answering: false }), "Microphone idle until the next question.")
		assert.equal(micStatusText({ ...base, listening: true, answering: false }), "Microphone idle until the next question.")
		assert.equal(micStatusText({ ...base, muted: true, answering: false }), "Microphone muted")
	})

	it("is unchanged while answering (default)", () => {
		assert.equal(micStatusText({ ...base, answering: true }), micStatusText(base))
		assert.equal(micStatusText({ ...base, listening: true, answering: true }), "Listening…")
	})

	it("the room passes whether an answer box is shown", () => {
		assert.match(src["VideoInterviewRoom.tsx"], /answering: session\.awaiting === "answer"/)
	})
})
