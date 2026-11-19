"use client"

/**
 * Phase 6.2: video interview lobby (camera check before entering the room).
 *
 * Takes the camera handle from its parent (one `useCamera()` per interview
 * workspace) so the same stream can continue into the room without reopening.
 * Continue needs a live camera AND the per-session privacy acknowledgement
 * (approved D7, D8); the acknowledgement is component state only.
 *
 * NOT wired into the student flow yet: Phase 6.3 renders it, behind
 * VIDEO_UI_READY and the existing build/server gates.
 */

import { useEffect, useState } from "react"
import { Spinner } from "@/components/ui/states"
import CameraDeviceSelect from "./CameraDeviceSelect"
import CameraPreview from "./CameraPreview"
import { lobbyReady } from "./cameraMedia"
import type { CameraHandle } from "./useCamera"
import VideoPrivacyNotice from "./VideoPrivacyNotice"

function Check({ done, children }: { done: boolean; children: React.ReactNode }) {
	return (
		<li className="flex items-center gap-2">
			<span
				aria-hidden="true"
				className={`flex h-5 w-5 items-center justify-center rounded-full text-xs ${
					done ? "bg-emerald-500 text-white" : "border border-slate-300 text-transparent dark:border-white/20"
				}`}
			>
				✓
			</span>
			<span className={done ? "" : "text-slate-500 dark:text-slate-400"}>
				{children}
				<span className="sr-only">{done ? " (done)" : " (not done yet)"}</span>
			</span>
		</li>
	)
}

export default function VideoLobby({
	camera,
	onContinue,
	onCancel,
	busy = false,
	continueLabel = "Continue to interview",
}: {
	camera: CameraHandle
	onContinue: () => void
	onCancel?: () => void
	busy?: boolean
	continueLabel?: string
}) {
	const [acknowledged, setAcknowledged] = useState(false)
	const { refreshDevices } = camera
	const ready = lobbyReady(camera, acknowledged)
	const starting = camera.status === "starting"
	const on = camera.status === "on"

	// Lists cameras only (no permission prompt, no stream); names stay hidden until access is allowed.
	useEffect(() => {
		void refreshDevices()
	}, [refreshDevices])

	return (
		<section
			aria-labelledby="mi-video-lobby-title"
			className="space-y-5 rounded-2xl border border-slate-200 bg-white p-4 sm:p-6 dark:border-white/10 dark:bg-slate-900"
		>
			<div>
				<h2 id="mi-video-lobby-title" className="text-lg font-semibold">
					Camera check
				</h2>
				<p className="text-sm text-slate-500 dark:text-slate-400">
					Turn on your camera and check how you appear before the interview starts.
				</p>
			</div>

			<div className="grid gap-5 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
				<div className="space-y-3">
					<CameraPreview stream={camera.stream} status={camera.status} error={camera.error} />
					<div className="flex flex-wrap items-center gap-2">
						{on ? (
							<button
								type="button"
								onClick={camera.stop}
								className="rounded-lg border border-slate-200 px-4 py-2 text-sm text-slate-700 hover:border-slate-300 dark:border-white/10 dark:text-slate-200"
							>
								Turn off camera
							</button>
						) : (
							<button
								type="button"
								onClick={() => void camera.start()}
								disabled={starting}
								className="inline-flex items-center gap-2 rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-60 dark:bg-white dark:text-slate-900"
							>
								{starting ? <Spinner /> : null}
								{starting ? "Starting camera…" : camera.status === "error" ? "Try again" : "Turn on camera"}
							</button>
						)}
						{camera.error?.reloadSuggested ? (
							<button
								type="button"
								onClick={camera.reloadToEnableCamera}
								className="rounded-lg border border-indigo-300 px-4 py-2 text-sm font-medium text-indigo-700 hover:bg-indigo-50 dark:border-indigo-400/40 dark:text-indigo-300 dark:hover:bg-indigo-500/10"
							>
								Reload to enable camera
							</button>
						) : null}
					</div>
					{camera.notice ? (
						<p role="status" className="text-xs text-amber-700 dark:text-amber-300">
							{camera.notice}
						</p>
					) : null}
				</div>

				<div className="space-y-4">
					<CameraDeviceSelect
						devices={camera.devices}
						selectedDeviceId={camera.selectedDeviceId}
						onSelect={(id) => void camera.switchDevice(id)}
						disabled={starting}
					/>
					<div aria-live="polite">
						<p className="mb-1 text-sm font-medium">Ready to start</p>
						<ul className="space-y-1 text-sm">
							<Check done={on}>Camera is on</Check>
							<Check done={acknowledged}>Privacy notice acknowledged</Check>
						</ul>
					</div>
				</div>
			</div>

			<VideoPrivacyNotice acknowledged={acknowledged} onAcknowledgedChange={setAcknowledged} />

			<div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
				{onCancel ? (
					<button
						type="button"
						onClick={onCancel}
						disabled={busy}
						className="rounded-lg border border-slate-200 px-4 py-2 text-sm text-slate-600 hover:border-red-300 hover:text-red-600 disabled:opacity-50 dark:border-white/10 dark:text-slate-300"
					>
						Cancel interview
					</button>
				) : null}
				<button
					type="button"
					onClick={onContinue}
					disabled={!ready || busy}
					aria-describedby={ready ? undefined : "mi-video-lobby-hint"}
					className="inline-flex items-center justify-center gap-2 rounded-lg bg-gradient-to-r from-indigo-500 to-violet-500 px-5 py-2 text-sm font-medium text-white disabled:opacity-50"
				>
					{busy ? <Spinner /> : null}
					{continueLabel}
				</button>
			</div>
			{!ready ? (
				<p id="mi-video-lobby-hint" className="text-right text-xs text-slate-500 dark:text-slate-400">
					Turn on your camera and tick the acknowledgement to continue.
				</p>
			) : null}
		</section>
	)
}
