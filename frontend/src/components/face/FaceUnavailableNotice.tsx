import Link from "next/link"

/**
 * Face recognition is OFF unless the build sets NEXT_PUBLIC_FACE_ENABLED=true
 * (it must match the AI worker's FACE_ENABLED). When off, the face pages show
 * this notice and hide their controls instead of offering actions that fail.
 */
export const FACE_ENABLED = process.env.NEXT_PUBLIC_FACE_ENABLED === "true"

export default function FaceUnavailableNotice() {
	return (
		<div
			role="status"
			className="rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-800 dark:text-amber-200"
		>
			<p className="font-medium">
				Face attendance is not available on this deployment.
			</p>
			<p className="mt-1">
				Face recognition is turned off here, so faces cannot be enrolled or
				matched. Please use{" "}
				<Link
					href="/dashboard/attendance"
					className="font-medium underline hover:no-underline"
				>
					manual attendance
				</Link>{" "}
				instead.
			</p>
		</div>
	)
}
