/**
 * Phase 6.3: video interview flow logic (no React; unit-tested in Node).
 *
 * Which screen a video session shows, when the camera must be released, how
 * the camera and microphone are described to the student, and a guard that
 * stops a double click from sending the same request twice.
 *
 * Camera and microphone are separate (approved D10): the camera stream is
 * video-only and owned by useCamera; the microphone belongs to the browser's
 * speech recognition (Phase 5 VoiceAnswerBox). Muting the microphone never
 * touches the camera, and turning the camera off never touches dictation.
 */

import type { CameraErrorInfo, CameraStatus } from "./cameraMedia"

export type VideoStage = "lobby" | "room" | "finished"

type SessionLike = { status: string; awaiting: string }

/** Same rules as the text/voice page: not started -> lobby, running -> room, else finished. */
export function videoStage(session: SessionLike): VideoStage {
	if (session.status === "setup" || session.awaiting === "start") return "lobby"
	if (session.status === "in_progress") return "room"
	return "finished"
}

/** Completed, abandoned, failed or evaluating: the camera must be off. */
export function isTerminalStatus(status: string): boolean {
	return status !== "setup" && status !== "in_progress"
}

/**
 * Release the camera once the session has ended (finish, abandon, time-out,
 * the engine ending the interview, or a terminal status seen after a refresh).
 * Returns true when it stopped the camera.
 */
export function releaseCameraIfEnded(camera: { stop: () => void }, status: string): boolean {
	if (!isTerminalStatus(status)) return false
	camera.stop()
	return true
}

/**
 * At most one action at a time. A second call while one is running is ignored
 * (returns undefined), even within the same tick, before React re-renders a
 * disabled button.
 */
export function createActionGuard() {
	let running: string | null = null
	return {
		get current(): string | null {
			return running
		},
		async run<T>(name: string, fn: () => Promise<T>): Promise<T | undefined> {
			if (running !== null) return undefined
			running = name
			try {
				return await fn()
			} finally {
				running = null
			}
		},
	}
}

export type CameraLike = { status: CameraStatus; error: CameraErrorInfo | null; notice: string | null }

/** One line for the room toolbar. The interview never depends on the camera (D7). */
export function cameraStatusText(camera: CameraLike): string {
	switch (camera.status) {
		case "on":
			return camera.notice ? `Camera on · ${camera.notice}` : "Camera on (preview only)"
		case "starting":
			return "Starting camera…"
		case "error":
			return "Camera unavailable. The interview continues without it."
		default:
			return "Camera off. The interview continues without it."
	}
}

export type MicState = {
	/** The browser has speech recognition. */
	supported: boolean
	listening: boolean
	/** Permission refused or the language/service is unavailable for this page. */
	blocked: boolean
	/** The student muted the microphone in the room toolbar. */
	muted: boolean
}

export function micStatusText(mic: MicState): string {
	if (!mic.supported) return "Speech input not available in this browser. Type your answers."
	if (mic.blocked) return "Microphone blocked. Type your answers."
	if (mic.muted) return "Microphone muted"
	if (mic.listening) return "Listening…"
	return "Microphone ready. Press Start speaking to answer."
}
