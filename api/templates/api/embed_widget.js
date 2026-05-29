/* Provost Home Design - Plan Embed Widget */
/* Usage: add  data-phd-plan="PLAN-NUMBER"  and  data-phd-key="YOUR_KEY"  to any div,
   then include this script once on the page. */
(function () {
  'use strict';

  var API_BASE = '{{ api_base }}';

  var CSS = [
    '.phd-card{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;',
    'border:1px solid #e0e0e0;border-radius:10px;overflow:hidden;max-width:380px;',
    'box-shadow:0 2px 10px rgba(0,0,0,.09);background:#fff;}',
    '.phd-card img{width:100%;height:210px;object-fit:cover;display:block;}',
    '.phd-card-body{padding:16px;}',
    '.phd-plan-num{font-size:12px;color:#999;letter-spacing:.4px;margin-bottom:6px;}',
    '.phd-specs{display:flex;flex-wrap:wrap;gap:14px;margin:10px 0 14px;}',
    '.phd-spec{display:flex;flex-direction:column;align-items:center;}',
    '.phd-spec-val{font-size:20px;font-weight:700;color:#1a1a1a;line-height:1;}',
    '.phd-spec-lbl{font-size:10px;color:#999;text-transform:uppercase;letter-spacing:.6px;margin-top:2px;}',
    '.phd-price{font-size:22px;font-weight:700;color:#2c6fad;margin-bottom:12px;}',
    '.phd-btn{display:inline-block;background:#2c6fad;color:#fff;text-decoration:none;',
    'padding:10px 22px;border-radius:6px;font-size:14px;font-weight:600;}',
    '.phd-btn:hover{background:#1a5490;}',
    '.phd-error{color:#c0392b;font-size:13px;padding:10px;}',
    '.phd-loading{color:#999;font-size:13px;padding:10px;}'
  ].join('');

  function injectStyles() {
    if (document.getElementById('phd-embed-css')) return;
    var s = document.createElement('style');
    s.id = 'phd-embed-css';
    s.textContent = CSS;
    document.head.appendChild(s);
  }

  function fmtPrice(p) {
    return '$' + parseFloat(p).toLocaleString('en-US', { maximumFractionDigits: 0 });
  }

  function spec(val, lbl) {
    return '<div class="phd-spec">'
      + '<span class="phd-spec-val">' + val + '</span>'
      + '<span class="phd-spec-lbl">' + lbl + '</span>'
      + '</div>';
  }

  function render(container, plan) {
    var img = plan.main_image_url
      ? '<img src="' + plan.main_image_url + '" alt="Plan ' + plan.plan_number + '">'
      : '';
    var price = plan.plan_price ? '<div class="phd-price">' + fmtPrice(plan.plan_price) + '</div>' : '';
    var garage = plan.garage_stalls ? spec(plan.garage_stalls, 'Garage') : '';

    container.innerHTML = '<div class="phd-card">'
      + img
      + '<div class="phd-card-body">'
      + '<div class="phd-plan-num">Plan #' + plan.plan_number + '</div>'
      + '<div class="phd-specs">'
      + spec(plan.bedrooms, 'Beds')
      + spec(plan.bathrooms, 'Baths')
      + spec(plan.square_footage.toLocaleString(), 'Sq Ft')
      + spec(plan.stories, 'Stories')
      + garage
      + '</div>'
      + price
      + '<a class="phd-btn" href="' + plan.url + '" target="_blank" rel="noopener">View Plan &#8594;</a>'
      + '</div>'
      + '</div>';
  }

  function load(container) {
    var planNumber = container.getAttribute('data-phd-plan');
    var apiKey = container.getAttribute('data-phd-key');

    if (!planNumber || !apiKey) {
      container.innerHTML = '<div class="phd-error">Missing data-phd-plan or data-phd-key.</div>';
      return;
    }

    container.innerHTML = '<div class="phd-loading">Loading plan...</div>';

    var url = API_BASE + 'plans/' + encodeURIComponent(planNumber) + '/?api_key=' + encodeURIComponent(apiKey);

    fetch(url)
      .then(function (resp) {
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        return resp.json();
      })
      .then(function (plan) { render(container, plan); })
      .catch(function (err) {
        container.innerHTML = '<div class="phd-error">Could not load plan ' + planNumber + ' (' + err.message + ').</div>';
      });
  }

  function init() {
    injectStyles();
    document.querySelectorAll('[data-phd-plan]').forEach(load);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
}());
