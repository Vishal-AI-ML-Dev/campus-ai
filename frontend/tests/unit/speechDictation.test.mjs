// Unit tests for the voice-interview dictation logic (no browser, no microphone).
// Run: npm run test:unit   (Node >= 22.18 / 24: runs the .ts module via built-in type stripping)
import assert from "node:assert/strict"
import { describe, it } from "node:test"
import {
	createDictation,
	describeSpeechError,
	FinalTranscriptMerger,
	PERMANENT_SPEECH_ERRORS,
	SPEECH_LANG,
	speechLang,
} from "../../src/components/mock-interview/speechDictation.ts"

// --- helpers -------------------------------------------------------------------
function results(list) {
	// list: [text, isFinal][]
	const r = { length: list.length }
	list.forEach(([t, fin], i) => {
		r[i] = { 0: { transcript: t }, length: 1, isFinal: fin }
	})
	return r
}

class FakeRec {
	static instances = []
	constructor() {
		this.started = 0
		this.stopped = 0
		this.aborted = 0
		this.onresult = null
		this.onerror = null
		this.onend = null
		FakeRec.instances.push(this)
	}
	start() {
		this.started++
		if (FakeRec.failStart) throw new Error("InvalidStateError")
	}
	stop() {
		this.stopped++
	}
	abort() {
		this.aborted++
	}
	// engine-side events
	emit(resultIndex, list) {
		this.onresult?.({ resultIndex, results: results(list) })
	}
	error(code) {
		this.onerror?.({ error: code })
	}
	end() {
		this.onend?.()
	}
}

function session(lang = "en-IN") {
	FakeRec.instances = []
	FakeRec.failStart = false
	const log = { finals: [], interims: [], errors: [], ends: [] }
	const d = createDictation(FakeRec, lang, {
		onFinal: (t) => log.finals.push(t),
		onInterim: (t) => log.interims.push(t),
		onError: (c) => log.errors.push(c),
		onEnd: (e) => log.ends.push(e),
	})
	return { d, log, rec: () => FakeRec.instances[0] }
}

// --- language mapping ------------------------------------------------------------
describe("language mapping", () => {
	it("maps English, Hindi and Hinglish to the recogniser tags", () => {
		assert.equal(speechLang("en"), "en-IN")
		assert.equal(speechLang("hi"), "hi-IN")
		assert.equal(speechLang("hinglish"), "en-IN")
		assert.equal(speechLang("unknown"), "en-IN")
		assert.deepEqual(SPEECH_LANG, { en: "en-IN", hi: "hi-IN", hinglish: "en-IN" })
	})

	it("passes the mapped language and continuous/interim settings to the engine", () => {
		for (const [language, tag] of [["en", "en-IN"], ["hi", "hi-IN"], ["hinglish", "en-IN"]]) {
			const { d, rec } = session(speechLang(language))
			assert.equal(d.start(), true)
			assert.equal(rec().lang, tag)
			assert.equal(rec().continuous, true)
			assert.equal(rec().interimResults, true)
			assert.equal(rec().maxAlternatives, 1)
		}
	})
})

// --- final-result merging ----------------------------------------------------------
describe("FinalTranscriptMerger", () => {
	it("appends separate phrases in order (desktop Chrome)", () => {
		const m = new FinalTranscriptMerger()
		assert.equal(m.accept(0, "I am a final year student"), "I am a final year student")
		assert.equal(m.accept(1, "and I enjoy teamwork"), "and I enjoy teamwork")
	})

	it("skips an exact re-delivery at the same index", () => {
		const m = new FinalTranscriptMerger()
		assert.equal(m.accept(0, "hello world"), "hello world")
		assert.equal(m.accept(0, "hello world"), null)
		assert.equal(m.accept(0, " Hello  world. "), null) // same words, different case/spacing/punctuation
	})

	it("skips an identical repeat of the previous phrase at a new index (Android)", () => {
		const m = new FinalTranscriptMerger()
		assert.equal(m.accept(0, "I like Java"), "I like Java")
		assert.equal(m.accept(1, "I like Java"), null)
	})

	it("appends only the new words of a growing phrase", () => {
		const m = new FinalTranscriptMerger()
		assert.equal(m.accept(0, "I like"), "I like")
		assert.equal(m.accept(1, "I like Java"), "Java")
		assert.equal(m.accept(2, "I like Java and Python"), "and Python")
	})

	it("handles the whole session being re-delivered with more words", () => {
		const m = new FinalTranscriptMerger()
		m.accept(0, "my name is Asha")
		m.accept(1, "I study computer science")
		assert.equal(m.accept(0, "my name is Asha I study computer science at Pune"), "at Pune")
	})

	it("ignores case and punctuation when matching, but keeps the student's text as recognised", () => {
		const m = new FinalTranscriptMerger()
		assert.equal(m.accept(0, "I like"), "I like")
		assert.equal(m.accept(1, "i like, Java."), "Java.")
	})

	it("never drops a genuinely new phrase that merely overlaps", () => {
		const m = new FinalTranscriptMerger()
		m.accept(0, "I worked on Java")
		// Shares words but is not a re-delivery: appended whole (editable later), not lost.
		assert.equal(m.accept(1, "Java and Spring Boot projects"), "Java and Spring Boot projects")
		assert.equal(m.accept(2, "I"), "I")
	})

	it("keeps standalone punctuation tokens unchanged", () => {
		const m = new FinalTranscriptMerger()
		assert.equal(m.accept(0, "results - forty percent"), "results - forty percent")
	})

	it("works for Hindi (Devanagari, danda punctuation)", () => {
		const m = new FinalTranscriptMerger()
		assert.equal(m.accept(0, "मैं ठीक हूँ"), "मैं ठीक हूँ")
		assert.equal(m.accept(1, "मैं ठीक हूँ।"), null)
		assert.equal(m.accept(2, "मैं ठीक हूँ और आप"), "और आप")
	})

	it("works for Hinglish (romanised Hindi in en-IN)", () => {
		const m = new FinalTranscriptMerger()
		assert.equal(m.accept(0, "main backend developer hoon"), "main backend developer hoon")
		assert.equal(m.accept(1, "main backend developer hoon aur Flask use karta hoon"), "aur Flask use karta hoon")
	})

	it("ignores empty and punctuation-only results", () => {
		const m = new FinalTranscriptMerger()
		assert.equal(m.accept(0, "   "), null)
		assert.equal(m.accept(1, " . , "), null)
	})
})

// --- recognition session lifecycle --------------------------------------------------
describe("createDictation", () => {
	it("shows interim text, then delivers the final once and clears the interim", () => {
		const { d, log, rec } = session()
		d.start()
		rec().emit(0, [["I am", false]])
		rec().emit(0, [["I am a student", false]])
		rec().emit(0, [["I am a student", true]])
		assert.deepEqual(log.finals, ["I am a student"])
		assert.deepEqual(log.interims, ["I am", "I am a student", ""])
	})

	it("suppresses duplicate final events across result events", () => {
		const { d, log, rec } = session()
		d.start()
		rec().emit(0, [["hello there", true]])
		rec().emit(0, [["hello there", true], ["how are", false]])
		rec().emit(1, [["hello there", true], ["how are you", true]])
		rec().emit(0, [["hello there", true], ["how are you", true]])
		assert.deepEqual(log.finals, ["hello there", "how are you"])
	})

	it("handles growing finals delivered with resultIndex stuck at 0 (Android)", () => {
		const { d, log, rec } = session()
		d.start()
		rec().emit(0, [["I like", true]])
		rec().emit(0, [["I like", true], ["I like Java", true]])
		rec().emit(0, [["I like", true], ["I like Java", true], ["I like Java and Python", true]])
		assert.deepEqual(log.finals, ["I like", "Java", "and Python"])
	})

	it("stop(): asks the engine to stop, ends as expected, and never restarts", () => {
		const { d, log, rec } = session()
		d.start()
		d.stop()
		assert.equal(rec().stopped, 1)
		rec().emit(0, [["last words", true]]) // engines may deliver the last final after stop()
		rec().end()
		assert.deepEqual(log.finals, ["last words"])
		assert.deepEqual(log.ends, [{ unexpected: false }])
		assert.equal(rec().started, 1)
		assert.equal(FakeRec.instances.length, 1)
		d.stop() // after end: no-op
		assert.equal(rec().stopped, 1)
	})

	it("engine ending on its own is reported as unexpected and keeps unfinished interim speech once", () => {
		const { d, log, rec } = session()
		d.start()
		rec().emit(0, [["I led a team", true]])
		rec().emit(1, [["I led a team", true], ["of four people", false]])
		rec().end()
		assert.deepEqual(log.finals, ["I led a team", "of four people"])
		assert.deepEqual(log.ends, [{ unexpected: true }])
		assert.equal(log.interims.at(-1), "")
		assert.equal(rec().started, 1) // no automatic restart
	})

	it("a growing leftover interim only adds its new words", () => {
		const { d, log, rec } = session()
		d.start()
		rec().emit(0, [["hello", true]])
		rec().emit(0, [["hello", true], ["hello world", false]])
		rec().end()
		assert.deepEqual(log.finals, ["hello", "world"])
	})

	it("network error: reported once, not permanent, explains browser support, no restart", () => {
		const { d, log, rec } = session()
		d.start()
		rec().error("network")
		rec().end()
		assert.deepEqual(log.errors, ["network"])
		assert.deepEqual(log.ends, [{ unexpected: false }])
		assert.equal(PERMANENT_SPEECH_ERRORS.has("network"), false)
		assert.match(describeSpeechError("network"), /could not be reached/)
		assert.match(describeSpeechError("network"), /Brave/)
		assert.equal(rec().started, 1)
	})

	it("no-speech error: friendly retry message, not permanent", () => {
		const { d, log, rec } = session()
		d.start()
		rec().error("no-speech")
		rec().end()
		assert.deepEqual(log.errors, ["no-speech"])
		assert.equal(PERMANENT_SPEECH_ERRORS.has("no-speech"), false)
		assert.match(describeSpeechError("no-speech"), /No speech was heard/)
	})

	it("permission and language errors are permanent for the page", () => {
		for (const code of ["not-allowed", "service-not-allowed", "language-not-supported"]) {
			assert.equal(PERMANENT_SPEECH_ERRORS.has(code), true, code)
			assert.ok(describeSpeechError(code))
		}
		assert.match(describeSpeechError("not-allowed"), /blocked/)
		assert.match(describeSpeechError("language-not-supported"), /type your answer/)
		assert.match(describeSpeechError("audio-capture"), /microphone/)
		assert.match(describeSpeechError("something-new"), /stopped unexpectedly/)
	})

	it("an 'aborted' error is silent", () => {
		const { d, log, rec } = session()
		d.start()
		rec().error("aborted")
		assert.deepEqual(log.errors, [])
		assert.equal(describeSpeechError("aborted"), null)
	})

	it("abort() releases the engine immediately and silences later events", () => {
		const { d, log, rec } = session()
		d.start()
		rec().emit(0, [["partial", false]])
		const engine = rec()
		d.abort()
		assert.equal(engine.aborted, 1)
		assert.equal(engine.onresult, null)
		assert.equal(engine.onend, null)
		// a late event from the engine object must not reach the app
		assert.doesNotThrow(() => engine.end())
		assert.deepEqual(log.finals, [])
		assert.deepEqual(log.ends, [])
		d.abort()
		assert.equal(engine.aborted, 1)
	})

	it("events after the session ended are ignored", () => {
		const { d, log, rec } = session()
		d.start()
		const engine = rec()
		const onresult = engine.onresult
		engine.end()
		onresult({ resultIndex: 0, results: results([["ghost", true]]) })
		engine.onend?.()
		assert.deepEqual(log.finals, [])
		assert.equal(log.ends.length, 1)
	})

	it("a start() failure is reported and leaves nothing running", () => {
		FakeRec.instances = []
		FakeRec.failStart = true
		const log = []
		const d = createDictation(FakeRec, "en-IN", {
			onFinal: () => {},
			onInterim: () => {},
			onError: (c) => log.push(c),
			onEnd: () => log.push("end"),
		})
		assert.equal(d.start(), false)
		assert.deepEqual(log, ["start-failed"])
		FakeRec.failStart = false
	})
})
