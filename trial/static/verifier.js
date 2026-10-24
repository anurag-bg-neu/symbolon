/* Trial verifier page. Plain JS; all values rendered with textContent. */
(function () {
  "use strict";

  var WALLETS = ["http://localhost:8101", "http://localhost:8102"];
  var POLL_MS = 1000;
  var timer = null;

  function $(id) { return document.getElementById(id); }
  function el(tag, text, cls) {
    var n = document.createElement(tag);
    if (text !== undefined) n.textContent = String(text);
    if (cls) n.className = cls;
    return n;
  }
  function showError(msg) {
    var box = el("div", undefined, "notice");
    box.setAttribute("role", "alert");
    box.appendChild(el("p", msg));
    $("error").replaceChildren(box);
  }

  function getJSON(method, path, body) {
    var opts = { method: method, headers: {} };
    if (body) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
    return fetch(path, opts).then(function (r) {
      return r.json().then(function (d) {
        if (!r.ok) throw new Error((d && d.detail) || "Request failed.");
        return d;
      });
    }, function () { throw new Error("We couldn't reach the trial service."); });
  }

  function loadTrials() {
    getJSON("GET", "/trials").then(function (trials) {
      var box = $("trials");
      box.replaceChildren();
      trials.forEach(function (t, i) {
        var id = "trial-" + i;
        var row = el("div", undefined, "option");
        var input = document.createElement("input");
        input.type = "radio"; input.name = "trial"; input.id = id; input.value = t.trial_id;
        if (i === 0) input.checked = true;
        var label = document.createElement("label");
        label.htmlFor = id;
        label.appendChild(el("strong", t.name));
        label.appendChild(el("span", t.summary, "muted"));
        label.appendChild(el("span", "Requires: " + t.required.gene + ", " + t.required.finding, "small"));
        row.appendChild(input); row.appendChild(label);
        box.appendChild(row);
      });
    }, function (e) { showError(e.message); });
  }

  function stop() { if (timer) window.clearTimeout(timer); timer = null; }

  function render(st) {
    $("status").hidden = false;
    var text = st.status === "pending" ? "Waiting for the patient to approve…"
      : st.status === "eligible" ? "Eligible — verified by lab"
      : "Not verified: " + (st.reason || "the presentation was rejected.");
    $("status-text").textContent = text;
    var dl = $("received");
    dl.replaceChildren();
    var rec = st.received || { claims: 0 };
    [["Claims received", rec.claims === 1 ? "1 claim" : rec.claims + " claims"],
     ["Finding", rec.gene ? rec.gene + ": " + rec.finding : "none yet"],
     ["Anything else", "nothing (no carrier results, no other findings)"]]
      .forEach(function (p) { dl.appendChild(el("dt", p[0])); dl.appendChild(el("dd", p[1])); });
  }

  function pollStatus(id) {
    getJSON("GET", "/requests/" + id).then(function (st) {
      render(st);
      if (st.status === "pending") timer = window.setTimeout(function () { pollStatus(id); }, POLL_MS);
    }, function (e) { stop(); showError(e.message); });
  }

  $("pick").addEventListener("submit", function (ev) {
    ev.preventDefault();
    stop();
    $("error").replaceChildren();
    var chosen = document.querySelector("input[name=trial]:checked");
    if (!chosen) return showError("Choose a trial first.");
    getJSON("POST", "/requests", { trial_id: chosen.value }).then(function (req) {
      $("request").hidden = false;
      $("req-what").textContent = "Asking for: " + req.gene + ", " + req.finding + ".";
      $("req-expiry").textContent = "This request works once and expires at " + req.expires_at + ".";
      $("link-a").href = WALLETS[0] + "/#present=" + req.request_id;
      $("link-b").href = WALLETS[1] + "/#present=" + req.request_id;
      $("req-title").focus();
      pollStatus(req.request_id);
    }, function (e) { showError(e.message); });
  });

  loadTrials();
})();
