// Phase 6.6: privacy guards for the whole Mock Interview frontend (components AND route).
// Approved design D2: the camera is a local preview only. Nothing may record, capture,
// encode, stream or upload media, and camera data must never reach the API client.
// Run: npm run test:unit
import assert from "node:assert/strict"
import { readdirSync, readFileSync } from "node:fs"
import { dirname, join, relative } from "node:path"
import { describe, it } from "node:test"
import { fileURLToPath } from "node:url"

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..")
const DIRS = [join(ROOT, "src", "components", "mock-interview"), join(ROOT, "src", "app", "dashboard", "mock-interview")]

/** Source without comments, so a comment saying "no canvas" is not a hit. */
function code(text) {
	return text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:"'`])\/\/.*$/gm, "$1")
}

function walk(dir) {
	return readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
		e.isDirectory() ? walk(join(dir, e.name)) : /\.(ts|tsx)$/.test(e.name) ? [join(dir, e.name)] : [],
	)
}

const files = DIRS.flatMap(walk).map((p) => ({ name: relative(ROOT, p).replaceAll("\\", "/"), src: code(readFileSync(p, "utf8")) }))
const byName = (n) => files.find((f) => f.name.endsWith(`/${n}`))

describe("Phase 6.6: video privacy guards", () => {
	it("covers every Mock Interview source file, including the route and the Phase 6 files", () => {
		assert.ok(files.length >= 24, `${files.length} files`) // 24 at Phase 6.6; the named list below is the real check
		for (const n of [
			"page.tsx", "cameraMedia.ts", "useCamera.ts", "CameraPreview.tsx", "CameraDeviceSelect.tsx",
			"VideoPrivacyNotice.tsx", "VideoLobby.tsx", "VideoInterviewWorkspace.tsx", "VideoInterviewRoom.tsx",
			"videoInterviewFlow.ts", "EndInterviewControl.tsx", "VoiceAnswerBox.tsx", "InterviewRoom.tsx",
		]) {
			assert.ok(byName(n), `missing ${n}`)
		}
	})

	it("never records, captures, encodes, streams or uploads media", () => {
		const banned = [
			/\bMediaRecorder\b/, /\bcaptureStream\b/, /\btoDataURL\b/, /\btoBlob\b/, /\bdrawImage\b/, /\bgetImageData\b/,
			/\bImageCapture\b/, /\bgetDisplayMedia\b/, /<canvas\b/, /createElement\(\s*["']canvas["']/, /\bOffscreenCanvas\b/,
			/\bgetContext\(/, /\bcreateObjectURL\b/, /\bFormData\b/, /\bnew Blob\b/, /\bsendBeacon\b/, /\bWebSocket\b/,
			/\bRTCPeerConnection\b/, /\bMediaSource\b/, /\brequestVideoFrameCallback\b/,
		]
		for (const f of files) for (const re of banned) assert.doesNotMatch(f.src, re, `${f.name}: ${re}`)
	})

	it("only cameraMedia.ts touches the camera API, always video-only", () => {
		for (const f of files) {
			if (!f.name.endsWith("/cameraMedia.ts")) assert.doesNotMatch(f.src, /getUserMedia|enumerateDevices/, f.name)
			assert.doesNotMatch(f.src, /audio:\s*true/, f.name)
		}
	})

	it("camera modules make no network calls", () => {
		for (const n of [
			"cameraMedia.ts", "useCamera.ts", "CameraPreview.tsx", "CameraDeviceSelect.tsx", "VideoPrivacyNotice.tsx",
			"VideoLobby.tsx", "videoInterviewFlow.ts", "EndInterviewControl.tsx",
		]) {
			const f = byName(n)
			assert.doesNotMatch(f.src, /mockInterviewApi|@\/lib\/api|fetch\(|XMLHttpRequest|useApiResource/, n)
		}
	})

	it("the API client never carries camera data", () => {
		const api = byName("mockInterviewApi.ts").src
		assert.doesNotMatch(api, /deviceId|camera|MediaStream|stream|frame|snapshot|image/i)
		// The only answer fields are the reviewed text, its seq and the typed/stt label.
		assert.match(api, /source \? \{ turn_seq: turnSeq, answer, source \} : \{ turn_seq: turnSeq, answer \}/)
	})

	it("no face-recognition code is imported", () => {
		for (const f of files) assert.doesNotMatch(f.src, /components\/face|faceApi|face-enroll|face-attendance/, f.name)
	})

	it("the camera preview cannot be popped out (picture-in-picture disabled)", () => {
		assert.match(byName("CameraPreview.tsx").src, /\bdisablePictureInPicture\b/)
	})
})
