(function () {
  "use strict";

  var links = [
    ["home", "Home", "index.html"],
    ["how", "How It Works", "index.html#how"],
    ["services", "Services", "index.html#services"],
    ["vehicles", "Vehicles", "driver-register.html#vehicleCatalogue"],
    ["fare", "Fare Calculator", "fare-calculator.html"],
    ["payment", "Protected Payment", "index.html#protected"],
    ["track", "Track Booking", "track.html"],
    ["partner", "Partner With Us", "driver-register.html#partnerPaths"],
    ["signin", "Sign In", "client.html"]
  ];

  function logo() {
    return '<svg class="logo" width="34" height="34" viewBox="0 0 40 40" fill="none" aria-hidden="true">' +
      '<rect x="2.5" y="2.5" width="35" height="35" rx="10" fill="#004F2D" stroke="#70C247" stroke-width="2"/>' +
      '<path d="M20 8.5 L28.8 19.5 H23 V25.6 H17 V19.5 H11.2 Z" fill="#70C247"/>' +
      '<rect x="11.5" y="28.6" width="17" height="3.3" rx="1.65" fill="#FFFFFF"/>' +
      '</svg><span class="wordmark"><b>Lift</b><b>Haul</b></span>';
  }

  function render(nav, index) {
    var active = nav.getAttribute("data-active") || "";
    var linkMarkup = links.map(function (item) {
      var current = item[0] === active;
      return '<a href="' + item[2] + '"' + (current ? ' class="on" aria-current="page"' : '') + '>' + item[1] + '</a>';
    }).join("");
    var suffix = index ? "-" + index : "";
    nav.className = "snav lh-public-nav";
    nav.setAttribute("aria-label", "Primary navigation");
    nav.innerHTML = '<div class="in">' +
      '<a class="mark" href="index.html" aria-label="LiftHaul home">' + logo() + '</a>' +
      '<div class="links" id="navlinks' + suffix + '">' + linkMarkup + '</div>' +
      '<div class="sp"></div>' +
      '<button class="navtoggle" id="navtoggle' + suffix + '" type="button" aria-label="Open navigation" aria-expanded="false" aria-controls="navlinks' + suffix + '">' +
      '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M3 6h18M3 12h18M3 18h18"/></svg>' +
      '</button></div>';

    var toggle = nav.querySelector(".navtoggle");
    var menu = nav.querySelector(".links");
    toggle.addEventListener("click", function (event) {
      event.stopPropagation();
      var open = menu.classList.toggle("open");
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
      toggle.setAttribute("aria-label", open ? "Close navigation" : "Open navigation");
    });
    menu.addEventListener("click", function () {
      menu.classList.remove("open");
      toggle.setAttribute("aria-expanded", "false");
      toggle.setAttribute("aria-label", "Open navigation");
    });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && menu.classList.contains("open")) {
        menu.classList.remove("open");
        toggle.setAttribute("aria-expanded", "false");
        toggle.focus();
      }
    });
  }

  function mount() {
    Array.prototype.forEach.call(document.querySelectorAll("[data-lh-public-nav]"), render);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount);
  else mount();
}());
