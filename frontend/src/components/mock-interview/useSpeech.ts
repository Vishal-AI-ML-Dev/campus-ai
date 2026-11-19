"use client"

/**
 * Phase 5 voice: browser speech only (Web Speech API).
 *
 * - Text-to-speech: `speechSynthesis` reads the interviewer's question aloud.
 * - Speech-to-text: `SpeechRecognition` (prefixed `webkitSpeechRecognition` in
 *   Chrome/Edge/Safari) turns the student's answer into text.
 *
 * Nothing here talks to our backend: only the final, student-reviewed text is
 * submitted, like a typed answer. Some browsers (e.g. Chrome) send microphone
 * audio to their own speech service to recognise it; the UI says so.
 * The microphone is only opened from a click (`listen`), never on load.
 */

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react"
import {
	createDictation,
	describeSpeechError,
	PERMANENT_SPEECH_ERRORS,
	type Dictation,
	type RecognitionCtor,
} from "./speechDictation"

export { describeSpeechError, speechLang, SPEECH_LANG } from "./speechDictation"

function recognitionCtor(): RecognitionCtor | null {
	if (typeof window === "undefined") return null
	const w = window as unknown as { SpeechRecognition?: RecognitionCtor; webkitSpeechRecognition?: RecognitionCtor }
	return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null
}

function hasSynthesis(): boolean {
	return typeof window !== "undefined" && "speechSynthesis" in window && typeof SpeechSynthesisUtterance !== "undefined"
}

const noopSubscribe = () => () => {}

/** Feature detection without a hydration mismatch (false on the server). */
export function useSpeechSupport(): { tts: boolean; stt: boolean } {
	const tts = useSyncExternalStore(noopSubscribe, hasSynthesis, () => false)
	const stt = useSyncExternalStore(noopSubscribe, () => recognitionCtor() !== null, () => false)
	return { tts, stt }
}

/** True once the page has had a click/keypress (browsers block autoplay speech before that). */
export function pageHasUserActivation(): boolean {
	if (typeof navigator === "undefined") return false
	const ua = (navigator as Navigator & { userActivation?: { hasBeenActive: boolean } }).userActivation
	return ua ? ua.hasBeenActive : true
}

export function useSpeechSynthesis() {
	const [speaking, setSpeaking] = useState(false)
	const supported = useSyncExternalStore(noopSubscribe, hasSynthesis, () => false)
	// Only the latest utterance may update `speaking` (a cancelled one can fire
	// its onend after the next one has started).
	const currentRef = useRef<SpeechSynthesisUtterance | null>(null)

	const cancel = useCallback(() => {
		if (!hasSynthesis()) return
		currentRef.current = null
		window.speechSynthesis.cancel()
		setSpeaking(false)
	}, [])

	/** `onStart` fires only if the browser actually starts speaking (not if blocked or cancelled first). */
	const speak = useCallback((text: string, lang: string, onStart?: () => void) => {
		if (!hasSynthesis() || !text.trim()) return
		const synth = window.speechSynthesis
		synth.cancel()
		const utter = new SpeechSynthesisUtterance(text)
		currentRef.current = utter
		utter.lang = lang
		const norm = (tag: string) => tag.replace("_", "-").toLowerCase()
		const voice = synth.getVoices().find((v) => norm(v.lang) === norm(lang))
		if (voice) utter.voice = voice
		utter.onstart = () => {
			if (currentRef.current !== utter) return
			setSpeaking(true)
			onStart?.()
		}
		const done = () => {
			if (currentRef.current !== utter) return
			currentRef.current = null
			setSpeaking(false)
		}
		utter.onend = done
		utter.onerror = done
		synth.speak(utter)
	}, [])

	// Never keep talking after the interview screen goes away.
	useEffect(() => () => {
		if (hasSynthesis()) window.speechSynthesis.cancel()
	}, [])

	return { supported, speaking, speak, cancel }
}

/**
 * Dictation hook (logic in speechDictation.ts). `onFinal` receives each new
 * finalised phrase; `interim` is the in-progress phrase for a live preview.
 * One click = one recognition session; it never restarts on its own.
 */
export function useSpeechRecognition(lang: string, onFinal: (text: string) => void) {
	const [listening, setListening] = useState(false)
	const [interim, setInterim] = useState("")
	const [error, setError] = useState<string | null>(null)
	const [blocked, setBlocked] = useState(false)
	const [stoppedUnexpectedly, setStoppedUnexpectedly] = useState(false)
	const supported = useSyncExternalStore(noopSubscribe, () => recognitionCtor() !== null, () => false)
	const current = useRef<Dictation | null>(null)
	const onFinalRef = useRef(onFinal)

	useEffect(() => {
		onFinalRef.current = onFinal
	}, [onFinal])

	const stop = useCallback(() => {
		current.current?.stop()
	}, [])

	const listen = useCallback(() => {
		const Ctor = recognitionCtor()
		if (!Ctor || current.current || blocked) return
		setError(null)
		setInterim("")
		setStoppedUnexpectedly(false)
		// Every callback checks it still belongs to the live session, so late
		// events from an earlier session can't change the current state.
		const session: Dictation = createDictation(Ctor, lang, {
			onFinal: (text) => {
				if (current.current === session) onFinalRef.current(text)
			},
			onInterim: (text) => {
				if (current.current === session) setInterim(text)
			},
			onError: (code) => {
				if (current.current !== session) return
				setError(describeSpeechError(code))
				if (PERMANENT_SPEECH_ERRORS.has(code)) setBlocked(true)
			},
			onEnd: ({ unexpected }) => {
				if (current.current !== session) return
				current.current = null
				setListening(false)
				setStoppedUnexpectedly(unexpected)
			},
		})
		current.current = session
		if (session.start()) setListening(true)
		else current.current = null
	}, [lang, blocked])

	// Release the microphone when the answer box goes away.
	useEffect(
		() => () => {
			current.current?.abort()
			current.current = null
		},
		[],
	)

	return { supported, listening, interim, error, blocked, stoppedUnexpectedly, listen, stop }
}
