/* Voice input for the AI prompt box.

   This deliberately does one job: turn speech into text and put it in the
   textbox. It does not interpret anything. The moment there are words, they
   go through exactly the same submit path a typed sentence does, so there is
   one command processor and no chance of the two drifting apart.

   Web Speech API support is uneven -- Chrome and Edge have it, Firefox does
   not, and iOS Safari only in recent versions -- so every entry point checks
   first and the button simply is not rendered where it cannot work. */

(() => {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;

  const form = document.getElementById("ai-prompt-form");
  const input = document.getElementById("ai-prompt-input");
  const micBtn = document.getElementById("ai-mic-btn");
  const panel = document.getElementById("voice-panel");
  if (!form || !input || !micBtn) return;

  // No engine: hide the button rather than offer something that fails on tap.
  if (!SR) {
    micBtn.remove();
    return;
  }

  let recognition = null;
  let listening = false;
  let finalText = "";
  // Set when the user stops on purpose, so onend can tell a deliberate stop
  // from the engine giving up mid-sentence.
  let stoppedByUser = false;
  let silenceTimer = null;

  const SILENCE_MS = 2600;

  function setPanel(state, text) {
    if (!panel) return;
    panel.dataset.state = state || "";
    if (state === "idle") {
      panel.classList.add("hidden");
      panel.innerHTML = "";
      return;
    }
    panel.classList.remove("hidden");

    if (state === "listening") {
      panel.innerHTML = `
        <div class="vp-row">
          <span class="vp-dot" aria-hidden="true"></span>
          <strong>Listening…</strong>
          <button type="button" class="btn btn-sm vp-stop" id="voice-stop">■ Stop</button>
        </div>
        <div class="vp-heard" id="voice-heard">${escapeHtml(text || "")}</div>`;
      document.getElementById("voice-stop")?.addEventListener("click", stop);
    } else if (state === "error") {
      panel.innerHTML = `
        <div class="vp-row vp-error">
          <span aria-hidden="true">⚠️</span>
          <span>${escapeHtml(text)}</span>
          <button type="button" class="btn btn-sm" id="voice-retry">Try again</button>
        </div>`;
      document.getElementById("voice-retry")?.addEventListener("click", start);
    }
  }

  function setMic(on) {
    listening = on;
    micBtn.classList.toggle("is-listening", on);
    micBtn.setAttribute("aria-pressed", String(on));
    micBtn.title = on ? "Stop listening" : "Speak your command";
  }

  /* A pause usually means the sentence is finished. The engine's own
     `continuous` end-of-speech detection is unreliable across browsers, so
     this is what actually closes the session. */
  function armSilenceTimer() {
    clearTimeout(silenceTimer);
    silenceTimer = setTimeout(() => {
      if (listening) stop();
    }, SILENCE_MS);
  }

  function start() {
    if (listening) return;
    finalText = "";
    stoppedByUser = false;

    recognition = new SR();
    recognition.lang = navigator.language || "en-IN";
    recognition.interimResults = true;   // so the professor sees words appear
    recognition.continuous = true;
    recognition.maxAlternatives = 1;

    recognition.onstart = () => {
      setMic(true);
      setPanel("listening", "");
    };

    recognition.onresult = (event) => {
      let interim = "";
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const chunk = event.results[i][0].transcript;
        if (event.results[i].isFinal) finalText += chunk;
        else interim += chunk;
      }
      const shown = (finalText + interim).trim();
      // Live into the textbox: the professor can see it forming and edit it
      // afterwards rather than trusting a black box.
      input.value = shown;
      const heard = document.getElementById("voice-heard");
      if (heard) heard.textContent = shown;
      armSilenceTimer();
    };

    recognition.onerror = (event) => {
      clearTimeout(silenceTimer);
      setMic(false);
      const messages = {
        "not-allowed": "Microphone access is blocked. Allow it for this site in your browser settings.",
        "service-not-allowed": "Microphone access is blocked for this site.",
        "no-speech": "I didn't catch anything. Try again a little closer to the mic.",
        "audio-capture": "No microphone found on this device.",
        network: "Speech recognition needs a connection and couldn't reach the service.",
        aborted: null,   // the user stopped; not an error worth showing
      };
      const msg = messages[event.error];
      if (msg === null) setPanel("idle");
      else setPanel("error", msg || `Speech recognition failed (${event.error}).`);
    };

    recognition.onend = () => {
      clearTimeout(silenceTimer);
      setMic(false);
      const said = input.value.trim();

      // Nothing heard and nobody pressed stop: the engine gave up on its own.
      if (!said) {
        if (!stoppedByUser) setPanel("error", "I didn't catch anything. Try again?");
        else setPanel("idle");
        return;
      }

      setPanel("idle");
      input.focus();

      // Submit only when there is a real sentence to act on. One or two words
      // is usually a misfire, and the professor is better served editing it
      // than watching the assistant guess.
      if (said.split(/\s+/).length >= 3) {
        form.requestSubmit ? form.requestSubmit() : form.dispatchEvent(new Event("submit", { cancelable: true }));
      } else {
        showToast("Heard “" + said + "”. Edit it and press enter.", "success");
      }
    };

    try {
      recognition.start();
    } catch (_) {
      // start() throws if called while already running; treat as a no-op.
    }
  }

  function stop() {
    stoppedByUser = true;
    clearTimeout(silenceTimer);
    try {
      recognition && recognition.stop();
    } catch (_) {}
    setMic(false);
  }

  micBtn.addEventListener("click", (e) => {
    e.preventDefault();
    listening ? stop() : start();
  });

  // Escape cancels, matching every other dismissable thing in the app.
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && listening) stop();
  });
})();
