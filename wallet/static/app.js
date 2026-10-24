/* GeneMatch wallet UI. Plain JS, no framework, no build step.
   Talks only to this wallet's own backend (same origin). Every value from the
   server is rendered with textContent (never parsed as HTML), because
   relay-supplied values in /check/seen are untrusted. */
(function () {
  "use strict";

  var app = document.getElementById("app");
  var live = document.getElementById("live");

  // --- polling policy ---------------------------------------------------------
  var POLL_WAITING_MS = 3000;   // waiting for the partner to join: may take a while
  var POLL_RUNNING_MS = 500;    // the check itself takes a second or two
  var RUNNING_TIMEOUT_MS = 60000;
  var MAX_NET_FAILURES = 3;
  var TERMINAL = { done: 1, failed: 1, declined: 1 };

  var poll = { timer: null, runningSince: null, failures: 0, active: false };

  var MODE_TEXT = {
    count: "You will both learn only how many carrier genes you share, not which ones.",
    detail: "You will both learn which carrier genes you share. Neither of you learns the genes only the other carries."
  };
  var COUNT_DISCLOSURE = "In both modes you will each also see how many carrier genes the other person has in total, but not which ones.";
  var MODE_ON_RESULT = {
    count: "Count mode: you both agreed to learn only the number of shared carrier genes, so no gene names are shown.",
    detail: "Detail mode: you both agreed to learn which carrier genes you share."
  };
  var STEPS = [
    { name: "Seal", text: "Each wallet locks in a fingerprint of its scrambled list, so nobody can change it later." },
    { name: "Reveal", text: "The scrambled lists are swapped and checked against the lab's signature." },
    { name: "Double-lock", text: "Each wallet adds its own lock to the other's list. Matching items now look identical." }
  ];

  // --- helpers ----------------------------------------------------------------

  function el(tag, attrs, children) {
    var n = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) {
        var v = attrs[k];
        if (v === null || v === undefined || v === false) return;
        if (k === "text") n.textContent = String(v);
        else if (k === "class") n.className = v;
        else if (k.slice(0, 2) === "on") n.addEventListener(k.slice(2), v);
        else n.setAttribute(k, v === true ? "" : String(v));
      });
    }
    (children || []).forEach(function (c) {
      if (c === null || c === undefined || c === false) return;
      n.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    });
    return n;
  }

  function announce(msg) {
    live.textContent = "";
    window.setTimeout(function () { live.textContent = msg; }, 50);
  }

  function screen(title, children, opts) {
    var h = el("h1", { id: "screen-title", tabindex: "-1", text: title });
    app.replaceChildren(el("section", { class: "screen" + (opts && opts.wide ? " wide" : "") },
      [h].concat(children)));
    h.focus();
  }

  function button(label, onclick, kind) {
    return el("button", { type: "button", class: "btn " + (kind || "primary"), onclick: onclick, text: label });
  }

  function errorBox(msg) {
    return el("div", { class: "notice", role: "alert" }, [el("p", { text: msg })]);
  }

  function api(method, path, body) {
    var opts = { method: method, headers: {} };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    return fetch(path, opts).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (data) {
        if (!r.ok) {
          var msg = (data && data.error && data.error.user_message) ||
            "Something went wrong, so nothing was shared. Please try again.";
          var err = new Error(msg);
          err.userMessage = msg;
          err.code = data && data.error && data.error.code;
          throw err;
        }
        return data;
      });
    }, function () {
      var err = new Error("offline");
      err.userMessage = "We couldn't reach your wallet. Check that it is still running. Nothing was shared.";
      err.network = true;
      throw err;
    });
  }

  function plural(n, word) { return n + " " + word + (n === 1 ? "" : "s"); }

  // --- polling ----------------------------------------------------------------

  function stopPolling() {
    poll.active = false;
    if (poll.timer) window.clearTimeout(poll.timer);
    poll.timer = null;
  }

  function startPolling() {
    stopPolling();
    poll.active = true;
    poll.failures = 0;
    poll.runningSince = null;
    tick();
  }

  function schedule(ms) {
    if (!poll.active) return;
    poll.timer = window.setTimeout(tick, ms);
  }

  function tick() {
    if (!poll.active) return;
    if (document.hidden) return; // resumed by visibilitychange
    api("GET", "/check/status").then(function (st) {
      poll.failures = 0;
      route(st);
      if (TERMINAL[st.state] || st.state === "idle" || st.state === "awaiting_accept") {
        stopPolling();
        return;
      }
      if (st.state === "running") {
        if (poll.runningSince === null) poll.runningSince = Date.now();
        if (Date.now() - poll.runningSince > RUNNING_TIMEOUT_MS) {
          stopPolling();
          showTimeout();
          return;
        }
        schedule(POLL_RUNNING_MS);
      } else {
        schedule(POLL_WAITING_MS);
      }
    }, function (e) {
      poll.failures += 1;
      if (poll.failures >= MAX_NET_FAILURES) {
        stopPolling();
        showUnreachable(e.userMessage);
        return;
      }
      schedule(POLL_WAITING_MS);
    });
  }

  document.addEventListener("visibilitychange", function () {
    if (!document.hidden && poll.active && !poll.timer) tick();
    if (document.hidden && poll.timer) { window.clearTimeout(poll.timer); poll.timer = null; }
  });

  // --- routing ----------------------------------------------------------------

  var current = { state: null, invite: null, round: null };

  function route(st) {
    var same = st.state === current.state && st.round === current.round;
    if (same && st.state !== "done") return;
    current.state = st.state;
    current.round = st.round;
    switch (st.state) {
      case "waiting_for_partner": return showInvite(st.mode, current.invite);
      case "running": return showRunning(st);
      case "done": return showResult(st);
      case "failed": return showFailed(st.error);
      case "declined": return showDeclined();
      case "awaiting_accept": return; // the join screen already shows the prompt
      default: return showHome();
    }
  }

  // --- screens: home, start, join ---------------------------------------------

  var me = null;

  function showHome() {
    stopPolling();
    current.state = "idle";
    var genes = me ? me.genes : [];
    var claims = me ? me.claims : [];
    screen("Your genetic wallet", [
      el("section", { class: "card", "aria-labelledby": "mine" }, [
        el("h2", { id: "mine", text: "Your carrier results" }),
        el("p", { class: "muted small", text: "Visible only to you. They never leave this wallet." }),
        genes.length
          ? el("ul", { class: "chips" }, genes.map(function (g) { return el("li", { class: "chip", text: g }); }))
          : el("p", { text: "No carrier genes on the tested panel." }),
        el("p", { class: "small", text: plural(genes.length, "carrier gene") + ", certified by the lab." })
      ]),
      el("section", { class: "card", "aria-labelledby": "findings" }, [
        el("h2", { id: "findings", text: "Findings you can share with a trial" }),
        claims.length
          ? el("ul", {}, claims.map(function (c) { return el("li", { text: c.gene + ": " + c.finding }); }))
          : el("p", { class: "muted", text: "None." })
      ]),
      el("div", { class: "actions" }, [
        button("Start a check with your partner", showStart),
        button("Join with an invite code", showJoin, "secondary")
      ])
    ]);
  }

  function modeOption(value, title, checked) {
    var id = "mode-" + value;
    return el("div", { class: "option" }, [
      el("input", { type: "radio", name: "mode", id: id, value: value, checked: checked }),
      el("label", { for: id }, [el("strong", { text: title }), el("span", { class: "muted", text: MODE_TEXT[value] })])
    ]);
  }

  function showStart() {
    var err = el("div");
    var form = el("form", { class: "card", onsubmit: function (ev) {
      ev.preventDefault();
      var mode = form.querySelector("input[name=mode]:checked").value;
      var btn = form.querySelector("button[type=submit]");
      btn.disabled = true;
      api("POST", "/check/start", { mode: mode }).then(function (r) {
        current.invite = r.invite_code;
        current.state = null;
        showInvite(mode, r.invite_code);
        startPolling();
      }, function (e) {
        btn.disabled = false;
        err.replaceChildren(errorBox(e.userMessage));
      });
    } }, [
      el("fieldset", {}, [
        el("legend", { text: "What should you both learn?" }),
        modeOption("count", "Count only", true),
        modeOption("detail", "Which genes", false)
      ]),
      el("p", { class: "small muted", text: COUNT_DISCLOSURE }),
      err,
      el("div", { class: "actions" }, [
        el("button", { type: "submit", class: "btn primary", text: "Create invite" }),
        button("Back", showHome, "secondary")
      ])
    ]);
    screen("Start a check", [form]);
  }

  function showInvite(mode, code) {
    var copyMsg = el("span", { class: "small muted", "aria-live": "polite" });
    screen("Waiting for your partner", [
      el("section", { class: "card" }, [
        el("p", { text: "Share this invite code with your partner. It works once and expires in 24 hours." }),
        code ? el("p", { class: "code", "aria-label": "Invite code " + code.split("").join(" "), text: code })
             : el("p", { class: "muted", text: "Your invite code was shown when you started this check." }),
        code ? el("div", { class: "actions" }, [
          button("Copy code", function () {
            (navigator.clipboard ? navigator.clipboard.writeText(code) : Promise.reject())
              .then(function () { copyMsg.textContent = "Copied."; },
                    function () { copyMsg.textContent = "Copy failed. Select the code instead."; });
          }, "secondary"), copyMsg]) : null,
        el("p", { class: "small", text: MODE_ON_RESULT[mode] || "" }),
        el("p", { class: "small muted", text: "Nothing about you has been sent yet. The check starts on its own when your partner accepts." })
      ]),
      el("div", { class: "actions" }, [button("Back to home", showHome, "secondary")])
    ]);
    announce("Waiting for your partner to join.");
  }

  function showJoin() {
    var err = el("div");
    var input = el("input", { id: "invite", name: "invite", autocomplete: "off", inputmode: "text",
      maxlength: "8", minlength: "8", required: true, spellcheck: "false", class: "code-input",
      "aria-describedby": "invite-help" });
    var form = el("form", { class: "card", onsubmit: function (ev) {
      ev.preventDefault();
      var btn = form.querySelector("button[type=submit]");
      btn.disabled = true;
      api("POST", "/check/join", { invite_code: input.value.trim() }).then(function (r) {
        current.state = "awaiting_accept";
        showConsent(r);
      }, function (e) {
        btn.disabled = false;
        err.replaceChildren(errorBox(e.userMessage));
        input.focus();
      });
    } }, [
      el("label", { for: "invite", text: "Invite code" }),
      input,
      el("p", { id: "invite-help", class: "small muted", text: "8 letters and numbers from your partner." }),
      err,
      el("div", { class: "actions" }, [
        el("button", { type: "submit", class: "btn primary", text: "Continue" }),
        button("Back", showHome, "secondary")
      ])
    ]);
    screen("Join a check", [form]);
  }

  function showConsent(r) {
    var err = el("div");
    var accept = button("Accept and run the check", function () {
      accept.disabled = decline.disabled = true;
      api("POST", "/check/accept").then(function () {
        current.state = null;
        startPolling();
      }, function (e) {
        accept.disabled = decline.disabled = false;
        err.replaceChildren(errorBox(e.userMessage));
      });
    });
    var decline = button("Decline", function () {
      accept.disabled = decline.disabled = true;
      api("POST", "/check/decline").then(showDeclined, function (e) {
        accept.disabled = decline.disabled = false;
        err.replaceChildren(errorBox(e.userMessage));
      });
    }, "secondary");
    screen("Your partner invited you to a check", [
      el("section", { class: "card" }, [
        el("h2", { text: r.mode === "count" ? "Count mode" : "Detail mode" }),
        el("p", { text: r.mode_text }),
        el("p", { class: "small muted", text: "Nothing is sent from your wallet unless you accept." }),
        err,
        el("div", { class: "actions" }, [accept, decline])
      ])
    ]);
  }

  // --- screens: running, result, failure --------------------------------------

  function stepper(round) {
    return el("ol", { class: "steps", "aria-label": "Check progress" }, STEPS.map(function (s, i) {
      var state = round === null || round === undefined ? "todo" : i < round ? "done" : i === round ? "now" : "todo";
      return el("li", { class: "step " + state, "aria-current": state === "now" ? "step" : null }, [
        el("span", { class: "step-name", text: (i + 1) + ". " + s.name }),
        el("span", { class: "step-text small", text: s.text }),
        el("span", { class: "sr-only", text: state === "done" ? " (done)" : state === "now" ? " (in progress)" : "" })
      ]);
    }));
  }

  function showRunning(st) {
    screen("Running the check", [
      el("section", { class: "card" }, [
        el("p", { text: MODE_ON_RESULT[st.mode] || "" }),
        stepper(st.round),
        el("p", { class: "small muted", text: "Your genes stay on this device. Only scrambled values are exchanged." })
      ])
    ]);
    if (st.round !== null && st.round !== undefined) announce("Step " + (st.round + 1) + " of 3: " + STEPS[st.round].name);
  }

  function resultCard(st) {
    var r = st.result;
    var n = r.shared_count;
    var kids = [el("p", { class: "mode", text: MODE_ON_RESULT[r.mode] })];
    if (n === 0) {
      kids.push(el("p", { class: "answer", text: "You don't share a carrier gene on the tested panel." }));
      kids.push(el("p", { text: "This means lower risk for the tested conditions, not zero risk. Conditions that weren't tested aren't covered by this result." }));
      kids.push(el("p", { text: "A genetic counselor can answer questions about untested conditions or your family history." }));
    } else {
      kids.push(el("p", { class: "answer", text: "You share " + plural(n, "carrier gene") + "." }));
      if (r.shared_genes && r.shared_genes.length) {
        kids.push(el("ul", { class: "chips" }, r.shared_genes.map(function (g) { return el("li", { class: "chip", text: g }); })));
      }
      kids.push(el("p", { text: "Being a carrier is common, and carriers are usually healthy. When two partners carry the same gene, each pregnancy has a 25% (1 in 4) chance of a child being affected. This is a chance, not a certainty." }));
      kids.push(el("p", { text: "Options exist for couples in this situation. We recommend talking with a genetic counselor, who can explain them." }));
    }
    kids.push(el("p", { class: "small muted", text: "This is not a diagnosis or medical advice." }));
    return el("section", { class: "card result", "aria-labelledby": "answer-title" },
      [el("h2", { id: "answer-title", text: "Your result" })].concat(kids));
  }

  function showResult(st) {
    var left = el("div", { class: "col" }, [resultCard(st)]);
    // Optional counselor module: rendered only if loaded and an explanation exists.
    if (window.GeneMatchCounselor && st.explanation) {
      try { window.GeneMatchCounselor.render(st.explanation, left); } catch (e) { /* optional */ }
    }
    left.appendChild(el("div", { class: "actions" }, [button("Back to home", showHome, "secondary")]));
    var right = el("div", { class: "col" }, [transparencyPanel()]);
    screen("Check complete", [el("div", { class: "split" }, [left, right])], { wide: true });
    announce("Check complete. " + (st.result.shared_count === 0 ? "No shared carrier genes." : "Shared carrier genes: " + st.result.shared_count + "."));
  }

  function showFailed(error) {
    var msg = (error && error.user_message) || "The check was stopped. Nothing about you was revealed.";
    screen("The check was stopped", [
      el("section", { class: "card" }, [el("p", { text: msg })]),
      el("div", { class: "actions" }, [button("Back to home", showHome)])
    ]);
  }

  function showDeclined() {
    stopPolling();
    current.state = "declined";
    screen("You declined", [
      el("section", { class: "card" }, [el("p", { text: "You declined the check. Nothing was shared." })]),
      el("div", { class: "actions" }, [button("Back to home", showHome)])
    ]);
  }

  function showTimeout() {
    current.state = null;
    screen("This is taking longer than expected", [
      el("section", { class: "card" }, [
        el("p", { text: "We stopped checking for updates. Your partner may have closed their window." }),
        el("p", { class: "small muted", text: "Your wallet only sends scrambled values, and the result is computed here." })
      ]),
      el("div", { class: "actions" }, [button("Check again", startPolling), button("Back to home", showHome, "secondary")])
    ]);
  }

  function showUnreachable(msg) {
    current.state = null;
    screen("Can't reach your wallet", [
      errorBox(msg || "We couldn't reach your wallet. Check that it is still running. Nothing was shared."),
      el("div", { class: "actions" }, [button("Try again", startPolling)])
    ]);
  }

  // --- transparency panel -----------------------------------------------------

  var PARTY = {
    me: { title: "You", sub: "What you received from your partner, plus the result." },
    partner: { title: "Your partner", sub: "Exactly what your wallet sent them." },
    relay: { title: "Relay server", sub: "What passed through the server, as it logged it." },
    agent: { title: "Counselor", sub: "The only input the explanation was built from." },
    trial: { title: "Trial site", sub: "What you shared with a clinical trial." }
  };

  function renderValue(v) {
    if (Array.isArray(v)) {
      if (!v.length) return el("span", { class: "muted", text: "none" });
      return el("span", { class: "hexes" }, v.map(function (x) {
        return el("code", { class: "hex", text: typeof x === "string" ? x : JSON.stringify(x) });
      }));
    }
    if (v !== null && typeof v === "object") {
      return el("code", { class: "obj", text: JSON.stringify(v) });
    }
    if (typeof v === "string" && /^[0-9a-f]{8}$/.test(v)) return el("code", { class: "hex", text: v });
    return el("span", { text: String(v) });
  }

  function transparencyPanel() {
    var body = el("div", { class: "parties" }, [el("p", { class: "muted", text: "Loading…" })]);
    var panel = el("section", { class: "card seen", "aria-labelledby": "seen-title" }, [
      el("h2", { id: "seen-title", text: "What did everyone see?" }),
      el("p", { class: "small muted", text: "From this wallet's records. Long values are shown as their first 8 characters. Nobody, including the server, received your gene list." }),
      body
    ]);
    api("GET", "/check/seen").then(function (data) {
      var cards = (data.parties || []).filter(function (p) {
        return p.items && (p.items.length || p.party === "me" || p.party === "partner" || p.party === "relay");
      }).map(function (p) {
        var meta = PARTY[p.party] || { title: p.party, sub: "" };
        return el("article", { class: "party party-" + p.party }, [
          el("h3", { text: meta.title }),
          el("p", { class: "small muted", text: meta.sub }),
          p.items.length
            ? el("dl", {}, p.items.reduce(function (acc, it) {
                acc.push(el("dt", { text: it.label }));
                acc.push(el("dd", {}, [renderValue(it.value)]));
                return acc;
              }, []))
            : el("p", { class: "muted", text: "Nothing yet." })
        ]);
      });
      body.replaceChildren.apply(body, cards);
    }, function (e) {
      body.replaceChildren(errorBox(e.userMessage));
    });
    return panel;
  }

  // --- trial approval (#present=<request_id>) ---------------------------------

  function presentId() {
    var m = /^#present=([0-9a-f]{32})$/.exec(window.location.hash);
    return m ? m[1] : null;
  }

  function clearHash() {
    if (window.location.hash) history.replaceState(null, "", window.location.pathname);
  }

  function showPresent(id) {
    stopPolling();
    screen("Trial request", [el("p", { class: "muted", text: "Loading the request…" })]);
    api("GET", "/present/pending?request_id=" + encodeURIComponent(id)).then(function (p) {
      var asking = p.trial_name + " is asking to verify your " + p.gene + " " + p.finding + " finding.";
      if (p.status !== "pending") {
        return presentDone("This request is no longer open", p.status === "eligible"
          ? "This request was already answered." : "This request has expired or was already used. Nothing was shared.");
      }
      if (!p.has_matching_claim) {
        return screen("Trial request", [
          el("section", { class: "card" }, [
            el("p", { class: "lead", text: asking }),
            el("p", { text: "Your wallet has no lab-signed " + p.gene + " finding, so there is nothing to share. Nothing was sent." })
          ]),
          el("div", { class: "actions" }, [button("OK", function () { clearHash(); showHome(); })])
        ]);
      }
      var err = el("div");
      var approve = button("Approve and share this finding", function () { answer(true); });
      var decline = button("Decline", function () { answer(false); }, "secondary");
      function answer(ok) {
        approve.disabled = decline.disabled = true;
        api("POST", "/present", { request_id: id, approve: ok }).then(function (r) {
          if (r.status === "eligible") presentDone("Finding verified", "The trial confirmed your finding with the lab's signature. It received exactly one claim and nothing else.");
          else if (r.status === "declined") presentDone("You declined", r.reason || "Nothing was shared.");
          else presentDone("Not verified", r.reason || "The trial could not verify the finding.");
        }, function (e) {
          approve.disabled = decline.disabled = false;
          err.replaceChildren(errorBox(e.userMessage));
        });
      }
      screen("Trial request", [
        el("section", { class: "card" }, [
          el("p", { class: "lead", text: asking }),
          el("h2", { text: "What will be shared" }),
          el("ul", {}, [
            el("li", { text: "This one lab-signed finding: " + p.gene + ", " + p.finding + "." }),
            el("li", { text: "A signature from your wallet proving the finding is yours, for this request only." })
          ]),
          el("h2", { text: "What won't be shared" }),
          el("ul", {}, [
            el("li", { text: "Your carrier results." }),
            el("li", { text: "Any other findings in your wallet." })
          ]),
          el("p", { class: "small muted", text: "If you share this finding with another trial, the two trials could tell it is the same record." }),
          err,
          el("div", { class: "actions" }, [approve, decline])
        ])
      ]);
    }, function (e) {
      screen("Trial request", [errorBox(e.userMessage),
        el("div", { class: "actions" }, [button("Back to home", function () { clearHash(); showHome(); })])]);
    });
  }

  function presentDone(title, msg) {
    screen(title, [
      el("section", { class: "card" }, [el("p", { text: msg })]),
      el("div", { class: "actions" }, [button("Back to home", function () { clearHash(); showHome(); })])
    ]);
  }

  // --- start ------------------------------------------------------------------

  function boot() {
    api("GET", "/me").then(function (m) {
      me = m;
      document.getElementById("who").textContent = "Wallet: " + m.person_id;
      var id = presentId();
      if (id) return showPresent(id);
      return api("GET", "/check/status").then(function (st) {
        if (st.state === "idle") return showHome();
        if (st.state === "awaiting_accept") {
          // Page reloaded after joining: ask for consent again before anything is sent.
          current.state = "awaiting_accept";
          return showConsent({ mode: st.mode, mode_text: MODE_TEXT[st.mode] + " " + COUNT_DISCLOSURE });
        }
        route(st);
        if (!TERMINAL[st.state]) startPolling();
      });
    }, function (e) {
      showUnreachable(e.userMessage);
    });
  }

  window.addEventListener("hashchange", function () {
    var id = presentId();
    if (id) showPresent(id);
  });

  boot();
})();
