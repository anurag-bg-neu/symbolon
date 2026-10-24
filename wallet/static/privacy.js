/* "Privacy details": what every party received during the check.
   Technical terms are allowed here (and only here). Every value is rendered with
   textContent: relay-supplied values are untrusted. */
(function () {
  "use strict";

  var PARTY = {
    me: { title: "You", sub: "What you received from your partner, plus the result." },
    partner: { title: "Your partner", sub: "Exactly what your wallet sent them." },
    relay: { title: "Relay server", sub: "What passed through the server, as it logged it." },
    agent: { title: "Counselor", sub: "The only input the explanation was built from." },
    trial: { title: "Trial site", sub: "What you shared with a clinical trial." }
  };

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = String(text);
    return n;
  }

  function renderValue(v) {
    if (Array.isArray(v)) {
      if (!v.length) return el("span", "muted", "none");
      var wrap = el("span", "hexes");
      v.forEach(function (x) { wrap.appendChild(el("code", "hex", typeof x === "string" ? x : JSON.stringify(x))); });
      return wrap;
    }
    if (v !== null && typeof v === "object") return el("code", "obj", JSON.stringify(v));
    if (typeof v === "string" && /^[0-9a-f]{8}$/.test(v)) return el("code", "hex", v);
    return el("span", null, String(v));
  }

  function cards(data) {
    return (data.parties || []).filter(function (p) {
      return p.items && (p.items.length || p.party === "me" || p.party === "partner" || p.party === "relay");
    }).map(function (p) {
      var meta = PARTY[p.party] || { title: p.party, sub: "" };
      var art = el("article", "party party-" + p.party);
      art.appendChild(el("h3", null, meta.title));
      art.appendChild(el("p", "small muted", meta.sub));
      if (!p.items.length) {
        art.appendChild(el("p", "muted", "Nothing yet."));
        return art;
      }
      var dl = el("dl");
      p.items.forEach(function (it) {
        dl.appendChild(el("dt", null, it.label));
        var dd = el("dd");
        dd.appendChild(renderValue(it.value));
        dl.appendChild(dd);
      });
      art.appendChild(dl);
      return art;
    });
  }

  /* Builds a collapsed <details> section. Data loads the first time it opens. */
  function render(container) {
    var details = el("details", "card privacy");
    var summary = el("summary", null, "Privacy details");
    details.appendChild(summary);
    var inner = el("div", "privacy-body");
    inner.appendChild(el("h2", null, "What did everyone see?"));
    inner.appendChild(el("p", "small muted",
      "From this wallet's records. Your partner and the relay only received blinded " +
      "ristretto255 points (shown as their first 8 hex characters), counts, the round-0 " +
      "commitments, and the lab-signed credential metadata. Nobody received your gene list."));
    var body = el("div", "parties");
    body.appendChild(el("p", "muted", "Open to load."));
    inner.appendChild(body);
    details.appendChild(inner);
    var loaded = false;
    details.addEventListener("toggle", function () {
      if (!details.open || loaded) return;
      loaded = true;
      body.replaceChildren(el("p", "muted", "Loading…"));
      fetch("/check/seen").then(function (r) {
        if (!r.ok) throw new Error();
        return r.json();
      }).then(function (data) {
        body.replaceChildren.apply(body, cards(data));
      }).catch(function () {
        loaded = false;
        body.replaceChildren(el("p", "notice", "Couldn't load the privacy details. Close and reopen to try again."));
      });
    });
    container.appendChild(details);
  }

  window.GeneMatchPrivacy = { render: render };
})();
