"use client"

/** Phase 8.2: a coding session's screens (instructions -> room -> finished), chosen by the server's status. */

import type { InterviewSession } from "../mockInterviewApi"
import CodingFinished from "./CodingFinished"
import CodingInstructions from "./CodingInstructions"
import CodingRoom from "./CodingRoom"
import { codingStage } from "./codingFlow"

export default function CodingWorkspace({
	session,
	onChange,
	onNew,
}: {
	session: InterviewSession
	onChange: (s: InterviewSession) => void
	onNew: () => void
}) {
	const stage = codingStage(session)
	if (stage === "instructions") return <CodingInstructions session={session} onChange={onChange} />
	if (stage === "room") return <CodingRoom session={session} onChange={onChange} />
	return <CodingFinished session={session} onNew={onNew} />
}
