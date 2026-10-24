/* "Privacy details" on the result screen: three plain lines, then the full
   transcript behind "Show technical details" (closed by default). Technical terms
   are allowed only inside that toggle. Every value is rendered with textContent:
   relay-supplied values are untrusted. */
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

  /* How many scrambled values this wallet sent to the partner (the round-1 count). */
  function sentCount(data) {
    var partner = (data.parties || []).filter(function (p) { return p.party === "partner"; })[0];
    if (!partner) return null;
    for (var i = 0; i < partner.items.length; i++) {
      var it = partner.items[i];
      if (/^blinded set from you: count$/.test(it.label) && typeof it.value === "number") return it.value;
    }
    return null;
  }

  function summaryLines(n) {
    var values = n === null ? "scrambled values" : n + " scrambled value" + (n === 1 ? "" : "s");
    return [
      "Your partner saw: " + values + " and your lab-signed count",
      "The server saw: scrambled values and your wallet's ID, never a gene name",
      "Your genes never left this device"
    ];
  }

  function render(container) {
    var card = el("section", "card privacy");
    card.setAttribute("aria-labelledby", "privacy-title");
    var h = el("h2", null, "Privacy details");
    h.id = "privacy-title";
    card.appendChild(h);

    var list = el("ul", "privacy-lines");
    summaryLines(null).forEach(function (t) { list.appendChild(el("li", null, t)); });
    card.appendChild(list);

    var tech = el("details", "tech");
    tech.appendChild(el("summary", null, "Show technical details"));
    var inner = el("div", "privacy-body");
    inner.appendChild(el("h3", null, "What did everyone see?"));
    inner.appendChild(el("p", "small muted",
      "From this wallet's records. Your partner and the relay received blinded ristretto255 " +
      "points (shown as their first 8 hex characters), counts, the round-0 commitments, and " +
      "the lab-signed credential metadata (person ID, count, issue time, signature)."));
    var body = el("div", "parties");
    body.appendChild(el("p", "muted", "Loading…"));
    inner.appendChild(body);
    tech.appendChild(inner);
    card.appendChild(tech);
    container.appendChild(card);

    fetch("/check/seen").then(function (r) {
      if (!r.ok) throw new Error();
      return r.json();
    }).then(function (data) {
      var lines = summaryLines(sentCount(data));
      list.replaceChildren.apply(list, lines.map(function (t) { return el("li", null, t); }));
      body.replaceChildren.apply(body, cards(data));
    }).catch(function () {
      body.replaceChildren(el("p", "notice", "Couldn't load the technical details."));
    });
  }

  window.GeneMatchPrivacy = { render: render, summaryLines: summaryLines };
})();
