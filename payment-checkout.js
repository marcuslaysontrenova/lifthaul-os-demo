(function () {
  "use strict";

  var TERMINAL = ["PAID", "FAILED", "EXPIRED", "CANCELLED", "REFUNDED", "PARTIALLY_REFUNDED", "UNDER_REVIEW"];
  var timers = new WeakMap();

  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"]/g, function (char) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[char];
    });
  }
  function peso(value) {
    return "₱" + Number(value || 0).toLocaleString("en-PH", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  function idem(token, channel) {
    var key = "lifthaul_payment_idem:" + token + ":" + channel;
    try {
      var saved = sessionStorage.getItem(key);
      if (saved) return saved;
      var value = window.crypto && crypto.randomUUID ? crypto.randomUUID() : "pay-" + Date.now() + "-" + Math.random().toString(36).slice(2);
      sessionStorage.setItem(key, value);
      return value;
    } catch (_) {
      return "pay-" + Date.now() + "-" + Math.random().toString(36).slice(2);
    }
  }
  function jsonRequest(url, options) {
    options = options || {};
    options.headers = Object.assign({ Accept: "application/json" }, options.headers || {});
    return fetch(url, options).then(function (response) {
      return response.text().then(function (text) {
        var body = text ? JSON.parse(text) : {};
        if (!response.ok) throw new Error(body.error || body.message || "Payment service request failed.");
        return body && body.data !== undefined ? body.data : body;
      });
    });
  }
  function progress(stage) {
    var current = { accept: 0, checkout: 1, verify: 2, confirmed: 3, review: 2, failed: 2 }[stage] || 0;
    return ["Quotation accepted", "Customer checkout", "Provider verification", "Payment confirmed"].map(function (label, index) {
      return '<li class="' + (index < current ? "done" : index === current ? "current" : "") + '">' + label + "</li>";
    }).join("");
  }
  function shell(element, title, subtitle, state, stage, body) {
    var stateClass = state === "PAID" ? " paid" : state === "UNDER_REVIEW" ? " review" : ["FAILED", "EXPIRED", "CANCELLED"].indexOf(state) >= 0 ? " error" : "";
    element.className = "payment-checkout";
    element.innerHTML =
      '<div class="payment-checkout-header"><div><strong>' + esc(title) + '</strong><small>' + esc(subtitle) + '</small></div><span class="payment-state' + stateClass + '">' + esc(state) + "</span></div>" +
      '<ol class="payment-progress">' + progress(stage) + '</ol><div class="payment-checkout-body">' + body + "</div>";
  }
  function message(element, text, kind) {
    var box = element.querySelector(".payment-message");
    if (!box) return;
    box.className = "payment-message" + (kind ? " " + kind : "");
    box.textContent = text;
  }
  function stop(element) {
    var timer = timers.get(element);
    if (timer) window.clearTimeout(timer);
    timers.delete(element);
  }
  function renderStatus(element, options, payment) {
    var status = String(payment.status || "PENDING").toUpperCase();
    var paid = status === "PAID";
    var stage = paid ? "confirmed" : status === "UNDER_REVIEW" ? "review" : ["FAILED", "EXPIRED", "CANCELLED"].indexOf(status) >= 0 ? "failed" : "verify";
    var body =
      "<p>" + (paid
        ? "The payment provider acknowledged this transaction and LiftHaul verified its booking reference, amount, currency and final status."
        : status === "UNDER_REVIEW"
          ? "Automatic confirmation stopped because the provider record needs investigation. The booking has not been marked paid."
          : "LiftHaul is waiting for the provider and checking the provider API directly. A redirect or screenshot is not confirmation.") + "</p>" +
      '<div class="payment-reference">Reference: ' + esc(payment.reference_number || "Pending provider reference") + "<br>Amount: " + peso(payment.amount || options.amount) + " · PHP<br>Status: " + esc(status) + "</div>" +
      (payment.checkout_url && !paid ? '<div class="payment-checkout-actions"><a class="btn sm" target="_blank" rel="noopener noreferrer" href="' + esc(payment.checkout_url) + '">Open secure checkout →</a><button type="button" class="btn line sm" data-refresh>Check provider status</button></div>' : '<div class="payment-checkout-actions"><button type="button" class="btn line sm" data-refresh>Refresh verified status</button></div>') +
      '<div class="payment-message" aria-live="polite">' + esc(payment.verification_reminder || "") + "</div>";
    shell(element, paid ? "Payment confirmed" : "Payment verification", "Authoritative status comes from the payment provider", status, stage, body);
    var refreshButton = element.querySelector("[data-refresh]");
    if (refreshButton) refreshButton.addEventListener("click", function () { refresh(element, options, true); });
    stop(element);
    if (TERMINAL.indexOf(status) < 0) {
      timers.set(element, window.setTimeout(function () { refresh(element, options, false); }, 8000));
    }
  }
  function refresh(element, options, manual) {
    var button = element.querySelector("[data-refresh]");
    if (button) button.disabled = true;
    jsonRequest(options.base + "/public/bookings/" + encodeURIComponent(options.token) + "/payments/refresh", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
    }).then(function (payment) {
      renderStatus(element, options, payment);
    }).catch(function (error) {
      if (button) button.disabled = false;
      message(element, (manual ? "" : "Automatic status check: ") + error.message, "warn");
      stop(element);
      timers.set(element, window.setTimeout(function () { refresh(element, options, false); }, 12000));
    });
  }
  function createPayment(element, options) {
    var selected = element.querySelector('input[name="certified-payment-channel"]:checked');
    if (!selected) { message(element, "Choose an available certified payment method.", "error"); return; }
    var button = element.querySelector("[data-create-payment]");
    if (button) { button.disabled = true; button.textContent = "Initializing payment…"; }
    var channel = selected.value;
    jsonRequest(options.base + "/public/bookings/" + encodeURIComponent(options.token) + "/payments", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ channel: channel, idempotency_key: idem(options.token, channel) }),
    }).then(function (payment) {
      renderStatus(element, options, payment);
      if (payment.checkout_url) window.open(payment.checkout_url, "_blank", "noopener,noreferrer");
    }).catch(function (error) {
      if (button) { button.disabled = false; button.textContent = "Continue to secure checkout →"; }
      message(element, error.message, "error");
    });
  }
  function loadChannels(element, options) {
    shell(element, "Secure payment", "Only provider-certified methods are displayed", "PENDING", "checkout",
      '<p>Checking the payment environment before showing any method…</p><div class="payment-message" aria-live="polite">Verifying channel certification.</div>');
    jsonRequest(options.base + "/public/payments/channels").then(function (data) {
      var channels = Array.isArray(data.channels) ? data.channels : [];
      if (!channels.length) {
        shell(element, "Online payment not active", "No uncertified method is exposed", "PENDING", "checkout",
          '<p>' + esc(data.reason || "No payment channel has completed activation testing.") + '</p><div class="payment-message warn">Your booking remains awaiting payment. No charge has been attempted.</div>');
        return;
      }
      var cards = channels.map(function (channel, index) {
        return '<label class="payment-channel"><input type="radio" name="certified-payment-channel" value="' + esc(channel.key) + '"' + (index === 0 ? " checked" : "") + '><span>' + esc(channel.label) + "<br><small>" + esc(channel.provider + " · " + channel.kind) + "</small></span></label>";
      }).join("");
      shell(element, "Choose a secure payment method", "Certified for " + (data.environment || "this environment"), "PENDING", "checkout",
        '<p>Your credentials are entered only on the provider-hosted checkout. Processing fees and final instructions are shown before authorization.</p><div class="payment-channel-list">' + cards + '</div><div class="payment-checkout-actions"><button type="button" class="btn sm" data-create-payment>Continue to secure checkout →</button></div><div class="payment-message" aria-live="polite">No method can mark this booking paid without provider verification.</div>');
      element.querySelector("[data-create-payment]").addEventListener("click", function () { createPayment(element, options); });
    }).catch(function (error) {
      shell(element, "Payment service unavailable", "No payment method is exposed", "PENDING", "checkout",
        '<p>The payment readiness check could not be completed.</p><div class="payment-message error">' + esc(error.message) + "</div>");
    });
  }
  function acceptQuote(element, options) {
    var button = element.querySelector("[data-accept-quote]");
    if (button) { button.disabled = true; button.textContent = "Accepting securely…"; }
    jsonRequest(options.base + "/public/bookings/" + encodeURIComponent(options.token) + "/accept-quote", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ idempotency_key: idem(options.token, "quotation") }),
    }).then(function () { loadChannels(element, options); }).catch(function (error) {
      if (button) { button.disabled = false; button.textContent = "Accept quotation & continue →"; }
      message(element, error.message, "error");
    });
  }
  function mount(element, options) {
    if (!element) return;
    stop(element);
    options = options || {};
    if (!options.base) {
      shell(element, "Payment activation required", "No live method is displayed", "PENDING", "accept",
        '<p>This demo is not connected to a hosted payment API. No transaction can be created and no payment can be marked paid.</p>');
      return;
    }
    jsonRequest(options.base + "/public/bookings/" + encodeURIComponent(options.token) + "/payments/status")
      .then(function (payment) {
        if (payment && payment.transaction_id) { renderStatus(element, options, payment); return; }
        if (options.quotationReady) {
          shell(element, "Review final quotation", "Amount " + peso(options.amount) + " · PHP", "PENDING", "accept",
            '<p>Accepting confirms the final amount and protected-payment terms. It does not charge you.</p><div class="payment-checkout-actions"><button type="button" class="btn sm" data-accept-quote>Accept quotation &amp; continue →</button></div><div class="payment-message" aria-live="polite">Payment methods are checked only after acceptance.</div>');
          element.querySelector("[data-accept-quote]").addEventListener("click", function () { acceptQuote(element, options); });
        } else if (options.paymentRequired) {
          loadChannels(element, options);
        } else {
          shell(element, "Payment not yet required", "Your quotation is still being prepared", "PENDING", "accept",
            "<p>LiftHaul will enable payment only after a final positive quotation is ready and accepted.</p>");
        }
      }).catch(function (error) {
        shell(element, "Payment status unavailable", "No status was assumed", "PENDING", "accept",
          '<p>The provider-backed payment status could not be retrieved.</p><div class="payment-message error">' + esc(error.message) + "</div>");
      });
  }

  window.LiftHaulPayment = { mount: mount };
})();
