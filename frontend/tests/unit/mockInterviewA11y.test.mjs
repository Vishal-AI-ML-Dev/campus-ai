// Phase 6.6: accessibility fixes in the Mock Interview UI (text, voice and video).
// Behaviour (focus moves, Escape, announcements) is also exercised in the browser smoke run.
// Run: npm run test:unit
import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { dirname, join } from "node:path"
import { describe, it } from "node:test"
import { fileURLToPath } from "node:url"

const MI = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "src", "components", "mock-interview")
const read = (f) => readFileSync(join(MI, f), "utf8")

describe("Phase 6.6: End interview confirmation", () => {
	const c = read("EndInterviewControl.tsx")

	it("focuses the safe choice on open, returns focus on close, closes on Escape", () => {
		assert.match(c, /if \(open\) keepRef\.current\?\.focus\(\)/)
		assert.match(c, /endRef\.current\?\.focus\(\)/)
		assert.match(c, /e\.key === "Escape"/)
		assert.match(c, /role="group"\s+aria-label="Confirm ending the interview"/)
	})

	it("keeps the existing texts", () => {
		for (const t of ["End interview", "End now?", "Yes, end", "Keep going", "Ending…"]) assert.ok(c.includes(t), t)
	})

	it("is used by both the text/voice room and the video room", () => {
		for (const f of ["InterviewRoom.tsx", "VideoInterviewRoom.tsx"]) {
			const s = read(f)
			assert.match(s, /<EndInterviewControl busy=\{busy !== null\} ending=\{busy === "finish"\} onConfirm=\{finish\} \/>/, f)
			assert.doesNotMatch(s, /confirmEnd/, f)
		}
	})
})

describe("Phase 6.6: timer", () => {
	const r = read("InterviewRoom.tsx")

	it("has a valid accessible name (role timer) and announces only milestones", () => {
		assert.match(r, /role="timer"/)
		assert.match(r, /className="sr-only" aria-live="polite"/)
		assert.match(r, /left <= 0 \? "Time is up\. Submit your last answer\." : left <= 60 \? "One minute remaining\." : left <= 300 \? "Five minutes remaining\." : ""/)
	})
})

describe("Phase 6.6: video room", () => {
	const v = read("VideoInterviewRoom.tsx")

	it("toggles keep one name and expose state with aria-pressed", () => {
		assert.match(v, /pressed=\{micMuted\}[\s\S]{0,120}>\s*Mute mic\s*</)
		assert.match(v, /<ToolbarButton pressed=\{voiceMuted\} onClick=\{toggleVoice\}>\s*Mute voice\s*</)
		assert.doesNotMatch(v, /Unmute mic|Unmute voice|label=\{micMuted|label=\{voiceMuted|label=\{camOn/)
	})

	it("each new question receives focus; the speaking line is not a chatty live region", () => {
		assert.match(v, /if \(currentSeq !== null\) questionRef\.current\?\.focus\(\{ preventScroll: true \}\)/)
		assert.match(v, /ref=\{questionRef\}\s+tabIndex=\{-1\}/)
		assert.doesNotMatch(v, /text-xs text-slate-500 dark:text-slate-400" role="status">\s*\{speaking/)
	})
})

describe("Phase 6.6: screen changes and answer boxes", () => {
	it("the lobby and the finished view focus their heading on entry", () => {
		assert.match(read("VideoLobby.tsx"), /id="mi-video-lobby-title" ref=\{headingRef\} tabIndex=\{-1\}/)
		assert.match(read("InterviewFinished.tsx"), /id="mi-finished-title" ref=\{headingRef\} tabIndex=\{-1\}/)
	})

	it("the voice room focuses the new answer box; the option is off by default", () => {
		const box = read("VoiceAnswerBox.tsx")
		assert.match(box, /focusOnMount = false/)
		assert.match(box, /if \(focusOnMount\) \(micRef\.current \?\? areaRef\.current\)\?\.focus\(\)/)
		assert.match(read("InterviewRoom.tsx"), /speaking=\{tts\.speaking\}\s+focusOnMount\s+\/>/)
		assert.doesNotMatch(read("VideoInterviewRoom.tsx"), /focusOnMount/) // the video room focuses the question instead
	})
})
