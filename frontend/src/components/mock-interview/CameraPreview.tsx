"use client"

/**
 * Phase 6.2: local camera self-view. The MediaStream is attached to a <video>
 * element with `srcObject` and shown mirrored, like a mirror. Nothing is drawn
 * to a canvas, captured, recorded or uploaded; this component never stops the
 * camera itself (the owner, useCamera, does) and only detaches it on unmount.
 */

import { useEffect, useRef } from "react"
import { Spinner } from "@/components/ui/states"
import type { CameraErrorInfo, CameraStatus } from "./cameraMedia"

function CameraOffIcon({ className = "h-8 w-8" }: { className?: string }) {
	return (
		<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" className={className} aria-hidden="true">
			<path d="M3 3l18 18" strokeLinecap="round" />
			<path d="M15 10.5l5-3v9l-5-3M10.5 7H13a2 2 0 0 1 2 2v2.5M15 15v.5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V9a2 2 0 0 1 1.5-1.94" strokeLinecap="round" strokeLinejoin="round" />
		</svg>
	)
}

export default function CameraPreview({
	stream,
	status,
	error,
	className = "",
}: {
	stream: MediaStream | null
	status: CameraStatus
	error?: CameraErrorInfo | null
	className?: string
}) {
	const videoRef = useRef<HTMLVideoElement>(null)
	const live = status === "on" && stream !== null

	useEffect(() => {
		const video = videoRef.current
		if (!video) return
		video.srcObject = live ? stream : null
		if (live) {
			// autoPlay + muted normally suffice; play() covers browsers that pause on re-attach.
			video.play().catch(() => {})
		}
		return () => {
			video.srcObject = null // detach only; the owner stops the tracks
		}
	}, [live, stream])

	return (
		<div
			className={`relative aspect-video w-full overflow-hidden rounded-2xl border border-slate-200 bg-slate-900 dark:border-white/10 ${className}`}
		>
			<video
				ref={videoRef}
				autoPlay
				muted
				playsInline
				// Phase 6.6: the self-view stays inside this page (no floating
				// picture-in-picture window outliving the interview screen).
				disablePictureInPicture
				aria-label="Your camera preview (only visible to you)"
				className={`h-full w-full -scale-x-100 object-cover ${live ? "" : "hidden"}`}
			/>

			{!live ? (
				<div className="absolute inset-0 flex flex-col items-center justify-center gap-2 px-4 text-center text-slate-300">
					{status === "starting" ? (
						<>
							<Spinner className="h-6 w-6" />
							<p className="text-sm">Starting your camera… Allow access if your browser asks.</p>
						</>
					) : status === "error" && error ? (
						<>
							<CameraOffIcon />
							<p role="alert" className="max-w-sm text-sm text-red-200">
								{error.message}
							</p>
						</>
					) : (
						<>
							<CameraOffIcon />
							<p className="text-sm">Camera is off</p>
						</>
					)}
				</div>
			) : null}

			{live ? (
				<span
					role="status"
					className="absolute left-3 top-3 inline-flex items-center gap-1.5 rounded-full bg-black/60 px-2.5 py-1 text-xs font-medium text-white"
				>
					<span className="h-2 w-2 animate-pulse rounded-full bg-red-500" aria-hidden="true" />
					Camera on · preview only
				</span>
			) : null}
		</div>
	)
}
