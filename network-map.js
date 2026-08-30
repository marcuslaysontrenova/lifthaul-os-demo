(function () {
  "use strict";

  var shell = document.querySelector(".mapscene");
  if (!shell || !window.L) return;

  var oldGraphic = shell.querySelector("svg");
  if (oldGraphic) oldGraphic.setAttribute("hidden", "hidden");
  var mapNode = document.createElement("div");
  mapNode.id = "networkMap";
  mapNode.className = "network-map";
  mapNode.setAttribute("aria-label", "Interactive Philippine coverage map");
  shell.appendChild(mapNode);

  var controls = document.createElement("div");
  controls.className = "network-map-controls";
  controls.setAttribute("aria-label", "Focus map on an island group");
  controls.innerHTML =
    '<button class="network-map-control on" type="button" data-area="all">Philippines</button>' +
    '<button class="network-map-control" type="button" data-area="luzon">Luzon</button>' +
    '<button class="network-map-control" type="button" data-area="visayas">Visayas</button>' +
    '<button class="network-map-control" type="button" data-area="mindanao">Mindanao</button>';
  shell.appendChild(controls);

  var legend = document.createElement("div");
  legend.className = "network-map-legend";
  legend.innerHTML = "<strong>Nationwide planning view</strong><span>Representative hubs · confirm serviceability when booking</span>";
  shell.appendChild(legend);

  var map = window.L.map(mapNode, {
    zoomControl: false,
    attributionControl: true,
    minZoom: 5,
    maxZoom: 18,
    scrollWheelZoom: false,
  });
  window.L.control.zoom({ position: "topright" }).addTo(map);
  window.L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", {
    maxZoom: 19,
    subdomains: "abcd",
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a> &copy; <a href="https://carto.com/attributions">CARTO</a>',
  }).addTo(map);

  var views = {
    all: [[4.45, 116.7], [21.25, 126.8]],
    luzon: [[12.15, 118.3], [21.25, 123.95]],
    visayas: [[9.0, 121.0], [13.2, 126.2]],
    mindanao: [[4.45, 121.0], [10.55, 126.8]],
  };
  var centers = {
    luzon: [15.15, 121.05],
    visayas: [10.75, 123.9],
    mindanao: [7.55, 124.75],
  };
  var focus = null;

  [
    ["Metro Manila", 14.5995, 120.9842],
    ["Clark", 15.1860, 120.5600],
    ["Batangas", 13.7565, 121.0583],
    ["Cebu", 10.3157, 123.8854],
    ["Iloilo", 10.7202, 122.5621],
    ["Tacloban", 11.2447, 125.0038],
    ["Cagayan de Oro", 8.4542, 124.6319],
    ["Davao", 7.1907, 125.4553],
    ["General Santos", 6.1164, 125.1716],
  ].forEach(function (hub) {
    window.L.marker([hub[1], hub[2]], {
      icon: window.L.divIcon({ className: "network-hub", html: "<span></span>", iconSize: [14, 14], iconAnchor: [7, 7] }),
      keyboard: true,
      title: hub[0],
    }).bindTooltip(hub[0], { className: "network-label", direction: "top", offset: [0, -7] }).addTo(map);
  });

  function setView(name) {
    var reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (focus) { map.removeLayer(focus); focus = null; }
    if (name !== "all") {
      focus = window.L.circle(centers[name], {
        radius: name === "luzon" ? 330000 : 230000,
        color: "#4e9e20",
        weight: 2,
        opacity: .78,
        fillColor: "#5fb92a",
        fillOpacity: .08,
        dashArray: "6 7",
        interactive: false,
      }).addTo(map);
    }
    map.fitBounds(views[name], { padding: [18, 18], animate: !reduced, duration: .35 });
    controls.querySelectorAll("button").forEach(function (button) {
      var selected = button.getAttribute("data-area") === name;
      button.classList.toggle("on", selected);
      button.setAttribute("aria-pressed", selected ? "true" : "false");
    });
  }

  controls.addEventListener("click", function (event) {
    var button = event.target.closest("[data-area]");
    if (button) setView(button.getAttribute("data-area"));
  });
  setView("all");
  window.setTimeout(function () { map.invalidateSize(); setView("all"); }, 80);
})();
