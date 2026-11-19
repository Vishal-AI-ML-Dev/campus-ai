"use client"

/**
 * Phase 6.2: React binding for CameraController (logic in cameraMedia.ts).
 *
 * Call it ONCE where the camera should live: the future interview workspace
 * (Phase 6.3), so the lobby and the room share one stream without reopening
 * the camera. Pass the returned handle down as a prop.
 *
 *   - Mounting never requests the camera; only `start()` does (from a click).
 *   - Unmount and `pagehide` stop every track.
 *   - Video only (`audio: false`): browser speech recognition keeps the
 *     microphone, as in Phase 5.
 */

import { useCallback, useEffect, useState, useSyncExternalStore } from "react"
import {
	browserCameraEnv,
	CameraController,
	INITIAL_CAMERA_STATE,
	markCameraReloadAttempt,
	type CameraState,
} from "./cameraMedia"

export type CameraHandle = CameraState & {
	/** Request the camera. Call from a user action only. */
	start: () => Promise<boolean>
	/** Turn the camera off (stops every track). */
	stop: () => void
	/** Choose a camera; a running camera switches to it. Remembered on this device. */
	switchDevice: (deviceId: string | null) => Promise<boolean>
	refreshDevices: () => Promise<unknown>
	/** Full reload of this page so the route's camera policy applies (student-initiated only). */
	reloadToEnableCamera: () => void
}

const serverSnapshot = () => INITIAL_CAMERA_STATE

export function useCamera(): CameraHandle {
	// Lazy and side-effect free: the constructor only reads the saved camera id.
	const [controller] = useState(() => new CameraController(browserCameraEnv()))
	const state = useSyncExternalStore(controller.subscribe, controller.getSnapshot, serverSnapshot)

	useEffect(() => {
		controller.attach()
		return () => controller.detach() // stops the camera on unmount
	}, [controller])

	const start = useCallback(() => controller.start(), [controller])
	const stop = useCallback(() => controller.stop(), [controller])
	const switchDevice = useCallback((id: string | null) => controller.switchDevice(id), [controller])
	const refreshDevices = useCallback(() => controller.refreshDevices(), [controller])
	const reloadToEnableCamera = useCallback(() => {
		controller.stop()
		markCameraReloadAttempt(browserCameraEnv())
		window.location.reload()
	}, [controller])

	return { ...state, start, stop, switchDevice, refreshDevices, reloadToEnableCamera }
}
