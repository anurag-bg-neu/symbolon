/* Optional counselor UI. Self-contained: app.js calls it only if it is loaded and
   the backend returned an explanation. Remove the <script> tag to disable it. */
(function () {
  "use strict";

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = String(text);
    return n;
  }

  function list(items, ordered) {
    var l = el(ordered ? "ol" : "ul");
    (items || []).forEach(function (t) { l.appendChild(el("li", null, t)); });
    return l;
  }

  function render(explanation, container) {
    if (!explanation || !container) return;
    var box = el("section", "card counselor");
    box.setAttribute("aria-labelledby", "counselor-title");
    var h = el("h2", null, "What this means");
    h.id = "counselor-title";
    box.appendChild(h);
    box.appendChild(el("p", "muted small",
      "Automated explanation from curated facts only. It is not a diagnosis."));
    if (explanation.summary) box.appendChild(el("p", "lead", explanation.summary));
    if (explanation.what_it_means) box.appendChild(el("p", null, explanation.what_it_means));
    if (explanation.next_steps && explanation.next_steps.length) {
      box.appendChild(el("h3", null, "Next steps"));
      box.appendChild(list(explanation.next_steps, false));
    }
    if (explanation.questions && explanation.questions.length) {
      box.appendChild(el("h3", null, "Questions to ask a genetic counselor"));
      box.appendChild(list(explanation.questions, true));
    }
    if (explanation.disclaimer) box.appendChild(el("p", "muted small", explanation.disclaimer));
    container.appendChild(box);
  }

  window.GeneMatchCounselor = { render: render };
})();
