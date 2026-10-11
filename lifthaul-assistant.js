/* LiftHaul assistant. No credentials, contact data or conversations in browser storage. */
(function () {
  'use strict';
  if (document.getElementById('lh-assistant')) return;
  var draft = {}, busy = false, lastFocus;
  var root = document.createElement('aside');
  root.id = 'lh-assistant';
  root.innerHTML = '<button type="button" class="lha-launch" aria-controls="lha-panel" aria-expanded="false">Ask LiftHaul</button>' +
    '<section id="lha-panel" class="lha-panel" role="dialog" aria-modal="false" aria-labelledby="lha-title" hidden>' +
    '<header><div><strong id="lha-title">LiftHaul Assistant</strong><small id="lha-mode">Guided assistant</small></div><button type="button" id="lha-close" aria-label="Close assistant">×</button></header>' +
    '<div class="lha-scroll"><p class="lha-note">Prepare a shipment or check your booking. Keep passwords, payment details and contact numbers out of chat.</p>' +
    '<div id="lha-messages" role="log" aria-live="polite" aria-relevant="additions"></div>' +
    '<div class="lha-shortcuts"><button data-intent="booking">Prepare shipment</button><button data-intent="pricing">Get estimate</button><button data-intent="tracking">Track booking</button><button data-intent="provider">Join as trucker</button><button data-intent="payments">Payments</button><button data-intent="support">Contact support</button></div>' +
    '<form id="lha-shipment" hidden><h3>Shipment details</h3><p>Confirm any details extracted from your message. Dimensions are per package in centimetres.</p><div class="lha-grid" id="lha-fields"></div><div class="lha-form-actions"><button type="submit">Suggest vehicles</button><button type="button" id="lha-price">Get estimate</button></div></form>' +
    '<form id="lha-tracking" hidden><label>Private tracking token<input name="tracking_token" maxlength="128" autocomplete="off" placeholder="pbk_…" required></label><button type="submit">Check status</button></form>' +
    '</div><form id="lha-chat"><label class="lha-sr" for="lha-message">Message</label><input id="lha-message" maxlength="1500" placeholder="Ask in English or Filipino…" autocomplete="off" required><button type="submit">Send</button></form>' +
    '<footer><button type="button" id="lha-reset">Clear conversation</button><span>No booking until you review and submit.</span></footer></section>';
  document.body.appendChild(root);
  function el(id) { return document.getElementById(id); }
  function node(tag, text, className) { var n = document.createElement(tag); n.textContent = text; if (className) n.className = className; return n; }
  var fields = [['cargo','Cargo description'],['weight_kg','Total weight (kg)'],['package_count','Package count'],['package_length_cm','Length (cm)'],['package_width_cm','Width (cm)'],['package_height_cm','Height (cm)'],['origin_island','Pickup island'],['origin_city','Pickup city / municipality'],['dest_island','Delivery island'],['dest_city','Delivery city / municipality'],['km','Route distance (km, for estimate)']];
  fields.forEach(function (entry) {
    var key = entry[0], label = node('label',entry[1]), input;
    if (key.indexOf('island') >= 0) {
      input = document.createElement('select');
      ['', 'Luzon', 'Visayas', 'Mindanao'].forEach(function (v) { var o = node('option',v || 'Select island'); o.value = v; input.appendChild(o); });
    } else {
      input = document.createElement('input');
      input.type = /kg|count|cm|^km$/.test(key) ? 'number' : 'text';
      if (input.type === 'number') { input.min = key === 'package_count' ? '1' : '0.1'; input.step = key === 'package_count' ? '1' : '0.1'; }
      else input.maxLength = 400;
    }
    input.name = key; label.appendChild(input); el('lha-fields').appendChild(label);
  });
  function syncFields() { fields.forEach(function (f) { el('lha-shipment').elements[f[0]].value = draft[f[0]] == null ? '' : draft[f[0]]; }); }
  function readFields() {
    draft = {};
    fields.forEach(function (f) { var v = el('lha-shipment').elements[f[0]].value.trim(); if (v) draft[f[0]] = v; });
  }
  function bubble(text, who) { var n = node('div',text,'lha-bubble ' + (who || 'assistant')); el('lha-messages').appendChild(n); while (el('lha-messages').children.length > 30) el('lha-messages').firstChild.remove(); n.scrollIntoView({block:'nearest'}); return n; }
  var safeLinks = ['book.html','track.html','provider.html','driver-register.html','policies.html','support.html'];
  function render(data) {
    draft = data.shipment || {}; syncFields();
    el('lha-mode').textContent = data.mode === 'ai' ? 'AI assisted · LiftHaul data' : 'Guided assistant';
    el('lha-shipment').hidden = !/^(booking|pricing)$/.test(data.intent);
    el('lha-tracking').hidden = data.intent !== 'tracking';
    var b = bubble(data.reply);
    if (data.recommendation) {
      data.recommendation.options.forEach(function (v) { b.appendChild(node('p',(v.name || v.label || v.id) + ' · ' + (v.vehicle_count || 1) + ' vehicle(s)')); });
    }
    if (data.estimate) {
      var quote = data.estimate.quote_breakdown || {}, amount = quote.customer_total;
      if (amount == null) amount = data.estimate.estimate;
      b.appendChild(node('p',amount == null ? 'Manual quotation required.' : 'Indicative total: ₱' + Number(amount).toLocaleString('en-PH')));
      b.appendChild(node('small','Review the full fee breakdown and exclusions in the booking form.'));
    }
    (data.actions || []).forEach(function (action) {
      if (safeLinks.indexOf(action.href) < 0) return;
      var a = node('a',action.label); a.href = action.href;
      if (action.href === 'book.html') a.addEventListener('click',function (event) {
        try { sessionStorage.setItem('lifthaul_assistant_draft',JSON.stringify({expires:Date.now()+15*60*1000,shipment:draft})); }
        catch (_) { event.preventDefault(); bubble('Your browser could not transfer the draft. Open the booking page and enter the shipment details manually.'); }
      });
      b.appendChild(a);
    });
    if ((data.sources || []).length) b.appendChild(node('small','Source: ' + data.sources.join(' · ')));
  }
  function send(payload) {
    if (busy) return;
    busy = true; root.setAttribute('aria-busy','true');
    root.querySelectorAll('button').forEach(function (b) { if (b.id !== 'lha-close' && !b.classList.contains('lha-launch')) b.disabled = true; });
    var base = window.RGO_CONFIG && window.RGO_CONFIG.apiBase;
    if (!base) base = location.origin;
    if (!el('lha-shipment').hidden) readFields();
    payload.shipment = draft;
    var abort = new AbortController(), timer = setTimeout(function () { abort.abort(); },12000);
    fetch(base.replace(/\/+$/,'') + '/public/assistant/message',{
      method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload),signal:abort.signal
    }).then(function (r) { return r.json().then(function (j) { if (!r.ok || j.error || !j.data) throw new Error(j.error || 'Assistant unavailable'); return j.data; }); })
      .then(render).catch(function (e) { bubble(e.name === 'AbortError' ? 'The assistant took too long to respond. Your draft is still here; try again.' : 'Could not complete this request: ' + e.message); })
      .finally(function () { clearTimeout(timer); busy = false; root.removeAttribute('aria-busy'); root.querySelectorAll('button').forEach(function (b) { b.disabled = false; }); });
  }
  function open() { lastFocus = document.activeElement; el('lha-panel').hidden = false; root.querySelector('.lha-launch').setAttribute('aria-expanded','true'); el('lha-message').focus(); }
  function close() { el('lha-panel').hidden = true; root.querySelector('.lha-launch').setAttribute('aria-expanded','false'); if (lastFocus) lastFocus.focus(); }
  root.querySelector('.lha-launch').onclick = function () { el('lha-panel').hidden ? open() : close(); };
  el('lha-close').onclick = close;
  root.addEventListener('keydown',function (e) { if (e.key === 'Escape') close(); });
  root.querySelectorAll('[data-intent]').forEach(function (b) { b.onclick = function () { send({intent:b.dataset.intent}); }; });
  el('lha-chat').onsubmit = function (e) { e.preventDefault(); if (busy) return; var message = el('lha-message').value.trim(); if (!message) return; bubble(message,'user'); el('lha-message').value = ''; send({message:message}); };
  el('lha-shipment').onsubmit = function (e) { e.preventDefault(); readFields(); send({intent:'booking'}); };
  el('lha-price').onclick = function () { readFields(); send({intent:'pricing'}); };
  el('lha-tracking').onsubmit = function (e) { e.preventDefault(); send({intent:'tracking',tracking_token:el('lha-tracking').elements.tracking_token.value.trim()}); el('lha-tracking').elements.tracking_token.value = ''; };
  el('lha-reset').onclick = function () { draft = {}; syncFields(); el('lha-messages').replaceChildren(); el('lha-chat').reset(); el('lha-tracking').reset(); el('lha-shipment').hidden = true; el('lha-tracking').hidden = true; try { sessionStorage.removeItem('lifthaul_assistant_draft'); } catch (_) {} bubble('Conversation cleared. How can I help?'); };
  bubble('Hello! I can help with shipments, estimates, tracking and trucker registration. Choose an action to get started.');
})();
