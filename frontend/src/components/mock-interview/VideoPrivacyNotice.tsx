"use client"

/**
 * Phase 6.2: what happens to camera and microphone data (approved design D2,
 * D8, D10) plus the per-session acknowledgement. The acknowledgement lives in
 * component state only; nothing about it (or the camera) is sent to the server.
 */

export default function VideoPrivacyNotice({
	acknowledged,
	onAcknowledgedChange,
	id = "mi-video-privacy",
}: {
	acknowledged: boolean
	onAcknowledgedChange: (value: boolean) => void
	id?: string
}) {
	return (
		<section
			aria-labelledby={`${id}-title`}
			className="space-y-3 rounded-xl border border-slate-200 bg-slate-50 p-4 text-sm dark:border-white/10 dark:bg-slate-800/50"
		>
			<h3 id={`${id}-title`} className="font-semibold">
				How your camera is used
			</h3>
			<ul className="list-disc space-y-1 pl-5 text-slate-600 dark:text-slate-300">
				<li>Your camera is used only for a live preview on this screen, so you can practise as in a real interview.</li>
				<li>The video is <strong>not recorded</strong> and <strong>not uploaded</strong> to Campus AI or anyone else.</li>
				<li>The video is <strong>not analysed</strong> by AI: no face, emotion, eye or identity analysis.</li>
				<li>Only the answer text you review and submit is saved and used for your feedback report.</li>
				<li>
					Spoken answers are converted to text by your browser. Some browsers (e.g. Chrome) send microphone
					audio to their own speech service to do this. Campus AI never receives audio.
				</li>
				<li>You can turn the camera off at any time. It switches off when you leave this page.</li>
			</ul>
			<label htmlFor={`${id}-ack`} className="flex items-start gap-2">
				<input
					id={`${id}-ack`}
					type="checkbox"
					checked={acknowledged}
					onChange={(e) => onAcknowledgedChange(e.target.checked)}
					className="mt-0.5 h-4 w-4 rounded border-slate-300 text-indigo-600 focus:ring-indigo-500"
				/>
				<span>I understand how my camera and microphone are used in this practice interview.</span>
			</label>
		</section>
	)
}
