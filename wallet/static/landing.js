/* Landing page helper: forward wallet deep links (trial requests, lab returns)
   to the signed-in app. The landing page itself works without JavaScript. */
(function () {
  "use strict";
  var h = window.location.hash;
  if (/^#present=[0-9a-f]{32}$/.test(h) || /^#connect(=[0-9a-f]{32}|-declined)$/.test(h)) {
    window.location.replace("/app" + h);
  }
})();
