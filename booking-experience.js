(function () {
  "use strict";

  var MANIFEST_URL = "data/psgc/manifest.json";
  var DIRECT = "@region-direct";
  var state = {
    manifest: null,
    cache: {},
    loadToken: { o: 0, d: 0 },
    nodes: { o: {}, d: {} },
    points: { o: null, d: null },
    activePin: "o",
    map: null,
    markers: {},
    scopes: {},
    routeLine: null,
    amount: null,
  };

  var REGION_CENTERS = {
    "1300000000": [14.61, 121.02], "1400000000": [17.35, 121.00],
    "0100000000": [17.55, 120.50], "0200000000": [17.30, 121.80],
    "0300000000": [15.48, 120.80], "0400000000": [14.10, 121.25],
    "1700000000": [11.80, 120.90], "0500000000": [13.45, 123.40],
    "0600000000": [10.75, 122.55], "1800000000": [10.10, 123.00],
    "0700000000": [10.30, 123.90], "0800000000": [11.25, 125.00],
    "0900000000": [7.80, 122.60], "1000000000": [8.30, 124.70],
    "1100000000": [7.10, 125.60], "1200000000": [6.30, 124.70],
    "1600000000": [8.90, 125.50], "1900000000": [7.20, 124.10],
  };

  var PAYMENTS = {
    gcash: {
      method: "GCash", provider: "Configured Philippine payment gateway", channel: "E-wallet",
      fee: "Provider-calculated before payment", status: "Selection only — no charge at booking",
      refund: "Eligible refunds return through the activated provider to the original wallet",
    },
    maya: {
      method: "Maya", provider: "Configured Philippine payment gateway", channel: "E-wallet / QRPh",
      fee: "Provider-calculated before payment", status: "Selection only — no charge at booking",
      refund: "Void or refund availability follows the activated provider and transaction state",
    },
    bank_transfer: {
      method: "Online bank transfer", provider: "Gateway-connected participating bank", channel: "InstaPay / PESONet",
      fee: "Bank or provider fee shown before authorization", status: "Selection only — no charge at booking",
      refund: "Cancellation before payment; paid refunds require provider and Finance verification",
    },
    other_wallet: {
      method: "Other supported e-wallet", provider: "Configured multi-channel payment gateway", channel: "E-wallet / QRPh",
      fee: "Provider-calculated before payment", status: "Availability confirmed at hosted checkout",
      refund: "Refund rules are displayed by the activated wallet channel before payment",
    },
    card: {
      method: "Debit or credit card", provider: "PCI-compliant hosted checkout", channel: "Visa / Mastercard / JCB",
      fee: "Provider-calculated before payment", status: "Card details are never entered on this booking form",
      refund: "Eligible refunds return to the original card; timing depends on issuer and provider",
    },
    otc: {
      method: "Over-the-counter", provider: "Gateway-supported retail payment partner", channel: "OTC reference payment",
      fee: "Channel fee and limits shown before reference creation", status: "Channel availability confirmed at checkout",
      refund: "Some cash channels do not support automatic refunds; Finance review may be required",
    },
    operator: {
      method: "Operator-verified bank transfer", provider: "LiftHaul Finance verification", channel: "Bank transfer with proof",
      fee: "Your bank may charge a transfer fee", status: "Available for controlled pilot settlement",
      refund: "Cancellation and refund require governed Finance review and an audit record",
    },
  };

  function byId(id) { return document.getElementById(id); }
  function reducedMotion() { return window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches; }
  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  function setOptions(select, items, placeholder, disabled) {
    select.innerHTML = '<option value="">' + esc(placeholder) + "</option>" + items.map(function (item) {
      return '<option value="' + esc(item.value) + '">' + esc(item.label) + "</option>";
    }).join("");
    select.disabled = !!disabled;
    select.value = "";
  }
  function clearAfter(prefix, level) {
    var order = ["Region", "Province", "City", "Barangay"];
    var start = order.indexOf(level) + 1;
    for (var i = start; i < order.length; i += 1) {
      setOptions(byId(prefix + order[i]), [], "Select " + order[i].toLowerCase() + "…", true);
    }
    var nodeKeys = { Region: "region", Province: "area", City: "locality", Barangay: "barangay" };
    for (var j = start; j < order.length; j += 1) delete state.nodes[prefix][nodeKeys[order[j]]];
  }
  function islandConfig(name) {
    if (!state.manifest) return null;
    return state.manifest.island_groups.find(function (item) { return item.id === name; }) || null;
  }
  async function loadIsland(name) {
    if (state.cache[name]) return state.cache[name];
    var cfg = islandConfig(name);
    if (!cfg) throw new Error("Unknown island group");
    var response = await fetch(cfg.file, { cache: "force-cache" });
    if (!response.ok) throw new Error("Unable to load " + name + " address data");
    var payload = await response.json();
    if (!payload || !Array.isArray(payload.regions)) throw new Error("Invalid geographic data");
    state.cache[name] = payload;
    return payload;
  }
  function findByCode(items, code) {
    return (items || []).find(function (item) { return item.psgc_code === code; }) || null;
  }
  function directArea(region) {
    return {
      psgc_code: DIRECT,
      name: region.psgc_code === "1300000000"
        ? "Metro Manila (province not applicable)"
        : "Independent cities / region-level municipalities",
      kind: "region_direct",
      localities: region.localities || [],
    };
  }
  function regionFor(prefix) { return state.nodes[prefix].region || null; }
  function localityFor(prefix) { return state.nodes[prefix].locality || null; }
  function regionCenter(prefix) {
    var region = regionFor(prefix);
    if (region && REGION_CENTERS[region.psgc_code]) return REGION_CENTERS[region.psgc_code].slice();
    var cfg = islandConfig(byId(prefix + "Island").value);
    return cfg ? cfg.center.slice() : [12.8797, 121.7740];
  }
  function offsetPoint(center, code, scale) {
    var seed = String(code || "0").split("").reduce(function (sum, digit, index) {
      return sum + Number(digit || 0) * (index + 3);
    }, 0);
    var angle = (seed % 360) * Math.PI / 180;
    var distance = ((seed % 7) + 1) / 7 * scale;
    return [center[0] + Math.sin(angle) * distance, center[1] + Math.cos(angle) * distance];
  }
  function setApproximatePoint(prefix, level) {
    var nodes = state.nodes[prefix];
    var point = regionCenter(prefix);
    var code = nodes.region && nodes.region.psgc_code;
    var radius = 90000;
    if (level === "island") {
      var cfg = islandConfig(byId(prefix + "Island").value);
      if (cfg) point = cfg.center.slice();
      radius = 280000;
    } else if (level === "province" && nodes.area && nodes.area.psgc_code !== DIRECT) {
      point = offsetPoint(point, nodes.area.psgc_code, 0.55); radius = 50000;
    } else if ((level === "city" || level === "barangay") && nodes.locality) {
      point = offsetPoint(point, nodes.locality.psgc_code, 0.42); radius = level === "barangay" ? 8000 : 20000;
      if (level === "barangay" && nodes.barangay) point = offsetPoint(point, nodes.barangay.psgc_code, 0.06);
    }
    state.points[prefix] = { lat: point[0], lng: point[1], source: "planning", radius: radius };
    updateMap();
  }
  function setMeta(prefix, message, ready) {
    var meta = byId(prefix + "LocationMeta");
    meta.textContent = message;
    meta.classList.toggle("ready", !!ready);
    var card = byId(prefix + "LocationCard");
    card.setAttribute("data-complete", ready ? "true" : "false");
  }
  function selectedText(select) {
    return select && select.selectedIndex >= 0 ? select.options[select.selectedIndex].text : "";
  }
  function updateProgress(prefix) {
    var island = byId(prefix + "Island").value;
    var region = state.nodes[prefix].region;
    var area = state.nodes[prefix].area;
    var locality = state.nodes[prefix].locality;
    var barangay = state.nodes[prefix].barangay;
    if (!island) return setMeta(prefix, "Start with an island group.", false);
    if (!region) return setMeta(prefix, "Now choose a region in " + island + ".", false);
    if (!area) return setMeta(prefix, "Choose the province or independent area.", false);
    if (!locality) return setMeta(prefix, "Choose the city or municipality.", false);
    if (!barangay) return setMeta(prefix, "Choose the barangay.", false);
    setMeta(prefix, locality.name + " · " + barangay.name, true);
  }
  function notifyChange(prefix) {
    updateProgress(prefix);
    document.dispatchEvent(new CustomEvent("lifthaul:locationchange", { detail: { prefix: prefix, location: getLocation(prefix) } }));
  }

  async function onIsland(prefix) {
    var token = ++state.loadToken[prefix];
    var island = byId(prefix + "Island").value;
    state.nodes[prefix] = {};
    clearAfter(prefix, "Island");
    state.points[prefix] = null;
    updateProgress(prefix); updateMap();
    if (!island) return;
    var regionSelect = byId(prefix + "Region");
    setOptions(regionSelect, [], "Loading regions…", true);
    try {
      var data = await loadIsland(island);
      if (token !== state.loadToken[prefix]) return;
      setOptions(regionSelect, data.regions.map(function (r) { return { value: r.psgc_code, label: r.name }; }), "Select region…", false);
      setApproximatePoint(prefix, "island");
      updateProgress(prefix);
    } catch (error) {
      setOptions(regionSelect, [], "Address data unavailable", true);
      setMeta(prefix, "Could not load the geographic database. Refresh and try again.", false);
      console.error(error);
    }
  }
  function onRegion(prefix) {
    var island = byId(prefix + "Island").value;
    var data = state.cache[island];
    var region = findByCode(data && data.regions, byId(prefix + "Region").value);
    state.nodes[prefix].region = region;
    delete state.nodes[prefix].area; delete state.nodes[prefix].locality; delete state.nodes[prefix].barangay;
    clearAfter(prefix, "Region");
    if (!region) { state.points[prefix] = null; updateProgress(prefix); return updateMap(); }
    var areas = (region.provinces || []).map(function (p) { return { value: p.psgc_code, label: p.name }; });
    if ((region.localities || []).length) areas.push({ value: DIRECT, label: directArea(region).name });
    setOptions(byId(prefix + "Province"), areas, "Select province / area…", false);
    setApproximatePoint(prefix, "region"); notifyChange(prefix);
  }
  function onProvince(prefix) {
    var region = regionFor(prefix);
    var code = byId(prefix + "Province").value;
    var area = code === DIRECT ? directArea(region) : findByCode(region && region.provinces, code);
    state.nodes[prefix].area = area;
    delete state.nodes[prefix].locality; delete state.nodes[prefix].barangay;
    clearAfter(prefix, "Province");
    if (!area) return notifyChange(prefix);
    setOptions(byId(prefix + "City"), (area.localities || []).map(function (l) {
      return { value: l.psgc_code, label: l.name + (l.type ? " · " + l.type : "") };
    }), "Select city / municipality…", false);
    setApproximatePoint(prefix, "province"); notifyChange(prefix);
  }
  function onCity(prefix) {
    var area = state.nodes[prefix].area;
    var locality = findByCode(area && area.localities, byId(prefix + "City").value);
    state.nodes[prefix].locality = locality;
    delete state.nodes[prefix].barangay;
    clearAfter(prefix, "City");
    if (!locality) return notifyChange(prefix);
    setOptions(byId(prefix + "Barangay"), (locality.barangays || []).map(function (b) {
      return { value: b.psgc_code, label: b.name };
    }), "Select barangay…", false);
    setApproximatePoint(prefix, "city"); notifyChange(prefix);
  }
  function onBarangay(prefix) {
    var locality = localityFor(prefix);
    state.nodes[prefix].barangay = findByCode(locality && locality.barangays, byId(prefix + "Barangay").value);
    if (state.nodes[prefix].barangay) setApproximatePoint(prefix, "barangay");
    notifyChange(prefix);
  }
  function bindCascade(prefix) {
    byId(prefix + "Island").addEventListener("change", function () { onIsland(prefix); });
    byId(prefix + "Region").addEventListener("change", function () { onRegion(prefix); });
    byId(prefix + "Province").addEventListener("change", function () { onProvince(prefix); });
    byId(prefix + "City").addEventListener("change", function () { onCity(prefix); });
    byId(prefix + "Barangay").addEventListener("change", function () { onBarangay(prefix); });
    byId(prefix + "Detail").addEventListener("input", function () { notifyChange(prefix); });
  }

  function markerIcon(prefix) {
    var label = prefix === "o" ? "P" : "D";
    var cls = prefix === "o" ? "pin-pickup" : "pin-dropoff";
    return window.L.divIcon({
      className: "route-map-pin",
      html: '<div class="pin-core ' + cls + '"><span>' + label + "</span></div>",
      iconSize: [30, 38], iconAnchor: [15, 34], popupAnchor: [0, -35],
    });
  }
  function scopeColor(prefix) { return prefix === "o" ? "#4e9e20" : "#f2611a"; }
  function pointLabel(prefix) {
    var location = getLocation(prefix);
    var role = prefix === "o" ? "Pickup" : "Drop-off";
    return role + (location.full_address ? ": " + location.full_address : " planning area");
  }
  function updateMapCaption() {
    var caption = byId("mapCaption");
    if (!caption) return;
    var o = state.points.o, d = state.points.d;
    if (o && d) caption.innerHTML = "<b>Route preview ready</b><span>Drag-free planning view · click the map to refine the active pin</span>";
    else if (o || d) caption.innerHTML = "<b>One point selected</b><span>Complete the other location to preview the route</span>";
    else caption.innerHTML = "<b>Philippine route planner</b><span>Select locations to place pickup and drop-off markers</span>";
  }
  function updateMap() {
    updateMapCaption();
    if (!state.map || !window.L) return;
    ["o", "d"].forEach(function (prefix) {
      var point = state.points[prefix];
      if (!point) {
        if (state.markers[prefix]) { state.map.removeLayer(state.markers[prefix]); delete state.markers[prefix]; }
        if (state.scopes[prefix]) { state.map.removeLayer(state.scopes[prefix]); delete state.scopes[prefix]; }
        return;
      }
      var latlng = [point.lat, point.lng];
      if (!state.markers[prefix]) state.markers[prefix] = window.L.marker(latlng, { icon: markerIcon(prefix), keyboard: true }).addTo(state.map);
      else state.markers[prefix].setLatLng(latlng);
      state.markers[prefix].bindPopup(esc(pointLabel(prefix)) + (point.source === "user-pin" ? "<br><small>Pin confirmed on map</small>" : "<br><small>Approximate planning marker</small>"));
      if (state.scopes[prefix]) state.map.removeLayer(state.scopes[prefix]);
      state.scopes[prefix] = window.L.circle(latlng, {
        radius: point.radius || 5000, color: scopeColor(prefix), weight: 2, opacity: .72,
        fillColor: scopeColor(prefix), fillOpacity: .08, dashArray: point.source === "user-pin" ? null : "6 7",
      }).addTo(state.map);
    });
    if (state.routeLine) { state.map.removeLayer(state.routeLine); state.routeLine = null; }
    if (state.points.o && state.points.d) {
      var route = [[state.points.o.lat, state.points.o.lng], [state.points.d.lat, state.points.d.lng]];
      state.routeLine = window.L.polyline(route, { color: "#17250f", weight: 3, opacity: .72, dashArray: "9 9" }).addTo(state.map);
      state.map.fitBounds(route, { padding: [42, 42], maxZoom: 9, animate: !reducedMotion() });
    } else {
      var only = state.points.o || state.points.d;
      if (only) {
        var zoom = only.radius > 200000 ? 6 : only.radius > 60000 ? 7 : 9;
        if (reducedMotion()) state.map.setView([only.lat, only.lng], zoom, { animate: false });
        else state.map.flyTo([only.lat, only.lng], zoom, { duration: .55 });
      }
    }
  }
  function initMap() {
    if (!byId("routeMap") || !window.L) {
      byId("mapCaption").innerHTML = "<b>Map preview unavailable</b><span>The full address hierarchy remains available.</span>";
      return;
    }
    state.map = window.L.map("routeMap", { zoomControl: false, minZoom: 5, maxZoom: 18, scrollWheelZoom: false }).setView([12.8797, 121.7740], 5);
    window.L.control.zoom({ position: "bottomright" }).addTo(state.map);
    window.L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    }).addTo(state.map);
    state.map.on("click", function (event) {
      var prefix = state.activePin;
      if (!regionFor(prefix)) return;
      state.points[prefix] = { lat: event.latlng.lat, lng: event.latlng.lng, source: "user-pin", radius: 1800 };
      updateMap(); notifyChange(prefix);
    });
    window.setTimeout(function () { state.map.invalidateSize(); }, 80);
  }
  function setPinMode(prefix) {
    state.activePin = prefix;
    document.querySelectorAll(".map-mode").forEach(function (button) {
      button.classList.toggle("on", button.getAttribute("data-map-mode") === prefix);
    });
  }

  function getLocation(prefix) {
    var nodes = state.nodes[prefix] || {};
    var detail = byId(prefix + "Detail") ? byId(prefix + "Detail").value.trim() : "";
    var area = nodes.area && nodes.area.psgc_code !== DIRECT ? nodes.area : null;
    var parts = [detail, nodes.barangay && nodes.barangay.name, nodes.locality && nodes.locality.name,
      area && area.name, nodes.region && nodes.region.name].filter(Boolean);
    var point = state.points[prefix];
    return {
      island_group: byId(prefix + "Island") ? byId(prefix + "Island").value : "",
      region_code: nodes.region ? nodes.region.psgc_code : null,
      region_name: nodes.region ? nodes.region.name : null,
      province_code: area ? area.psgc_code : null,
      province_name: area ? area.name : null,
      administrative_area_kind: nodes.area ? (nodes.area.kind || "province") : null,
      locality_code: nodes.locality ? nodes.locality.psgc_code : null,
      locality_name: nodes.locality ? nodes.locality.name : null,
      locality_type: nodes.locality ? nodes.locality.type : null,
      barangay_code: nodes.barangay ? nodes.barangay.psgc_code : null,
      barangay_name: nodes.barangay ? nodes.barangay.name : null,
      address_detail: detail || null,
      full_address: parts.join(", "),
      latitude: point ? Number(point.lat.toFixed(6)) : null,
      longitude: point ? Number(point.lng.toFixed(6)) : null,
      coordinate_source: point ? point.source : null,
    };
  }
  function ready(prefix) {
    var location = getLocation(prefix);
    return !!(location.island_group && location.region_code && location.locality_code && location.barangay_code);
  }

  function paymentChoice() {
    var radio = document.querySelector('input[name="pm"]:checked');
    var code = radio ? radio.value : "operator";
    return Object.assign({ code: code }, PAYMENTS[code] || PAYMENTS.operator);
  }
  function updatePaymentSummary() {
    var choice = paymentChoice();
    var amount = state.amount == null ? "Awaiting vehicle & distance" : "₱" + Number(state.amount).toLocaleString("en-PH", { maximumFractionDigits: 0 });
    var fields = {
      payMethodValue: choice.method, payProviderValue: choice.provider, payAmountValue: amount,
      payFeeValue: choice.fee, payStatusValue: choice.status,
      payReferenceValue: "Assigned only after a payment request is created",
      payRefundValue: choice.refund, payProtectionValue: "Provider-governed settlement; release follows verified delivery and approved controls",
    };
    Object.keys(fields).forEach(function (id) { if (byId(id)) byId(id).textContent = fields[id]; });
    document.querySelectorAll(".payopt").forEach(function (option) {
      var input = option.querySelector('input[name="pm"]');
      option.classList.toggle("on", !!(input && input.checked));
    });
  }
  function updatePaymentAmount(amount) { state.amount = Number.isFinite(Number(amount)) ? Number(amount) : null; updatePaymentSummary(); }

  function reset() {
    ["o", "d"].forEach(function (prefix) {
      state.nodes[prefix] = {}; state.points[prefix] = null; state.loadToken[prefix] += 1;
      clearAfter(prefix, "Island"); updateProgress(prefix);
    });
    state.amount = null; setPinMode("o"); updateMap(); updatePaymentSummary();
  }
  async function init() {
    var status = byId("geoStatus");
    try {
      var response = await fetch(MANIFEST_URL, { cache: "force-cache" });
      if (!response.ok) throw new Error("Unable to load geographic manifest");
      state.manifest = await response.json();
      var islandOptions = state.manifest.island_groups.map(function (item) { return { value: item.id, label: item.id }; });
      ["o", "d"].forEach(function (prefix) {
        setOptions(byId(prefix + "Island"), islandOptions, "Select island group…", false);
        clearAfter(prefix, "Island"); bindCascade(prefix); updateProgress(prefix);
      });
      status.textContent = state.manifest.meta.totals.barangays.toLocaleString("en-PH") + " barangays ready";
      status.classList.remove("error");
    } catch (error) {
      status.textContent = "Location database unavailable"; status.classList.add("error");
      console.error(error);
    }
    document.querySelectorAll(".map-mode").forEach(function (button) {
      button.addEventListener("click", function () { setPinMode(button.getAttribute("data-map-mode")); });
    });
    document.querySelectorAll('input[name="pm"]').forEach(function (radio) { radio.addEventListener("change", updatePaymentSummary); });
    initMap(); setPinMode("o"); updatePaymentSummary();
  }

  window.LiftHaulBookingUX = {
    getLocation: getLocation,
    isLocationReady: ready,
    paymentChoice: paymentChoice,
    updatePaymentAmount: updatePaymentAmount,
    reset: reset,
    state: state,
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
