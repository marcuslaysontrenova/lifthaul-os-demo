/* Explicit assistant-to-booking handoff. Cargo only; geography remains canonical selectors. */
(function () {
  'use strict';
  var stored;
  try { stored = sessionStorage.getItem('lifthaul_assistant_draft'); sessionStorage.removeItem('lifthaul_assistant_draft'); } catch (_) { return; }
  if (!stored) return;
  try {
    var data = JSON.parse(stored);
    if (!data || !Number.isFinite(data.expires) || data.expires < Date.now() || !data.shipment || typeof data.shipment !== 'object') return;
    var draft = data.shipment;
    var map = {cargo:'cargoDesc',weight_kg:'cargoWeight',package_count:'packageCount',package_length_cm:'packageLength',package_width_cm:'packageWidth',package_height_cm:'packageHeight',km:'km'};
    Object.keys(map).forEach(function (key) {
      var input = document.getElementById(map[key]), value = draft[key];
      if (input && value != null && (typeof value === 'string' || typeof value === 'number')) {
        input.value = String(value).slice(0,400);
        input.dispatchEvent(new Event('input',{bubbles:true}));
        input.dispatchEvent(new Event('change',{bubbles:true}));
      }
    });
    var first = document.getElementById('cargoDesc');
    if (first) {
      var notice = document.createElement('p');
      notice.className = 'assistant-draft-notice';
      notice.setAttribute('role','status');
      notice.textContent = 'Assistant draft loaded. Review cargo details and select the complete pickup and delivery geography below. Requested route: ' + (draft.origin_city || 'Not specified') + ' → ' + (draft.dest_city || 'Not specified') + '. Nothing has been submitted.';
      first.closest('.field').before(notice);
    }
  } catch (_) { /* Invalid or expired drafts do not affect the booking flow. */ }
})();
