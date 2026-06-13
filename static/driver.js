/**
 * ControIDE Phase-0 driver page script.
 *
 * Connects to the dashboard SSE stream, listens for named 'question' and
 * 'cleared' events, renders clickable tiles, and POSTs answers back to
 * /api/answer.
 *
 * No framework, no build step. Plain ES2020.
 */

(function () {
  "use strict";

  // ── DOM refs ─────────────────────────────────────────────────────────────

  const statusDot        = document.getElementById("status-dot");
  const statusText       = document.getElementById("status-text");
  const placeholder      = document.getElementById("placeholder");
  const questionCard     = document.getElementById("question-card");
  const hookTypeBadge    = document.getElementById("hook-type-badge");
  const promptText       = document.getElementById("prompt-text");
  const optionsGrid      = document.getElementById("options-grid");
  const customArea       = document.getElementById("custom-area");
  const customInput      = document.getElementById("custom-input");
  const customSubmit     = document.getElementById("custom-submit");
  const waitingIndicator = document.getElementById("waiting-indicator");
  const toastContainer   = document.getElementById("toast-container");

  // ── State ────────────────────────────────────────────────────────────────

  let currentQuestion = null;   // { id, hook_type, prompt, options }
  let submitting = false;       // guard against double-submission

  // ── Helpers ──────────────────────────────────────────────────────────────

  function setStatus(state, text) {
    statusDot.className = state;
    statusText.textContent = text;
  }

  function showQuestion(data) {
    currentQuestion = data;
    submitting = false;

    // Badge
    hookTypeBadge.textContent = data.hook_type === "stop" ? "Stop Hook" : "Pre-Tool Use";
    hookTypeBadge.className = data.hook_type === "stop" ? "stop" : "pretooluse";

    // Prompt
    promptText.textContent = data.prompt || "(no prompt)";

    // Build tiles
    optionsGrid.innerHTML = "";
    customArea.classList.remove("visible");
    customInput.value = "";

    data.options.forEach(function (opt, idx) {
      const btn = document.createElement("button");
      btn.className = "option-tile";
      btn.type = "button";
      btn.dataset.choiceId = opt.id;

      // Colour variant hint
      const variantMap = {
        allow: "variant-allow",
        continue: "variant-continue",
        deny: "variant-deny",
        stop: "variant-stop",
      };
      if (variantMap[opt.id]) {
        btn.classList.add(variantMap[opt.id]);
      }

      // Keyboard shortcut hint
      const keyHint = document.createElement("span");
      keyHint.className = "key-hint";
      keyHint.textContent = String(idx + 1);
      btn.appendChild(keyHint);

      btn.appendChild(document.createTextNode(opt.label));
      btn.setAttribute("aria-label", opt.label);

      btn.addEventListener("click", function () {
        handleTileClick(opt);
      });

      optionsGrid.appendChild(btn);
    });

    // Show card
    placeholder.style.display = "none";
    questionCard.classList.add("visible");
    waitingIndicator.classList.remove("visible");

    // Focus first tile for keyboard nav
    const firstTile = optionsGrid.querySelector(".option-tile");
    if (firstTile) firstTile.focus();
  }

  function clearQuestion() {
    currentQuestion = null;
    submitting = false;
    questionCard.classList.remove("visible");
    customArea.classList.remove("visible");
    waitingIndicator.classList.remove("visible");
    placeholder.style.display = "";
  }

  function handleTileClick(opt) {
    if (submitting || !currentQuestion) return;

    if (opt.id === "custom") {
      customArea.classList.add("visible");
      customInput.focus();
      return;
    }

    submitAnswer(currentQuestion.id, opt.id, null);
  }

  function submitAnswer(questionId, choiceId, customText) {
    if (submitting) return;
    submitting = true;

    waitingIndicator.classList.add("visible");

    const payload = JSON.stringify({
      id: questionId,
      choice_id: choiceId,
      custom_text: customText || null,
    });

    fetch("/api/answer", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: payload,
    })
      .then(function (resp) {
        if (!resp.ok) {
          return resp.json().then(function (err) {
            throw new Error(err.error || ("HTTP " + resp.status));
          });
        }
        return resp.json();
      })
      .then(function () {
        showToast("Answer sent: " + choiceId);
        // clearQuestion is triggered by the 'cleared' SSE event, not here,
        // so the UI stays consistent even if the SSE arrives slightly later.
      })
      .catch(function (err) {
        submitting = false;
        waitingIndicator.classList.remove("visible");
        showToast("Error: " + err.message, true);
      });
  }

  // ── Custom text submit ───────────────────────────────────────────────────

  customSubmit.addEventListener("click", function () {
    if (!currentQuestion) return;
    const text = customInput.value.trim();
    if (!text) {
      customInput.focus();
      return;
    }
    submitAnswer(currentQuestion.id, "custom", text);
  });

  customInput.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      customSubmit.click();
    }
  });

  // ── Keyboard shortcuts (1-4 for tile selection) ──────────────────────────

  document.addEventListener("keydown", function (e) {
    if (!currentQuestion || customArea.classList.contains("visible")) return;
    const num = parseInt(e.key, 10);
    if (isNaN(num) || num < 1) return;
    const tiles = optionsGrid.querySelectorAll(".option-tile");
    const tile = tiles[num - 1];
    if (tile) tile.click();
  });

  // ── Toast notifications ──────────────────────────────────────────────────

  function showToast(msg, isError) {
    const el = document.createElement("div");
    el.className = "toast" + (isError ? " error" : "");
    el.textContent = msg;
    toastContainer.appendChild(el);
    setTimeout(function () {
      el.remove();
    }, 3000);
  }

  // ── SSE connection ───────────────────────────────────────────────────────

  let evtSource = null;
  let reconnectDelay = 1000;

  function connect() {
    setStatus("connecting", "Connecting…");

    evtSource = new EventSource("/events");

    evtSource.onopen = function () {
      setStatus("connected", "Connected");
      reconnectDelay = 1000;
    };

    evtSource.onerror = function () {
      setStatus("disconnected", "Disconnected — reconnecting…");
      evtSource.close();
      setTimeout(connect, reconnectDelay);
      reconnectDelay = Math.min(reconnectDelay * 2, 30000);
    };

    // Named event: a new question arrived
    evtSource.addEventListener("question", function (e) {
      let data;
      try {
        data = JSON.parse(e.data);
      } catch (err) {
        console.error("driver: failed to parse question event", err);
        return;
      }
      showQuestion(data);
    });

    // Named event: the question was answered (may come from another tab)
    evtSource.addEventListener("cleared", function (e) {
      let data;
      try {
        data = JSON.parse(e.data);
      } catch (err) {
        return;
      }
      if (currentQuestion && currentQuestion.id === data.id) {
        clearQuestion();
      }
    });

    // Unnamed data events (existing dashboard state broadcasts) — ignored
    // on this page but must not throw.
    evtSource.onmessage = function () {};
  }

  connect();

})();
