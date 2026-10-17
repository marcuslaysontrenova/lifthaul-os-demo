(function () {
  "use strict";

  var URL = "https://trenovatech.ai/";
  var SELECTOR = 'a[href^="https://trenovatech.ai"]';

  function prepare(link) {
    link.href = URL;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.classList.add("lh-trenovatech-link");
    link.setAttribute("aria-label", "Visit TrenovaTech.ai (opens in a new tab)");
  }

  function mount() {
    if (!document.getElementById("lh-trenovatech-attribution-style")) {
      var style = document.createElement("style");
      style.id = "lh-trenovatech-attribution-style";
      style.textContent =
        ".lh-trenovatech-attribution{display:flex;justify-content:center;align-items:center;gap:.35rem;padding:14px 20px;background:#0b2015;color:#c8d4cc;font:600 13px/1.4 Arial,sans-serif;text-align:center}" +
        ".lh-trenovatech-link{color:#8ddd5b!important;font-weight:800;text-decoration:underline;text-decoration-thickness:1px;text-underline-offset:3px}" +
        ".lh-trenovatech-link:hover{color:#fff!important}" +
        ".lh-trenovatech-link:focus-visible{outline:3px solid #ffb000;outline-offset:4px;border-radius:2px}";
      document.head.appendChild(style);
    }

    var existing = document.querySelectorAll(SELECTOR);
    if (existing.length) {
      Array.prototype.forEach.call(existing, prepare);
      return;
    }

    var host = document.querySelector("footer");
    if (!host) {
      host = document.createElement("footer");
      host.className = "lh-trenovatech-attribution";
      host.setAttribute("aria-label", "Technology attribution");
      document.body.appendChild(host);
    }

    var wrapper = document.createElement("span");
    wrapper.className = "lh-trenovatech-attribution";
    wrapper.appendChild(document.createTextNode("Developed by "));
    var link = document.createElement("a");
    link.textContent = "TrenovaTech.ai \u2197";
    prepare(link);
    wrapper.appendChild(link);
    host.appendChild(wrapper);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mount);
  } else {
    mount();
  }
}());
