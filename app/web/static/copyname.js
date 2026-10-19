// Opening WhatsApp also copies the person's name, so it can be pasted as the contact name.
// Any link or button with data-copy-name="..." does it; a .copy-note next to it shows the result.
(function () {
  "use strict";
  function fallback(text) {
    var box = document.createElement("textarea");
    box.value = text; box.setAttribute("readonly", "");
    box.style.position = "fixed"; box.style.opacity = "0";
    document.body.appendChild(box); box.select();
    var ok = false;
    try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
    document.body.removeChild(box);
    return ok;
  }
  function copy(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text).then(function () { return true; }, function () { return fallback(text); });
    }
    return Promise.resolve(fallback(text));
  }
  function note(el) {
    var scope = el.closest("section, dd") || el.parentNode;
    return scope.querySelector(".copy-note");
  }
  document.addEventListener("click", function (e) {
    var el = e.target.closest("[data-copy-name]");
    if (!el) return;
    var name = el.getAttribute("data-copy-name");
    var out = note(el);
    var open = el.getAttribute("data-open");
    if (open) {
      // The server opens WhatsApp in the person's own browser or WhatsApp app: a link followed inside
      // the app window would take over its screen. If that fails, the plain link still works.
      e.preventDefault();
      var body = new URLSearchParams();
      if (el.getAttribute("data-variant")) body.set("v", el.getAttribute("data-variant"));
      fetch(open, { method: "POST", body: body, credentials: "same-origin" }).then(function (r) {
        if (!r.ok) throw new Error("open failed");
      }).catch(function () { window.open(el.href, "_blank"); });
    }
    copy(name).then(function (ok) {
      if (!out) return;
      out.textContent = ok ? "השם הועתק: " + name + ". אפשר להדביק אותו כשם איש הקשר." : "לא הצלחתי להעתיק את השם. אפשר להעתיק ידנית: " + name;
    });
  });
})();
