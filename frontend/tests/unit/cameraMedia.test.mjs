// Phase 6.2 unit tests: camera logic (cameraMedia.ts) with fake browser media APIs.
// No camera hardware is used. Run: npm run test:unit
import assert from "node:assert/strict"
import { readdirSync, readFileSync } from "node:fs"
import { dirname, join } from "node:path"
import { describe, it } from "node:test"
import { fileURLToPath } from "node:url"
import {
	CAMERA_DEVICE_STORAGE_KEY,
	CAMERA_IDEAL_HEIGHT,
	CAMERA_IDEAL_WIDTH,
	CAMERA_RELOAD_MARKER_KEY,
	CameraController,
	cameraConstraints,
	cameraPolicyAllowed,
	cameraSupport,
	classifyCameraError,
	describeCameraError,
	INITIAL_CAMERA_STATE,
	listCameras,
	lobbyReady,
	markCameraReloadAttempt,
	stopStream,
} from "../../src/components/mock-interview/cameraMedia.ts"
import { VIDEO_UI_READY, visibleModes } from "../../src/components/mock-interview/mockInterviewModes.ts"
import { buildHeaderRules, buildSecurityHeaders } from "../../next.config.ts"

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..")
const MI_DIR = join(ROOT, "src", "components", "mock-interview")
const read = (...p) => readFileSync(join(...p), "utf8")

// --- Fakes ---------------------------------------------------------------------------
class FakeTrack extends EventTarget {
	constructor(kind = "video", deviceId = "cam-1") {
		super()
		this.kind = kind
		this.deviceId = deviceId
		this.readyState = "live"
		this.stopCount = 0
	}
	stop() {
		this.stopCount += 1
		this.readyState = "ended" // a page-initiated stop() fires no "ended" event (as in browsers)
	}
	getSettings() {
		return { deviceId: this.deviceId }
	}
	fire(type) {
		if (type === "ended") this.readyState = "ended"
		this.dispatchEvent(new Event(type))
	}
}

class FakeStream {
	constructor(tracks) {
		this.tracks = tracks
	}
	getTracks() {
		return [...this.tracks]
	}
	getVideoTracks() {
		return this.tracks.filter((t) => t.kind === "video")
	}
	getAudioTracks() {
		return this.tracks.filter((t) => t.kind === "audio")
	}
}

function domError(name) {
	return new DOMException(`fake ${name}`, name)
}

class FakeMediaDevices extends EventTarget {
	constructor(cameras = [{ deviceId: "cam-1", label: "Front Camera" }, { deviceId: "cam-2", label: "USB Camera" }]) {
		super()
		this.cameras = cameras
		this.calls = [] // every getUserMedia constraints object
		this.streams = []
		this.failures = [] // queue of error names for the next calls
		this.hold = false // when true, requests wait for release()
		this.waiting = []
		this.labelsVisible = false
	}
	async getUserMedia(constraints) {
		this.calls.push(constraints)
		if (this.hold) await new Promise((r) => this.waiting.push(r))
		const failure = this.failures.shift()
		if (failure) throw domError(failure)
		const exact = constraints.video?.deviceId?.exact
		if (exact && !this.cameras.some((c) => c.deviceId === exact)) throw domError("OverconstrainedError")
		if (this.cameras.length === 0) throw domError("NotFoundError")
		const id = exact ?? this.cameras[0].deviceId
		const stream = new FakeStream([new FakeTrack("video", id)])
		this.streams.push(stream)
		this.labelsVisible = true
		return stream
	}
	release() {
		for (const r of this.waiting.splice(0)) r()
	}
	async enumerateDevices() {
		return [
			{ kind: "audioinput", deviceId: "mic-1", label: this.labelsVisible ? "Mic" : "" },
			...this.cameras.map((c) => ({
				kind: "videoinput",
				deviceId: this.labelsVisible ? c.deviceId : "",
				label: this.labelsVisible ? c.label : "",
			})),
		]
	}
}

class MemStorage {
	constructor() {
		this.map = new Map()
	}
	getItem(k) {
		return this.map.has(k) ? this.map.get(k) : null
	}
	setItem(k, v) {
		this.map.set(k, String(v))
	}
	removeItem(k) {
		this.map.delete(k)
	}
}

function makeEnv({ policy = true, secure = true, mediaDevices = new FakeMediaDevices(), policyApi = true } = {}) {
	return {
		mediaDevices,
		isSecureContext: secure,
		document: policyApi ? { permissionsPolicy: { allowsFeature: (f) => (f === "camera" ? policy : true) } } : {},
		window: new EventTarget(),
		localStorage: new MemStorage(),
		sessionStorage: new MemStorage(),
	}
}

const tick = () => new Promise((r) => setImmediate(r))
const allTracks = (md) => md.streams.flatMap((s) => s.getTracks())

// --- Pure helpers ------------------------------------------------------------------------
describe("constraints and support", () => {
	it("requests video only, 640x360 ideal (not mandatory)", () => {
		const c = cameraConstraints()
		assert.equal(c.audio, false)
		assert.deepEqual(c.video, { width: { ideal: CAMERA_IDEAL_WIDTH }, height: { ideal: CAMERA_IDEAL_HEIGHT } })
		assert.equal(CAMERA_IDEAL_WIDTH, 640)
		assert.equal(CAMERA_IDEAL_HEIGHT, 360)
		assert.deepEqual(cameraConstraints("cam-2").video.deviceId, { exact: "cam-2" })
		assert.equal(cameraConstraints("cam-2").audio, false)
	})

	it("detects insecure context before missing APIs", () => {
		assert.equal(cameraSupport({ isSecureContext: false, mediaDevices: null }), "insecure-context")
		assert.equal(cameraSupport({ isSecureContext: true, mediaDevices: null }), "unsupported")
		assert.equal(cameraSupport({ isSecureContext: true, mediaDevices: {} }), "unsupported")
		assert.equal(cameraSupport(makeEnv()), "ok")
		assert.equal(cameraSupport({}), "unsupported") // server / no browser
	})

	it("reads the Permissions-Policy where the browser exposes it", () => {
		assert.equal(cameraPolicyAllowed(makeEnv({ policy: true })), true)
		assert.equal(cameraPolicyAllowed(makeEnv({ policy: false })), false)
		assert.equal(cameraPolicyAllowed(makeEnv({ policyApi: false })), null)
		assert.equal(cameraPolicyAllowed({ document: { featurePolicy: { allowsFeature: () => false } } }), false)
		assert.equal(cameraPolicyAllowed({ document: { permissionsPolicy: { allowsFeature: () => { throw new Error() } } } }), null)
		assert.equal(cameraPolicyAllowed({}), null)
	})

	it("maps every browser error to a message", () => {
		const cases = [
			["NotAllowedError", null, "not-allowed"],
			["PermissionDeniedError", true, "not-allowed"],
			["NotAllowedError", false, "policy-blocked"],
			["SecurityError", null, "security"],
			["SecurityError", false, "policy-blocked"],
			["NotFoundError", null, "not-found"],
			["NotReadableError", null, "in-use"],
			["TrackStartError", null, "in-use"],
			["OverconstrainedError", null, "overconstrained"],
			["TypeError", null, "unsupported"],
			["WeirdError", null, "unknown"],
		]
		for (const [name, policy, code] of cases) {
			assert.equal(classifyCameraError(domError(name), policy), code, name)
			assert.ok(describeCameraError(code).length > 20)
		}
		assert.equal(classifyCameraError("boom", null), "unknown")
		assert.equal(classifyCameraError(undefined, null), "unknown")
	})

	it("stopStream stops every track, even if one throws", () => {
		const good = new FakeTrack()
		const bad = new FakeTrack()
		bad.stop = () => {
			throw new Error("broken")
		}
		const last = new FakeTrack("audio")
		assert.equal(stopStream(new FakeStream([good, bad, last])), 2)
		assert.equal(good.readyState, "ended")
		assert.equal(last.readyState, "ended")
		assert.equal(stopStream(null), 0)
	})

	it("lists only cameras, with numbered names until labels are visible", async () => {
		const env = makeEnv()
		assert.deepEqual(await listCameras(env), [
			{ deviceId: "", label: "Camera 1", hasLabel: false },
			{ deviceId: "", label: "Camera 2", hasLabel: false },
		])
		env.mediaDevices.labelsVisible = true
		assert.deepEqual(
			(await listCameras(env)).map((d) => [d.deviceId, d.label, d.hasLabel]),
			[["cam-1", "Front Camera", true], ["cam-2", "USB Camera", true]],
		)
		assert.deepEqual(await listCameras({}), [])
	})
})

// --- Controller lifecycle -----------------------------------------------------------------
describe("CameraController", () => {
	it("(2) creating, attaching and listing devices never requests the camera", async () => {
		const env = makeEnv()
		const c = new CameraController(env)
		c.attach()
		await c.refreshDevices()
		assert.equal(env.mediaDevices.calls.length, 0)
		assert.deepEqual(c.getSnapshot(), { ...INITIAL_CAMERA_STATE, devices: c.getSnapshot().devices })
		assert.equal(c.getSnapshot().status, "off")
	})

	it("(1, 3, 19) start() requests video only and goes live", async () => {
		const env = makeEnv()
		const c = new CameraController(env)
		const seen = []
		c.subscribe(() => seen.push(c.getSnapshot().status))
		assert.equal(await c.start(), true)
		assert.equal(env.mediaDevices.calls.length, 1)
		assert.equal(env.mediaDevices.calls[0].audio, false)
		const s = c.getSnapshot()
		assert.equal(s.status, "on")
		assert.equal(s.permission, "granted")
		assert.equal(s.stream.getAudioTracks().length, 0)
		assert.deepEqual(seen.slice(0, 2), ["starting", "on"])
		await tick()
		assert.ok(c.getSnapshot().devices.every((d) => d.hasLabel)) // labels after permission
	})

	it("(4) permission denied", async () => {
		const env = makeEnv()
		env.mediaDevices.failures.push("NotAllowedError")
		const c = new CameraController(env)
		assert.equal(await c.start(), false)
		const s = c.getSnapshot()
		assert.equal(s.status, "error")
		assert.equal(s.error.code, "not-allowed")
		assert.equal(s.error.reloadSuggested, false)
		assert.equal(s.permission, "denied")
		assert.equal(s.stream, null)
	})

	it("(5) no camera found", async () => {
		const env = makeEnv({ mediaDevices: new FakeMediaDevices([]) })
		const c = new CameraController(env)
		assert.equal(await c.start(), false)
		assert.equal(c.getSnapshot().error.code, "not-found")
	})

	it("(6) camera in use by another app", async () => {
		const env = makeEnv()
		env.mediaDevices.failures.push("NotReadableError")
		const c = new CameraController(env)
		assert.equal(await c.start(), false)
		assert.equal(c.getSnapshot().error.code, "in-use")
	})

	it("(7) unsupported browser API: no request is attempted", async () => {
		const env = makeEnv()
		env.mediaDevices = null
		const c = new CameraController(env)
		assert.equal(await c.start(), false)
		assert.equal(c.getSnapshot().error.code, "unsupported")
	})

	it("(8) insecure context", async () => {
		const env = makeEnv({ secure: false })
		const c = new CameraController(env)
		assert.equal(await c.start(), false)
		assert.equal(c.getSnapshot().error.code, "insecure-context")
		assert.equal(env.mediaDevices.calls.length, 0)
	})

	it("(9) Permissions-Policy blocked: no request, reload offered once per tab", async () => {
		const env = makeEnv({ policy: false })
		const c = new CameraController(env)
		assert.equal(await c.start(), false)
		assert.equal(env.mediaDevices.calls.length, 0) // known blocked: don't even ask
		let err = c.getSnapshot().error
		assert.equal(err.code, "policy-blocked")
		assert.equal(err.reloadSuggested, true)
		// The student reloads; still blocked: no second reload offer (no loop).
		markCameraReloadAttempt(env)
		assert.equal(env.sessionStorage.getItem(CAMERA_RELOAD_MARKER_KEY), "1")
		const after = new CameraController(env)
		assert.equal(await after.start(), false)
		err = after.getSnapshot().error
		assert.equal(err.code, "policy-still-blocked")
		assert.equal(err.reloadSuggested, false)
	})

	it("(9) policy block detected from the error where the policy API is missing (SecurityError)", async () => {
		const env = makeEnv({ policyApi: false })
		env.mediaDevices.failures.push("SecurityError")
		const c = new CameraController(env)
		assert.equal(await c.start(), false)
		assert.equal(c.getSnapshot().error.code, "security")
		assert.equal(c.getSnapshot().error.reloadSuggested, true)
	})

	it("a working camera clears the reload marker", async () => {
		const env = makeEnv()
		markCameraReloadAttempt(env)
		assert.equal(await new CameraController(env).start(), true)
		assert.equal(env.sessionStorage.getItem(CAMERA_RELOAD_MARKER_KEY), null)
	})

	it("(10) enumerates cameras", async () => {
		const env = makeEnv()
		const c = new CameraController(env)
		await c.start()
		const devices = await c.refreshDevices()
		assert.deepEqual(devices.map((d) => d.deviceId), ["cam-1", "cam-2"])
		assert.ok(!devices.some((d) => d.deviceId === "mic-1"))
	})

	it("(11, 12, 18) switching stops the old tracks first, then requests the new camera", async () => {
		const env = makeEnv()
		const c = new CameraController(env)
		await c.start()
		const [first] = env.mediaDevices.streams
		let oldStoppedBeforeRequest = null
		const gum = env.mediaDevices.getUserMedia.bind(env.mediaDevices)
		env.mediaDevices.getUserMedia = (cons) => {
			oldStoppedBeforeRequest = first.getTracks().every((t) => t.readyState === "ended")
			return gum(cons)
		}
		assert.equal(await c.switchDevice("cam-2"), true)
		assert.equal(oldStoppedBeforeRequest, true)
		assert.equal(first.getTracks()[0].stopCount, 1)
		const s = c.getSnapshot()
		assert.equal(s.selectedDeviceId, "cam-2")
		assert.equal(s.stream.getVideoTracks()[0].deviceId, "cam-2")
		assert.deepEqual(env.mediaDevices.calls.at(-1).video.deviceId, { exact: "cam-2" })
		assert.equal(env.mediaDevices.calls.at(-1).audio, false)
		assert.equal(env.localStorage.getItem(CAMERA_DEVICE_STORAGE_KEY), "cam-2")
	})

	it("(18) the saved camera is used next time; a broken storage is tolerated", async () => {
		const env = makeEnv()
		env.localStorage.setItem(CAMERA_DEVICE_STORAGE_KEY, "cam-2")
		const c = new CameraController(env)
		assert.equal(c.getSnapshot().selectedDeviceId, "cam-2")
		await c.start()
		assert.deepEqual(env.mediaDevices.calls[0].video.deviceId, { exact: "cam-2" })

		const broken = makeEnv()
		broken.localStorage = {
			getItem() {
				throw new Error("blocked")
			},
			setItem() {
				throw new Error("blocked")
			},
			removeItem() {
				throw new Error("blocked")
			},
		}
		const c2 = new CameraController(broken)
		assert.equal(c2.getSnapshot().selectedDeviceId, null)
		assert.equal(await c2.switchDevice("cam-1"), false) // camera off: just selects
		assert.equal(await c2.start(), true)
	})

	it("switching while the camera is off only selects", async () => {
		const env = makeEnv()
		const c = new CameraController(env)
		assert.equal(await c.switchDevice("cam-2"), false)
		assert.equal(env.mediaDevices.calls.length, 0)
		assert.equal(c.getSnapshot().selectedDeviceId, "cam-2")
	})

	it("a saved camera that no longer exists falls back to the default camera", async () => {
		const env = makeEnv()
		env.localStorage.setItem(CAMERA_DEVICE_STORAGE_KEY, "gone")
		const c = new CameraController(env)
		assert.equal(await c.start(), true)
		assert.equal(env.mediaDevices.calls.length, 2)
		assert.equal(env.mediaDevices.calls[1].video.deviceId, undefined)
		const s = c.getSnapshot()
		assert.equal(s.selectedDeviceId, "cam-1")
		assert.match(s.notice, /default camera/)
		assert.equal(env.localStorage.getItem(CAMERA_DEVICE_STORAGE_KEY), "cam-1")
	})

	it("(13) turning the camera off stops every track", async () => {
		const env = makeEnv()
		const c = new CameraController(env)
		await c.start()
		c.stop()
		assert.equal(c.getSnapshot().status, "off")
		assert.equal(c.getSnapshot().stream, null)
		assert.ok(allTracks(env.mediaDevices).every((t) => t.readyState === "ended"))
	})

	it("(14) unmount (detach) stops every track and removes listeners", async () => {
		const env = makeEnv()
		const c = new CameraController(env)
		c.attach()
		await c.start()
		c.detach()
		assert.ok(allTracks(env.mediaDevices).every((t) => t.stopCount === 1))
		// Listeners are gone: a later pagehide/devicechange does nothing.
		env.window.dispatchEvent(new Event("pagehide"))
		env.mediaDevices.dispatchEvent(new Event("devicechange"))
		assert.equal(c.getSnapshot().status, "off")
	})

	it("re-attach after detach works (React StrictMode mount/unmount/mount)", async () => {
		const env = makeEnv()
		const c = new CameraController(env)
		c.attach()
		c.detach()
		c.attach()
		assert.equal(env.mediaDevices.calls.length, 0)
		await c.start()
		env.window.dispatchEvent(new Event("pagehide"))
		assert.equal(c.getSnapshot().status, "off")
	})

	it("(15) pagehide stops every track", async () => {
		const env = makeEnv()
		const c = new CameraController(env)
		c.attach()
		await c.start()
		env.window.dispatchEvent(new Event("pagehide"))
		assert.equal(c.getSnapshot().status, "off")
		assert.ok(allTracks(env.mediaDevices).every((t) => t.readyState === "ended"))
	})

	it("(16) track ended -> device-lost error, tracks stopped", async () => {
		const env = makeEnv()
		const c = new CameraController(env)
		await c.start()
		const track = c.getSnapshot().stream.getVideoTracks()[0]
		track.fire("ended")
		const s = c.getSnapshot()
		assert.equal(s.status, "error")
		assert.equal(s.error.code, "device-lost")
		assert.equal(s.stream, null)
		assert.equal(track.stopCount, 1)
	})

	it("track mute/unmute shows and clears a notice", async () => {
		const env = makeEnv()
		const c = new CameraController(env)
		await c.start()
		const track = c.getSnapshot().stream.getVideoTracks()[0]
		track.fire("mute")
		assert.match(c.getSnapshot().notice, /paused/)
		assert.equal(c.getSnapshot().status, "on")
		track.fire("unmute")
		assert.equal(c.getSnapshot().notice, null)
	})

	it("events from an old track are ignored after a switch", async () => {
		const env = makeEnv()
		const c = new CameraController(env)
		await c.start()
		const oldTrack = c.getSnapshot().stream.getVideoTracks()[0]
		await c.switchDevice("cam-2")
		oldTrack.fire("ended")
		assert.equal(c.getSnapshot().status, "on")
	})

	it("(17) device unplugged while live: devicechange -> device-lost", async () => {
		const env = makeEnv()
		const c = new CameraController(env)
		c.attach()
		await c.start()
		const track = c.getSnapshot().stream.getVideoTracks()[0]
		track.readyState = "ended" // the OS ended it; some browsers fire devicechange first
		env.mediaDevices.cameras = env.mediaDevices.cameras.slice(1)
		env.mediaDevices.dispatchEvent(new Event("devicechange"))
		assert.equal(c.getSnapshot().error.code, "device-lost")
		await tick()
		assert.deepEqual(c.getSnapshot().devices.map((d) => d.deviceId), ["cam-2"])
	})

	it("(17) selected camera unplugged while off: selection and saved id are cleared", async () => {
		const env = makeEnv()
		const c = new CameraController(env)
		c.attach()
		await c.switchDevice("cam-2")
		env.mediaDevices.labelsVisible = true
		env.mediaDevices.cameras = env.mediaDevices.cameras.slice(0, 1)
		env.mediaDevices.dispatchEvent(new Event("devicechange"))
		await tick()
		const s = c.getSnapshot()
		assert.equal(s.selectedDeviceId, null)
		assert.match(s.notice, /disconnected/)
		assert.equal(env.localStorage.getItem(CAMERA_DEVICE_STORAGE_KEY), null)
	})

	it("concurrent start() calls share one request", async () => {
		const env = makeEnv()
		env.mediaDevices.hold = true
		const c = new CameraController(env)
		const a = c.start()
		const b = c.start()
		assert.equal(a, b)
		await tick()
		assert.equal(env.mediaDevices.calls.length, 1)
		env.mediaDevices.release()
		assert.equal(await a, true)
		assert.equal(env.mediaDevices.streams.length, 1)
	})

	it("a request that finishes after stop() is stopped at once (stale guard)", async () => {
		const env = makeEnv()
		env.mediaDevices.hold = true
		const c = new CameraController(env)
		const p = c.start()
		await tick()
		c.stop()
		env.mediaDevices.release()
		assert.equal(await p, false)
		assert.equal(c.getSnapshot().status, "off")
		assert.equal(c.getSnapshot().stream, null)
		assert.ok(allTracks(env.mediaDevices).every((t) => t.readyState === "ended"))
	})

	it("a request that finishes after unmount is stopped at once", async () => {
		const env = makeEnv()
		env.mediaDevices.hold = true
		const c = new CameraController(env)
		c.attach()
		const p = c.start()
		await tick()
		c.detach()
		env.mediaDevices.release()
		assert.equal(await p, false)
		assert.ok(allTracks(env.mediaDevices).every((t) => t.readyState === "ended"))
	})

	it("choosing another camera while one is opening switches once it is open", async () => {
		const env = makeEnv()
		env.mediaDevices.hold = true
		const c = new CameraController(env)
		const p = c.start()
		await tick()
		const q = c.switchDevice("cam-2")
		assert.equal(c.getSnapshot().selectedDeviceId, "cam-2")
		env.mediaDevices.hold = false
		env.mediaDevices.release()
		assert.equal(await p, true)
		assert.equal(await q, true)
		assert.equal(env.mediaDevices.calls.length, 2)
		assert.equal(c.getSnapshot().stream.getVideoTracks()[0].deviceId, "cam-2")
		assert.equal(env.mediaDevices.streams[0].getTracks()[0].readyState, "ended")
	})

	it("(19) audio is never requested in any path", async () => {
		const env = makeEnv()
		env.localStorage.setItem(CAMERA_DEVICE_STORAGE_KEY, "gone")
		const c = new CameraController(env)
		c.attach()
		await c.start()
		await c.switchDevice("cam-2")
		c.stop()
		await c.start()
		assert.ok(env.mediaDevices.calls.length >= 4)
		for (const call of env.mediaDevices.calls) assert.equal(call.audio, false)
	})
})

// --- Lobby readiness ------------------------------------------------------------------------
describe("(20) lobby readiness", () => {
	it("needs a live camera AND the acknowledgement", async () => {
		const env = makeEnv()
		const c = new CameraController(env)
		assert.equal(lobbyReady(c.getSnapshot(), true), false) // camera off
		await c.start()
		assert.equal(lobbyReady(c.getSnapshot(), false), false) // not acknowledged
		assert.equal(lobbyReady(c.getSnapshot(), true), true)
		c.getSnapshot().stream.getVideoTracks()[0].readyState = "ended"
		assert.equal(lobbyReady(c.getSnapshot(), true), false) // dead track
		c.stop()
		assert.equal(lobbyReady(c.getSnapshot(), true), false)
	})

	it("errors never count as ready", () => {
		assert.equal(lobbyReady({ status: "error", stream: null }, true), false)
		assert.equal(lobbyReady({ status: "starting", stream: null }, true), false)
	})
})

// --- Static guards and regressions ----------------------------------------------------------
describe("Phase 6.2 guards and regressions", () => {
	const files = readdirSync(MI_DIR).filter((f) => /\.(ts|tsx)$/.test(f))
	const src = Object.fromEntries(files.map((f) => [f, read(MI_DIR, f)]))

	it("only cameraMedia.ts calls getUserMedia, always with audio: false", () => {
		for (const [f, text] of Object.entries(src)) {
			if (f !== "cameraMedia.ts") assert.doesNotMatch(text, /getUserMedia\s*\(|\bgum\s*\(/, f)
		}
		assert.match(src["cameraMedia.ts"], /return \{ video, audio: false \}/)
		for (const text of Object.values(src)) assert.doesNotMatch(text, /audio:\s*true/)
	})

	it("useCamera only attaches/detaches on mount (no camera request)", () => {
		const effect = src["useCamera.ts"].match(/useEffect\(\(\) => \{([\s\S]*?)\}, \[controller\]\)/)
		assert.ok(effect, "mount effect found")
		assert.doesNotMatch(effect[1], /start|getUserMedia/)
		assert.match(effect[1], /controller\.attach\(\)/)
		assert.match(effect[1], /controller\.detach\(\)/)
	})

	it("the lobby starts the camera only from a click", () => {
		const lobby = src["VideoLobby.tsx"]
		const starts = lobby.match(/camera\.start\(/g) ?? []
		assert.equal(starts.length, 1)
		assert.match(lobby, /onClick=\{\(\) => void camera\.start\(\)\}/)
	})

	it("the preview uses srcObject with autoPlay, muted and playsInline", () => {
		const p = src["CameraPreview.tsx"]
		assert.match(p, /srcObject = live \? stream : null/)
		assert.match(p, /srcObject = null/)
		for (const attr of ["autoPlay", "muted", "playsInline"]) assert.match(p, new RegExp(`\\b${attr}\\b`))
	})

	it("the reload button is the only reload, behind a click", () => {
		for (const [f, text] of Object.entries(src)) {
			const n = (text.match(/location\.reload\(/g) ?? []).length
			assert.equal(n, f === "useCamera.ts" ? 1 : 0, f)
		}
		assert.match(src["VideoLobby.tsx"], /onClick=\{camera\.reloadToEnableCamera\}/)
	})

	it("camera UI is used only by the video workspace and room (Phase 6.3)", () => {
		assert.equal(VIDEO_UI_READY, true)
		const page = read(ROOT, "src", "app", "dashboard", "mock-interview", "page.tsx")
		// The page only knows the workspace; the camera pieces stay inside the video files.
		assert.match(page, /VideoInterviewWorkspace/)
		assert.doesNotMatch(page, /VideoLobby|CameraPreview|CameraDeviceSelect|VideoPrivacyNotice|useCamera|cameraMedia/)
		const videoFiles = [
			"VideoLobby.tsx", "CameraPreview.tsx", "CameraDeviceSelect.tsx", "VideoPrivacyNotice.tsx", "useCamera.ts",
			"cameraMedia.ts", "VideoInterviewWorkspace.tsx", "VideoInterviewRoom.tsx", "videoInterviewFlow.ts",
		]
		const others = Object.entries(src).filter(([f]) => !videoFiles.includes(f))
		assert.ok(others.length >= 10)
		for (const [f, text] of others) {
			assert.doesNotMatch(text, /VideoLobby|CameraPreview|CameraDeviceSelect|VideoPrivacyNotice|useCamera|cameraMedia/, f)
		}
	})

	it("the privacy notice says what the approved design promises", () => {
		const n = src["VideoPrivacyNotice.tsx"]
		for (const phrase of [/not recorded/, /not uploaded/, /not analysed/, /answer text you review and submit/, /speech service/, /type="checkbox"/]) {
			assert.match(n, phrase)
		}
		assert.doesNotMatch(n, /fetch\(|api\.|mockInterviewApi/)
	})

	it("text and voice modes are unchanged; video hidden whenever the build flag is off", () => {
		const text = { value: "text", label: "Text" }
		const voice = { value: "voice", label: "Voice" }
		const buildFlagOff = false // MOCK_INTERVIEW_VIDEO_AVAILABLE with the default build
		assert.deepEqual(visibleModes([text, voice, { value: "video", label: "Video" }], buildFlagOff), [text, voice])
		assert.deepEqual(visibleModes([text], buildFlagOff), [text])
		assert.deepEqual(visibleModes([text, voice], VIDEO_UI_READY), [text, voice])
	})

	it("camera policy stays route-scoped and the microphone policy is untouched", () => {
		const on = { NEXT_PUBLIC_MOCK_INTERVIEW_ENABLED: "true", NEXT_PUBLIC_MOCK_INTERVIEW_VIDEO_ENABLED: "true" }
		const pol = (h) => h.find((x) => x.key === "Permissions-Policy").value
		assert.equal(buildHeaderRules({}, false).length, 1)
		const globalOff = pol(buildSecurityHeaders({}, false))
		assert.match(globalOff, /camera=\(\)/)
		assert.equal(pol(buildSecurityHeaders(on, false)), globalOff)
		const mic = (p) => p.split(",").map((d) => d.trim()).find((d) => d.startsWith("microphone="))
		for (const rule of buildHeaderRules(on, false).slice(1)) {
			assert.ok(rule.source.startsWith("/dashboard/mock-interview"))
			assert.equal(mic(pol(rule.headers)), mic(globalOff))
		}
	})
})
