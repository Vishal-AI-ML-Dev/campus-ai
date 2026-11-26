"use client"

/**
 * Phase 6.2: camera picker. Browsers hide camera names until camera access is
 * allowed, so numbered names are shown until then. The choice is remembered
 * on this device only (useCamera -> localStorage) and never sent anywhere.
 */

import type { CameraDevice } from "./cameraMedia"

export default function CameraDeviceSelect({
	devices,
	selectedDeviceId,
	onSelect,
	disabled = false,
	id = "mi-camera-select",
}: {
	devices: CameraDevice[]
	selectedDeviceId: string | null
	onSelect: (deviceId: string | null) => void
	disabled?: boolean
	id?: string
}) {
	// Before permission, Chrome lists cameras with an empty id: nothing to choose yet.
	const choosable = devices.filter((d) => d.deviceId)
	const labelsHidden = devices.length > 0 && devices.every((d) => !d.hasLabel)
	const value = selectedDeviceId && choosable.some((d) => d.deviceId === selectedDeviceId) ? selectedDeviceId : ""

	return (
		<div className="space-y-1">
			<label htmlFor={id} className="text-sm font-medium">
				Camera
			</label>
			<select
				id={id}
				value={value}
				disabled={disabled || choosable.length === 0}
				onChange={(e) => onSelect(e.target.value || null)}
				aria-describedby={`${id}-help`}
				className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm outline-none focus:border-indigo-400 focus:ring-2 focus:ring-indigo-400/30 disabled:opacity-60 dark:border-white/10 dark:bg-slate-900"
			>
				<option value="">Default camera</option>
				{choosable.map((d) => (
					<option key={d.deviceId} value={d.deviceId}>
						{d.label}
					</option>
				))}
			</select>
			<p id={`${id}-help`} className="text-xs text-slate-500 dark:text-slate-400">
				{devices.length === 0
					? "No camera detected yet. Connect one, or turn on the camera to check again."
					: labelsHidden
						? "Camera names appear after you allow camera access."
						: "Your choice is remembered on this device only."}
			</p>
		</div>
	)
}
