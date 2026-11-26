/**
 * Phase 6.2: camera logic for the video interview (no React; unit-tested in Node).
 *
 * Privacy (approved design D2/D10, specs/phase-6-video-interview.md):
 *   - The camera feeds ONLY a local <video> element. Nothing here records,
 *     captures frames, encodes, uploads or analyses video.
 *   - Every request is `getUserMedia({ video, audio: false })`: browser speech
 *     recognition (Phase 5) owns the microphone; no second microphone stream.
 *   - The camera is only requested by `start()`, which the UI calls from a
 *     click. Creating or attaching a controller never touches the camera.
 *   - Only the chosen camera's deviceId is remembered, in localStorage, on this
 *     device. It is never sent to the server.
 *
 * Browser globals are injected through `CameraEnv` so tests can use fakes.
 */

export const CAMERA_IDEAL_WIDTH = 640
export const CAMERA_IDEAL_HEIGHT = 360
export const CAMERA_DEVICE_STORAGE_KEY = "mock-interview:camera-device"
/** sessionStorage marker: the student already used "Reload to enable camera" in this tab. */
export const CAMERA_RELOAD_MARKER_KEY = "mock-interview:camera-reload-attempted"

// --- Environment -----------------------------------------------------------------

type Listenable = {
	addEventListener: (type: string, listener: (event: Event) => void) => void
	removeEventListener: (type: string, listener: (event: Event) => void) => void
}

type StorageLike = {
	getItem: (key: string) => string | null
	setItem: (key: string, value: string) => void
	removeItem: (key: string) => void
}

type PolicyLike = { allowsFeature?: (feature: string) => boolean }

export type MediaDevicesLike = Listenable & {
	getUserMedia?: (constraints: MediaStreamConstraints) => Promise<MediaStream>
	enumerateDevices?: () => Promise<MediaDeviceInfo[]>
}

export type CameraEnv = {
	mediaDevices?: MediaDevicesLike | null
	/** `window.isSecureContext`; undefined = unknown (treated as secure). */
	isSecureContext?: boolean
	document?: { permissionsPolicy?: PolicyLike; featurePolicy?: PolicyLike } | null
	/** Receives `pagehide`. */
	window?: Listenable | null
	localStorage?: StorageLike | null
	sessionStorage?: StorageLike | null
}

function safeStorage(get: () => Storage | undefined): StorageLike | null {
	try {
		return get() ?? null // access itself can throw (blocked site data)
	} catch {
		return null
	}
}

/** The real browser environment (empty on the server). Reads globals at call time. */
export function browserCameraEnv(): CameraEnv {
	if (typeof window === "undefined" || typeof navigator === "undefined") return {}
	const doc = typeof document === "undefined" ? null : (document as unknown as CameraEnv["document"])
	return {
		mediaDevices: (navigator.mediaDevices as unknown as MediaDevicesLike | undefined) ?? null,
		isSecureContext: window.isSecureContext,
		document: doc,
		window,
		localStorage: safeStorage(() => window.localStorage),
		sessionStorage: safeStorage(() => window.sessionStorage),
	}
}

// --- Support and policy ----------------------------------------------------------

export type CameraSupport = "ok" | "insecure-context" | "unsupported"

/** Can this page ask for a camera at all? Insecure context is checked first (it also hides mediaDevices). */
export function cameraSupport(env: CameraEnv): CameraSupport {
	if (env.isSecureContext === false) return "insecure-context"
	if (!env.mediaDevices || typeof env.mediaDevices.getUserMedia !== "function") return "unsupported"
	return "ok"
}

/**
 * Does this document's Permissions-Policy allow the camera?
 * true/false where the browser exposes the policy (Chromium), null when unknown.
 */
export function cameraPolicyAllowed(env: CameraEnv): boolean | null {
	const doc = env.document
	if (!doc) return null
	for (const policy of [doc.permissionsPolicy, doc.featurePolicy]) {
		if (policy && typeof policy.allowsFeature === "function") {
			try {
				return Boolean(policy.allowsFeature("camera"))
			} catch {
				return null
			}
		}
	}
	return null
}

// --- Constraints -----------------------------------------------------------------

/** Video only, 640x360 ideal (not mandatory); an exact deviceId when one is chosen. */
export function cameraConstraints(deviceId?: string | null): MediaStreamConstraints {
	const video: MediaTrackConstraints = {
		width: { ideal: CAMERA_IDEAL_WIDTH },
		height: { ideal: CAMERA_IDEAL_HEIGHT },
	}
	if (deviceId) video.deviceId = { exact: deviceId }
	return { video, audio: false }
}

// --- Errors ----------------------------------------------------------------------

export type CameraErrorCode =
	| "not-allowed"
	| "not-found"
	| "in-use"
	| "overconstrained"
	| "security"
	| "insecure-context"
	| "unsupported"
	| "policy-blocked"
	| "policy-still-blocked"
	| "device-lost"
	| "unknown"

export type CameraErrorInfo = {
	code: CameraErrorCode
	message: string
	/** Show "Reload to enable camera" (never automatic; one attempt per tab). */
	reloadSuggested: boolean
}

const MESSAGES: Record<CameraErrorCode, string> = {
	"not-allowed":
		"Camera access was blocked. Allow the camera for this site (the camera icon in the address bar or your browser's site settings), then try again.",
	"not-found": "No camera was found. Connect a camera, then try again.",
	"in-use":
		"Your camera could not start. It may be in use by another app or browser tab. Close it there, then try again.",
	overconstrained: "The selected camera is not available. Choose another camera and try again.",
	security:
		"Your browser blocked the camera on this page. Reloading the page usually fixes this.",
	"insecure-context":
		"The camera only works on a secure (https) connection or on localhost. Open Campus AI over https to use a video interview.",
	unsupported:
		"This browser cannot use a camera here. Use a recent Chrome, Edge, Firefox or Safari, or choose a text or voice interview.",
	"policy-blocked":
		"The camera is not enabled yet because this page was opened from another page. Reload this page to enable the camera.",
	"policy-still-blocked":
		"The camera is still blocked on this page after reloading. Video interviews may not be available on this site; choose a text or voice interview.",
	"device-lost":
		"Your camera stopped. It may have been unplugged or taken over by another app. Turn it on again or choose another camera.",
	unknown: "The camera could not be started. Try again, or choose another camera.",
}

export function describeCameraError(code: CameraErrorCode): string {
	return MESSAGES[code]
}

function errorName(err: unknown): string {
	if (err && typeof err === "object" && "name" in err) return String((err as { name: unknown }).name)
	return ""
}

/**
 * Map a getUserMedia failure to a code. `policyAllowed` is cameraPolicyAllowed():
 * a denial on a document whose policy blocks the camera is a policy problem,
 * not the student's choice.
 */
export function classifyCameraError(err: unknown, policyAllowed: boolean | null): CameraErrorCode {
	switch (errorName(err)) {
		case "NotAllowedError":
		case "PermissionDeniedError":
			return policyAllowed === false ? "policy-blocked" : "not-allowed"
		case "SecurityError":
			return policyAllowed === false ? "policy-blocked" : "security"
		case "NotFoundError":
		case "DevicesNotFoundError":
			return "not-found"
		case "NotReadableError":
		case "TrackStartError":
		case "AbortError":
			return "in-use"
		case "OverconstrainedError":
		case "ConstraintNotSatisfiedError":
			return "overconstrained"
		case "TypeError":
			return "unsupported"
		default:
			return "unknown"
	}
}

// --- Streams and devices ---------------------------------------------------------

/** Stop every track of a stream (each in its own try). Returns how many were stopped. */
export function stopStream(stream: MediaStream | null | undefined): number {
	if (!stream) return 0
	let n = 0
	let tracks: MediaStreamTrack[] = []
	try {
		tracks = stream.getTracks()
	} catch {
		return 0
	}
	for (const track of tracks) {
		try {
			track.stop()
			n += 1
		} catch {
			// a broken track must not keep the others running
		}
	}
	return n
}

export type CameraDevice = {
	deviceId: string
	/** A readable name; a numbered fallback until permission reveals real labels. */
	label: string
	hasLabel: boolean
}

export async function listCameras(env: CameraEnv): Promise<CameraDevice[]> {
	const md = env.mediaDevices
	if (!md || typeof md.enumerateDevices !== "function") return []
	let all: MediaDeviceInfo[]
	try {
		all = await md.enumerateDevices()
	} catch {
		return []
	}
	return all
		.filter((d) => d.kind === "videoinput")
		.map((d, i) => ({
			deviceId: d.deviceId,
			label: d.label || `Camera ${i + 1}`,
			hasLabel: Boolean(d.label),
		}))
}

export function readStoredCamera(env: CameraEnv): string | null {
	try {
		return env.localStorage?.getItem(CAMERA_DEVICE_STORAGE_KEY) || null
	} catch {
		return null
	}
}

export function writeStoredCamera(env: CameraEnv, deviceId: string | null): void {
	try {
		if (deviceId) env.localStorage?.setItem(CAMERA_DEVICE_STORAGE_KEY, deviceId)
		else env.localStorage?.removeItem(CAMERA_DEVICE_STORAGE_KEY)
	} catch {
		// storage blocked (private mode etc.): the choice is a convenience only
	}
}

function readMarker(env: CameraEnv): boolean {
	try {
		return env.sessionStorage?.getItem(CAMERA_RELOAD_MARKER_KEY) === "1"
	} catch {
		return false
	}
}

function writeMarker(env: CameraEnv, on: boolean): void {
	try {
		if (on) env.sessionStorage?.setItem(CAMERA_RELOAD_MARKER_KEY, "1")
		else env.sessionStorage?.removeItem(CAMERA_RELOAD_MARKER_KEY)
	} catch {
		// ignore
	}
}

/** Call right before the student-initiated full reload. One attempt per tab: no loops. */
export function markCameraReloadAttempt(env: CameraEnv): void {
	writeMarker(env, true)
}

function liveVideoTrack(stream: MediaStream | null): MediaStreamTrack | null {
	if (!stream) return null
	try {
		return stream.getVideoTracks().find((t) => t.readyState === "live") ?? null
	} catch {
		return null
	}
}

// --- Lobby readiness ----------------------------------------------------------------

/** The lobby's Continue button: a live camera AND the per-session acknowledgement. */
export function lobbyReady(state: Pick<CameraState, "status" | "stream">, acknowledged: boolean): boolean {
	return acknowledged && state.status === "on" && liveVideoTrack(state.stream) !== null
}

// --- Controller ----------------------------------------------------------------------

export type CameraStatus = "off" | "starting" | "on" | "error"
export type CameraPermission = "unknown" | "granted" | "denied"

export type CameraState = {
	status: CameraStatus
	stream: MediaStream | null
	devices: CameraDevice[]
	selectedDeviceId: string | null
	permission: CameraPermission
	error: CameraErrorInfo | null
	/** Non-blocking status message (e.g. "using the default camera"). */
	notice: string | null
}

export const INITIAL_CAMERA_STATE: CameraState = Object.freeze({
	status: "off",
	stream: null,
	devices: [],
	selectedDeviceId: null,
	permission: "unknown",
	error: null,
	notice: null,
}) as CameraState

const NOTICE_MUTED = "The camera picture is paused by your device or browser."
const NOTICE_DEFAULT_CAMERA = "Your saved camera is not available, so the default camera is used."
const NOTICE_SELECTED_GONE = "Your selected camera was disconnected. The default camera will be used."

/**
 * Owns at most one camera stream. One instance per interview workspace, so the
 * same stream can move from the lobby to the room without reopening.
 *
 * Guarantees: a request only from start(); concurrent start() calls share one
 * request; a request that finishes after stop()/switch/detach is stopped at
 * once (stale guard); the old stream is stopped BEFORE a new one is requested;
 * stop(), detach() and `pagehide` stop every track.
 */
export class CameraController {
	private env: CameraEnv
	private state: CameraState
	private listeners = new Set<() => void>()
	private requestId = 0
	private inFlight: Promise<boolean> | null = null
	/** A camera chosen while a request was pending (undefined = none). */
	private pendingDevice: string | null | undefined = undefined
	private attached = false
	private trackCleanup: (() => void) | null = null

	constructor(env: CameraEnv) {
		this.env = env
		this.state = { ...INITIAL_CAMERA_STATE, selectedDeviceId: readStoredCamera(env) }
	}

	// useSyncExternalStore plumbing (stable arrow functions)
	subscribe = (listener: () => void): (() => void) => {
		this.listeners.add(listener)
		return () => {
			this.listeners.delete(listener)
		}
	}

	getSnapshot = (): CameraState => this.state

	private set(patch: Partial<CameraState>): void {
		this.state = { ...this.state, ...patch }
		for (const l of [...this.listeners]) l()
	}

	/** Start listening for pagehide / devicechange. Never requests the camera. */
	attach(): void {
		if (this.attached) return
		this.attached = true
		this.env.window?.addEventListener("pagehide", this.onPageHide)
		this.env.mediaDevices?.addEventListener?.("devicechange", this.onDeviceChange)
	}

	/** Stop the camera and remove every listener (component unmount). Re-attachable. */
	detach(): void {
		this.stop()
		if (!this.attached) return
		this.attached = false
		this.env.window?.removeEventListener("pagehide", this.onPageHide)
		this.env.mediaDevices?.removeEventListener?.("devicechange", this.onDeviceChange)
	}

	private onPageHide = (): void => {
		this.stop()
	}

	private onDeviceChange = (): void => {
		void this.refreshDevices()
		if (this.state.status === "on" && liveVideoTrack(this.state.stream) === null) this.lost()
	}

	/** Request the camera. Call ONLY from a user action. Resolves true when live. */
	start(deviceId?: string | null): Promise<boolean> {
		if (this.inFlight) return this.inFlight
		const id = ++this.requestId
		const wanted = deviceId === undefined ? this.state.selectedDeviceId : deviceId
		const p: Promise<boolean> = this.run(id, wanted).then(
			(ok) => {
				if (this.inFlight === p) this.inFlight = null
				const pending = this.pendingDevice
				this.pendingDevice = undefined
				// The student picked another camera while this one was opening.
				if (ok && id === this.requestId && pending !== undefined && pending !== this.state.selectedDeviceId) {
					return this.start(pending)
				}
				return ok
			},
			(err: unknown) => {
				if (this.inFlight === p) this.inFlight = null
				this.pendingDevice = undefined
				throw err
			},
		)
		this.inFlight = p
		return p
	}

	private fail(code: CameraErrorCode): false {
		if (code === "policy-blocked" && readMarker(this.env)) code = "policy-still-blocked"
		this.pendingDevice = undefined
		const reloadSuggested = code === "policy-blocked" || code === "security"
		this.set({
			status: "error",
			stream: null,
			error: { code, message: describeCameraError(code), reloadSuggested },
			permission: code === "not-allowed" ? "denied" : this.state.permission,
		})
		return false
	}

	private async run(id: number, wanted: string | null): Promise<boolean> {
		// Old tracks first: some devices cannot open a second camera stream.
		this.releaseStream()
		this.set({ status: "starting", error: null, notice: null })

		const support = cameraSupport(this.env)
		if (support !== "ok") return this.fail(support)
		const policy = cameraPolicyAllowed(this.env)
		if (policy === false) return this.fail("policy-blocked") // known blocked: don't even ask

		const gum = this.env.mediaDevices!.getUserMedia!.bind(this.env.mediaDevices)
		let stream: MediaStream
		let notice: string | null = null
		try {
			stream = await gum(cameraConstraints(wanted))
		} catch (err) {
			if (id !== this.requestId) return false // superseded; nothing to clean up
			const code = classifyCameraError(err, policy)
			if (!wanted || (code !== "overconstrained" && code !== "not-found")) return this.fail(code)
			// The saved/selected camera is gone: forget it and try the default camera once.
			writeStoredCamera(this.env, null)
			this.set({ selectedDeviceId: null })
			try {
				stream = await gum(cameraConstraints(null))
				notice = NOTICE_DEFAULT_CAMERA
			} catch (err2) {
				if (id !== this.requestId) return false
				return this.fail(classifyCameraError(err2, policy))
			}
		}

		if (id !== this.requestId) {
			stopStream(stream) // stale: stop() or a newer request happened meanwhile
			return false
		}
		const track = liveVideoTrack(stream)
		if (!track) {
			stopStream(stream)
			return this.fail("unknown")
		}

		let actual: string | null = null
		try {
			actual = track.getSettings?.().deviceId || null
		} catch {
			actual = null
		}
		const selected = actual ?? wanted ?? null
		if (selected) writeStoredCamera(this.env, selected)
		writeMarker(this.env, false) // the camera works on this document

		this.watchTrack(stream, track)
		this.set({
			status: "on",
			stream,
			error: null,
			notice,
			permission: "granted",
			selectedDeviceId: selected,
		})
		void this.refreshDevices() // labels are visible once permission is granted

		return true
	}

	private watchTrack(stream: MediaStream, track: MediaStreamTrack): void {
		this.trackCleanup?.()
		const onEnded = () => {
			if (this.state.stream === stream) this.lost()
		}
		const onMute = () => {
			if (this.state.stream === stream) this.set({ notice: NOTICE_MUTED })
		}
		const onUnmute = () => {
			if (this.state.stream === stream && this.state.notice === NOTICE_MUTED) this.set({ notice: null })
		}
		track.addEventListener("ended", onEnded)
		track.addEventListener("mute", onMute)
		track.addEventListener("unmute", onUnmute)
		this.trackCleanup = () => {
			track.removeEventListener("ended", onEnded)
			track.removeEventListener("mute", onMute)
			track.removeEventListener("unmute", onUnmute)
		}
	}

	/** The live camera went away (unplugged, revoked, taken by the OS). */
	private lost(): void {
		this.requestId++
		this.releaseStream()
		this.fail("device-lost")
		void this.refreshDevices()
	}

	private releaseStream(): void {
		this.trackCleanup?.()
		this.trackCleanup = null
		const old = this.state.stream
		if (old) {
			stopStream(old)
			this.state = { ...this.state, stream: null }
		}
	}

	/** Turn the camera off: stops every track; cancels a pending request. */
	stop(): void {
		this.requestId++
		this.pendingDevice = undefined
		this.inFlight = null
		const hadSomething = this.state.stream !== null || this.state.status === "starting"
		this.releaseStream()
		if (hadSomething || this.state.status === "on") this.set({ status: "off", stream: null, notice: null })
	}

	/** Choose a camera (remembered locally). A running camera switches to it. */
	switchDevice(deviceId: string | null): Promise<boolean> {
		writeStoredCamera(this.env, deviceId)
		this.set({ selectedDeviceId: deviceId })
		if (this.state.status === "starting") {
			this.pendingDevice = deviceId // applied once the pending request finishes
			return this.inFlight ?? Promise.resolve(false)
		}
		if (this.state.status === "on") return this.start(deviceId)
		return Promise.resolve(false)
	}

	/** Re-read the camera list; forgets a selected camera that has been unplugged. */
	async refreshDevices(): Promise<CameraDevice[]> {
		const devices = await listCameras(this.env)
		const patch: Partial<CameraState> = { devices }
		const sel = this.state.selectedDeviceId
		const idsKnown = devices.length > 0 && devices.every((d) => d.deviceId)
		if (sel && (devices.length === 0 || (idsKnown && !devices.some((d) => d.deviceId === sel)))) {
			if (this.state.status !== "on" && this.state.status !== "starting") {
				writeStoredCamera(this.env, null)
				patch.selectedDeviceId = null
				patch.notice = NOTICE_SELECTED_GONE
			}
		}
		this.set(patch)
		return devices
	}
}
