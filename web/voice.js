/* Speech in and out, using the browser's built-in Web Speech API.
 *
 * Nothing is uploaded by this file: recognition and synthesis run in the
 * browser. Recognition needs a secure context, so it works on localhost and
 * over HTTPS but not over plain-HTTP LAN -- callers must handle `supported`
 * being false.
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

  class Voice {
    constructor() {
      this.recognition = null;
      this.listening = false;
      this.handlers = {};
      this.preferredVoice = null;
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

    get canListen() {
      return Boolean(Recognition && window.isSecureContext);
    }

    get canSpeak() {
      return Boolean(synth);
    }

    /** Why listening is unavailable, for a message the user can act on. */
    get unavailableReason() {
      if (!Recognition) return "This browser has no speech recognition.";
      if (!window.isSecureContext) {
        return "Voice needs HTTPS or localhost. Open Jarvis on this PC, or serve it over HTTPS.";
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
      if (!this.canListen || this.listening) return;
      this.stopSpeaking();

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
        const messages = {
          "not-allowed": "Microphone permission was denied.",
          "service-not-allowed": "Microphone permission was denied.",
          "no-speech": "I didn't catch that.",
          network: "Speech recognition needs a network connection.",
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

    stop() {
      this.recognition?.stop();
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
