/* Sign-in page. Values from the server are rendered with textContent only. */
(function () {
  "use strict";

  var err = document.getElementById("error");

  function next() {
    // Keep a pending trial request or lab return across sign-in.
    var h = window.location.hash;
    var ok = /^#present=[0-9a-f]{32}$/.test(h);
    return "/app" + (ok ? h : "");
  }

  function show(msg) {
    var box = document.createElement("div");
    box.className = "notice";
    box.setAttribute("role", "alert");
    var p = document.createElement("p");
    p.textContent = msg;
    box.appendChild(p);
    err.replaceChildren(box);
  }

  function post(path, body) {
    return fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {})
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        if (!r.ok) throw new Error((d.error && d.error.user_message) || "Sign-in didn't work. Please try again.");
        return d;
      });
    }, function () { throw new Error("We couldn't reach your wallet. Check that it is running."); });
  }

  fetch("/auth/session").then(function (r) { return r.json(); }).then(function (s) {
    if (s.signed_in) { window.location.replace(next()); return; }
    document.getElementById("owner").textContent = s.display_name + "'s";
    document.getElementById("demo").textContent = "Continue as " + s.display_name;
    document.getElementById("demo-creds").textContent = s.demo_email + " / " + s.demo_password;
    document.getElementById("email").value = "";
  }).catch(function () { show("We couldn't reach your wallet. Check that it is running."); });

  document.getElementById("signin").addEventListener("submit", function (ev) {
    ev.preventDefault();
    var email = document.getElementById("email");
    var pw = document.getElementById("password");
    if (!email.value || !pw.value) { show("Enter your email and password."); return; }
    post("/auth/signin", { email: email.value, password: pw.value })
      .then(function () { window.location.replace(next()); },
            function (e) { show(e.message); pw.value = ""; pw.focus(); });
  });

  document.getElementById("demo").addEventListener("click", function () {
    post("/auth/demo").then(function () { window.location.replace(next()); },
                            function (e) { show(e.message); });
  });

  document.getElementById("screen-title").focus();
})();
