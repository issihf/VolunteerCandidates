(function () {
  "use strict";
  var current = null, opener = null;
  function $(id) { return document.getElementById(id); }
  function here() { return location.pathname + location.search; }
  function open(dlg, who, trigger) {
    opener = trigger;
    Array.prototype.forEach.call(dlg.querySelectorAll(".who"), function (s) { s.textContent = who; });
    if (typeof dlg.showModal === "function") dlg.showModal(); else dlg.setAttribute("open", "");
  }
  function setNext(dlg) { var n = dlg.querySelector('input[name="next"]'); if (n) n.value = here(); }

  function statusDialog(b) {
    var d = $("dlg-status"), sel = $("d-status");
    d.querySelector("form").action = "/candidates/" + b.dataset.id + "/status";
    setNext(d);
    Array.prototype.forEach.call(sel.options, function (o) {
      if (o.value === b.dataset.status) { o.hidden = false; o.disabled = false; }
    });
    sel.value = b.dataset.status || sel.options[0].value;
    $("d-fu").value = ""; d.querySelector('input[name="note"]').value = "";
    syncStatus();
    open(d, b.dataset.name, b);
  }
  function syncStatus() {
    var o = $("d-status").selectedOptions[0], d = $("dlg-status");
    var req = o && o.dataset.requires === "1";
    d.querySelector(".req").hidden = !req;
    $("d-fu").required = !!req;
    var hint = $("d-fu-hint");
    if (req) hint.textContent = "";
    else if (o && o.dataset.final === "1") hint.textContent = "סטטוס סיום: בלי מעקב אוטומטי.";
    else if (o && o.dataset.days) hint.textContent = "ריק = מעקב אוטומטי בעוד " + o.dataset.days + " ימים.";
    else hint.textContent = "ריק = בלי מעקב.";
  }
  function contactDialog(b) {
    var d = $("dlg-contact");
    d.querySelector("form").action = "/candidates/" + b.dataset.id + "/contact";
    setNext(d);
    $("d-dir").value = "out"; $("d-text").value = ""; syncContact();
    open(d, b.dataset.name, b);
  }
  function syncContact() {
    var note = $("d-dir").value === "note";
    $("d-ch-wrap").hidden = note; $("d-adv-wrap").hidden = note;
    $("d-ch").disabled = note;
    $("d-text").required = note;
    $("d-text-l").textContent = note ? "תוכן ההערה" : "פרטים (לא חובה)";
  }
  function sendDialog(b) {
    var d = $("dlg-send");
    Array.prototype.forEach.call(d.querySelectorAll("[data-send-link]"), function (a) {
      a.href = "/candidates/" + b.dataset.id + a.dataset.sendLink;
    });
    open(d, b.dataset.name, b);
  }
  function summaryDialog(b) {
    var d = $("dlg-summary"), box = $("d-sum");
    current = b; box.value = "טוען…"; $("d-sum-done").textContent = "";
    open(d, b.dataset.name, b);
    fetch("/candidates/" + b.dataset.id + "/summary.txt", { credentials: "same-origin" })
      .then(function (r) { if (!r.ok) throw new Error(); return r.text(); })
      .then(function (t) { box.value = t; })
      .catch(function () { box.value = "לא הצלחנו להכין את הסיכום."; });
  }
  function copy() {
    var box = $("d-sum");
    function ok() { $("d-sum-done").textContent = "הועתק"; return true; }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(box.value).then(ok, function () { box.select(); document.execCommand("copy"); return ok(); });
    }
    box.select(); document.execCommand("copy"); return Promise.resolve(ok());
  }

  document.addEventListener("click", function (e) {
    var b = e.target.closest("[data-act]");
    if (b) {
      e.preventDefault();
      ({ status: statusDialog, contact: contactDialog, send: sendDialog, summary: summaryDialog })[b.dataset.act](b);
      return;
    }
    var c = e.target.closest("[data-close]");
    if (c) { c.closest("dialog").close(); return; }
    var dlg = e.target.tagName === "DIALOG" ? e.target : null; // click on the backdrop
    if (dlg) {
      var r = dlg.getBoundingClientRect();
      if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) dlg.close();
    }
  });
  document.addEventListener("close", function (e) {
    if (e.target.tagName === "DIALOG" && opener && document.contains(opener)) opener.focus();
  }, true);
  if ($("d-status")) $("d-status").addEventListener("change", syncStatus);
  if ($("d-dir")) $("d-dir").addEventListener("change", syncContact);
  if ($("d-sum-copy")) $("d-sum-copy").addEventListener("click", copy);
  if ($("d-sum-open")) $("d-sum-open").addEventListener("click", function () {
    var base = $("dlg-summary").dataset.paamonim;
    var win = window.open("about:blank", "_blank");  // opened in the click itself so it is not blocked
    copy().then(function () { if (win) { win.opener = null; win.location = base + "/contacts/show/" + current.dataset.show; } });
  });
})();
