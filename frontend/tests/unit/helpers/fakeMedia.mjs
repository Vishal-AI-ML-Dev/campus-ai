// Fake browser media APIs for unit tests (no camera hardware). Not a test file itself.

export class FakeTrack extends EventTarget {
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

export class FakeStream {
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

export class FakeMediaDevices extends EventTarget {
	constructor(cameras = [{ deviceId: "cam-1", label: "Front Camera" }, { deviceId: "cam-2", label: "USB Camera" }]) {
		super()
		this.cameras = cameras
		this.calls = []
		this.streams = []
		this.failures = []
		this.labelsVisible = false
	}
	async getUserMedia(constraints) {
		this.calls.push(constraints)
		const failure = this.failures.shift()
		if (failure) throw new DOMException(`fake ${failure}`, failure)
		const exact = constraints.video?.deviceId?.exact
		if (exact && !this.cameras.some((c) => c.deviceId === exact)) throw new DOMException("x", "OverconstrainedError")
		if (this.cameras.length === 0) throw new DOMException("x", "NotFoundError")
		const stream = new FakeStream([new FakeTrack("video", exact ?? this.cameras[0].deviceId)])
		this.streams.push(stream)
		this.labelsVisible = true
		return stream
	}
	async enumerateDevices() {
		return this.cameras.map((c) => ({
			kind: "videoinput",
			deviceId: this.labelsVisible ? c.deviceId : "",
			label: this.labelsVisible ? c.label : "",
		}))
	}
}

export class MemStorage {
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

/** A CameraEnv backed by fakes. Pass the same localStorage to simulate a refresh. */
export function makeEnv({ policy = true, mediaDevices = new FakeMediaDevices(), localStorage = new MemStorage() } = {}) {
	return {
		mediaDevices,
		isSecureContext: true,
		document: { permissionsPolicy: { allowsFeature: (f) => (f === "camera" ? policy : true) } },
		window: new EventTarget(),
		localStorage,
		sessionStorage: new MemStorage(),
	}
}

export const allTracks = (md) => md.streams.flatMap((s) => s.getTracks())
