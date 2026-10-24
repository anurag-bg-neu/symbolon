/* Landing page helper: forward trial-request deep links to the signed-in app.
   The landing page itself works without JavaScript. */
(function () {
  "use strict";
  var h = window.location.hash;
  if (/^#present=[0-9a-f]{32}$/.test(h)) {
    window.location.replace("/app" + h);
  }
})();
