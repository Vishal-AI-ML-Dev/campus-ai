/**
 * Phase 5 voice: framework-free dictation logic (unit-tested with `node --test`,
 * see frontend/tests/unit/). `useSpeech.ts` wraps it in a React hook.
 *
 * Browser speech engines are inconsistent about how they report results:
 * - Desktop Chrome (continuous mode) appends one result per phrase and moves
 *   `resultIndex` forward.
 * - Android Chrome often re-delivers earlier final results, at the same or a
 *   new index, sometimes as a growing string ("I like" -> "I like Java"), and
 *   sometimes the whole session so far in one result.
 * - Some engines end the session without finalising the last phrase.
 *
 * Rules (never rewrite or invent words; only decide what is NEW):
 * - Comparison is word-based and ignores case and surrounding punctuation, but
 *   the student's text is appended exactly as recognised.
 * - Exact re-delivery (same index, same words) and an identical repeat of the
 *   previous final phrase are skipped.
 * - If a final starts with everything already delivered in this session, or
 *   with the previous final phrase, only the extra words are appended.
 * - Anything else is appended whole (we prefer a visible, editable duplicate
 *   over silently losing speech).
 * - Confidence scores are ignored.
 * - Interim text left over when the engine ends unexpectedly is kept (passed
 *   through the same rules), because it is real recognised speech.
 * - Nothing restarts automatically: one click = one recognition session.
 */

/** Session language -> BCP 47 tag passed to the recogniser. Hinglish is usually spoken in Latin script. */
export const SPEECH_LANG: Record<string, string> = { en: "en-IN", hi: "hi-IN", hinglish: "en-IN" }

export function speechLang(language: string): string {
	return SPEECH_LANG[language] ?? "en-IN"
}

const EDGE_PUNCT = /^[\p{P}\p{S}]+|[\p{P}\p{S}]+$/gu

function words(text: string): string[] {
	return text.trim().split(/\s+/).filter(Boolean)
}

function normWord(word: string): string {
	return word.replace(EDGE_PUNCT, "").toLocaleLowerCase()
}

function startsWithWords(full: string[], prefix: string[]): boolean {
	if (!prefix.length || prefix.length >= full.length) return false
	return prefix.every((w, i) => full[i] === w)
}

function sameWords(a: string[], b: string[]): boolean {
	return a.length === b.length && a.every((w, i) => w === b[i])
}

/** Decides which part of each final result is new. One instance per recognition session. */
export class FinalTranscriptMerger {
	private deliveredAt = new Map<number, string>()
	private session: string[] = []
	private last: string[] = []

	/** Returns the text to append (original casing/punctuation), or null if nothing is new. */
	accept(index: number, transcript: string): string | null {
		const raw = words(transcript)
		const norm = raw.map(normWord)
		const kept = norm.filter(Boolean)
		if (!kept.length) return null
		const key = kept.join(" ")
		if (this.deliveredAt.get(index) === key) return null // same result delivered again
		this.deliveredAt.set(index, key)

		// Positions of real words (tokens that are only punctuation don't count for matching).
		const positions = norm.flatMap((w, i) => (w ? [i] : []))
		const normContent = positions.map((i) => norm[i])
		if (sameWords(normContent, this.last)) return null // identical repeat of the previous phrase

		let skip = 0
		if (startsWithWords(normContent, this.session)) skip = this.session.length // whole session re-delivered + more
		else if (startsWithWords(normContent, this.last)) skip = this.last.length // growing phrase
		this.last = normContent
		this.session.push(...normContent.slice(skip))
		// Append the original tokens from the first new word on, unchanged.
		const from = skip === 0 ? 0 : positions[skip]
		return raw.slice(from).join(" ") || null
	}
}

// --- Recognition controller -------------------------------------------------------

type Alternative = { transcript: string }
type Result = { isFinal: boolean; length: number; [i: number]: Alternative }
export type RecognitionResultEvent = { resultIndex: number; results: { length: number; [i: number]: Result } }
export type RecognitionErrorEvent = { error: string }

export type RecognitionLike = {
	lang: string
	continuous: boolean
	interimResults: boolean
	maxAlternatives: number
	onresult: ((e: RecognitionResultEvent) => void) | null
	onerror: ((e: RecognitionErrorEvent) => void) | null
	onend: (() => void) | null
	start: () => void
	stop: () => void
	abort: () => void
}
export type RecognitionCtor = new () => RecognitionLike

/** Errors after which retrying cannot help in this page (the mic button is disabled). */
export const PERMANENT_SPEECH_ERRORS = new Set(["not-allowed", "service-not-allowed", "language-not-supported"])

export function describeSpeechError(code: string): string | null {
	switch (code) {
		case "aborted":
			return null
		case "not-allowed":
		case "service-not-allowed":
			return "Microphone or speech access is blocked. Allow the microphone in your browser's site settings and reload the page, or type your answer."
		case "audio-capture":
			return "No microphone was found or it is in use by another app. Check it and try again, or type your answer."
		case "no-speech":
			return "No speech was heard. Check your microphone, speak a little louder, then press Start speaking again."
		case "network":
			return "Your browser's speech service could not be reached. Check your connection and try again. Some browsers (for example Brave) don't provide speech recognition: use Chrome or Edge, or type your answer."
		case "language-not-supported":
			return "Your browser can't recognise speech in this language. Please type your answer."
		default:
			return "Speech recognition stopped unexpectedly. Try again, or type your answer."
	}
}

export type DictationEnd = {
	/** True when the engine stopped on its own (silence, time limit, network) rather than via stop()/an error. */
	unexpected: boolean
}

export type DictationHandlers = {
	onFinal: (text: string) => void
	onInterim: (text: string) => void
	onError: (code: string) => void
	onEnd: (info: DictationEnd) => void
}

export type Dictation = {
	/** False if the engine refused to start (handlers.onError was called). */
	start: () => boolean
	/** Graceful stop: the engine may still deliver its last final result, then onEnd. */
	stop: () => void
	/** Immediate release (unmount): no further callbacks. */
	abort: () => void
}

/** One recognition session. Never restarts itself. */
export function createDictation(Ctor: RecognitionCtor, lang: string, handlers: DictationHandlers): Dictation {
	const rec = new Ctor()
	const merger = new FinalTranscriptMerger()
	let interim = ""
	let stopping = false
	let failed = false
	let closed = false
	let flushIndex = Number.MAX_SAFE_INTEGER

	rec.lang = lang
	rec.continuous = true
	rec.interimResults = true
	rec.maxAlternatives = 1

	rec.onresult = (e) => {
		if (closed) return
		let pending = ""
		for (let i = e.resultIndex; i < e.results.length; i++) {
			const result = e.results[i]
			const text = result?.[0]?.transcript ?? ""
			if (result?.isFinal) {
				const fresh = merger.accept(i, text)
				if (fresh) handlers.onFinal(fresh)
			} else {
				pending += text
			}
		}
		interim = pending.trim()
		handlers.onInterim(interim)
	}
	rec.onerror = (e) => {
		if (closed) return
		if (e.error === "aborted") return
		failed = true
		handlers.onError(e.error)
	}
	rec.onend = () => {
		if (closed) return
		closed = true
		if (interim) {
			// The engine ended without finalising the last phrase: keep it.
			const fresh = merger.accept(flushIndex--, interim)
			if (fresh) handlers.onFinal(fresh)
			interim = ""
		}
		handlers.onInterim("")
		handlers.onEnd({ unexpected: !stopping && !failed })
	}

	return {
		start() {
			try {
				rec.start()
				return true
			} catch {
				closed = true
				handlers.onError("start-failed")
				return false
			}
		},
		stop() {
			if (closed) return
			stopping = true
			try {
				rec.stop()
			} catch {
				rec.onend?.()
			}
		},
		abort() {
			if (closed) return
			closed = true
			rec.onresult = null
			rec.onerror = null
			rec.onend = null
			try {
				rec.abort()
			} catch {
				// already stopped
			}
		},
	}
}
