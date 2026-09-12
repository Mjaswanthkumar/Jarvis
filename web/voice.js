/* Speech in and out.
 *
 * Two paths for listening:
 *   1. The Web Speech API, where the browser has one that works. Chromium
 *      streams that audio to Google; it is not on-device.
 *   2. MediaRecorder + POST /api/transcribe, used when the first is missing or
 *      fails with a network error -- Brave ships no speech API key, so its
 *      Web Speech API never works. That audio goes to Gemini.
 *
 * Speaking back is always local to the browser. Both paths need a secure
 * context, so callers must handle `canListen` being false.
 */
(() => {
  "use strict";

  const Recognition =
    window.SpeechRecognition || window.webkitSpeechRecognition || null;
  const synth = window.speechSynthesis || null;

  /** Markdown reads badly aloud; strip it down to the words. */
  function speakableText(markdown) {
    return (markdown || "")
      .replace(/```[\s\S]*?```/g, " code block ")
      .replace(/`([^`]+)`/g, "$1")
      .replace(/\*\*([^*]+)\*\*/g, "$1")
      .replace(/^\s*[-*•]\s+/gm, ", ")
      .replace(/^#+\s*/gm, "")
      .replace(/\|/g, " ")
      .replace(/https?:\/\/\S+/g, "a link")
      .replace(/[_~>]/g, "")
      .replace(/\s+/g, " ")
      .trim()
      .slice(0, 700);
  }

  /** Pick a container MediaRecorder supports here and the server accepts. */
  function recorderMimeType() {
    const candidates = [
      "audio/webm;codecs=opus",
      "audio/webm",
      "audio/ogg;codecs=opus",
      "audio/mp4",
    ];
    if (!window.MediaRecorder) return "";
    return candidates.find((type) => MediaRecorder.isTypeSupported(type)) || "";
  }

  class Voice {
    constructor() {
      this.recognition = null;
      this.listening = false;
      this.handlers = {};
      this.preferredVoice = null;
      //: Set by the app: uploads a recording and resolves to a transcript.
      this.transcriber = null;
      this.recorder = null;
      this.mode = "native";
      if (synth) {
        const pick = () => {
          const voices = synth.getVoices();
          this.preferredVoice =
            voices.find((v) => v.localService && /en[-_]/i.test(v.lang)) ||
            voices.find((v) => /en[-_]/i.test(v.lang)) ||
            voices[0] ||
            null;
        };
        pick();
        synth.addEventListener?.("voiceschanged", pick);
      }
    }

    get canRecord() {
      return Boolean(
        window.isSecureContext &&
          navigator.mediaDevices?.getUserMedia &&
          window.MediaRecorder &&
          recorderMimeType()
      );
    }

    get canListen() {
      if (!window.isSecureContext) return false;
      return Boolean(Recognition) || (this.canRecord && this.transcriber);
    }

    get canSpeak() {
      return Boolean(synth);
    }

    /** Why listening is unavailable, for a message the user can act on. */
    get unavailableReason() {
      if (!window.isSecureContext) {
        return "Voice needs HTTPS or localhost. Open Jarvis on this PC, or serve it over HTTPS.";
      }
      if (!Recognition && !this.canRecord) {
        return "This browser cannot capture audio.";
      }
      return "";
    }

    on(event, handler) {
      this.handlers[event] = handler;
      return this;
    }

    _emit(event, ...args) {
      this.handlers[event]?.(...args);
    }

    start() {
      if (this.listening) return;
      this.stopSpeaking();
      if (!Recognition || this.mode === "recorder") return this._startRecording();
      this._startRecognition();
    }

    _startRecognition() {
      const recognition = new Recognition();
      recognition.lang = navigator.language || "en-US";
      recognition.interimResults = true;
      recognition.continuous = false;
      recognition.maxAlternatives = 1;

      let finalText = "";

      recognition.onstart = () => {
        this.listening = true;
        this._emit("start");
      };

      recognition.onresult = (event) => {
        let interim = "";
        for (let i = event.resultIndex; i < event.results.length; i += 1) {
          const result = event.results[i];
          if (result.isFinal) finalText += result[0].transcript;
          else interim += result[0].transcript;
        }
        this._emit("partial", (finalText + interim).trim());
      };

      recognition.onerror = (event) => {
        // Brave (and other builds without a speech API key) always fail with
        // "network" here. Switch to server-side transcription for good.
        if (event.error === "network" && this.canRecord && this.transcriber) {
          this.mode = "recorder";
          recognition.onend = null;
          this.listening = false;
          this.recognition = null;
          this._emit("fallback");
          this._startRecording();
          return;
        }
        const messages = {
          "not-allowed": "Microphone permission was denied.",
          "service-not-allowed": "Microphone permission was denied.",
          "no-speech": "I didn't catch that.",
          network: "Speech recognition is unavailable in this browser.",
        };
        this._emit("error", messages[event.error] || `Voice error: ${event.error}`);
      };

      recognition.onend = () => {
        this.listening = false;
        this.recognition = null;
        this._emit("end", finalText.trim());
      };

      this.recognition = recognition;
      try {
        recognition.start();
      } catch {
        this.listening = false;
        this._emit("error", "Could not start the microphone.");
      }
    }

    /** Record locally, then hand the audio to the server to transcribe. */
    async _startRecording() {
      if (!this.canRecord || !this.transcriber) {
        return this._emit("error", this.unavailableReason || "Voice is unavailable.");
      }

      let stream;
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      } catch {
        return this._emit("error", "Microphone permission was denied.");
      }

      const mimeType = recorderMimeType();
      const recorder = new MediaRecorder(stream, { mimeType });
      const chunks = [];

      recorder.ondataavailable = (event) => {
        if (event.data.size) chunks.push(event.data);
      };

      recorder.onstop = async () => {
        stream.getTracks().forEach((track) => track.stop());
        this.listening = false;
        this.recorder = null;

        const blob = new Blob(chunks, { type: mimeType.split(";")[0] });
        if (blob.size < 1200) return this._emit("end", "");

        this._emit("transcribing");
        try {
          const text = (await this.transcriber(blob)) || "";
          this._emit("end", text.trim());
        } catch (error) {
          this._emit("error", error.message || "Could not transcribe that.");
        }
      };

      this.recorder = recorder;
      this.listening = true;
      recorder.start();
      this._emit("start");
    }

    stop() {
      this.recognition?.stop();
      if (this.recorder?.state === "recording") this.recorder.stop();
    }

    toggle() {
      this.listening ? this.stop() : this.start();
    }

    /** Speak `text`, then call `onDone` -- used to re-open the mic hands-free. */
    speak(text, onDone) {
      if (!this.canSpeak) return onDone?.();
      const spoken = speakableText(text);
      if (!spoken) return onDone?.();

      synth.cancel();
      const utterance = new SpeechSynthesisUtterance(spoken);
      if (this.preferredVoice) utterance.voice = this.preferredVoice;
      utterance.rate = 1.05;
      utterance.pitch = 1;
      utterance.onend = () => onDone?.();
      utterance.onerror = () => onDone?.();
      synth.speak(utterance);
    }

    stopSpeaking() {
      synth?.cancel();
    }
  }

  window.JarvisVoice = new Voice();
  window.JarvisVoice.speakableText = speakableText;
})();
