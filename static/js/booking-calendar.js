(() => {
  'use strict';
  const calendar = document.getElementById('calendar-day-form');
  const meeting = document.getElementById('meeting');
  const form = document.getElementById('appointment-form');
  const data = document.getElementById('booking-slot-data');
  if (!calendar || !meeting || !form || !data) return;
  let slots;
  try { slots = JSON.parse(data.textContent); } catch (_) { return; }
  const options = document.getElementById('booking-time-options');
  const heading = document.getElementById('selected-booking-day');
  const day = form.querySelector('input[name="day"]');
  let dirty = false;
  form.addEventListener('input', () => { dirty = true; });
  form.addEventListener('change', () => { dirty = true; });
  const choose = (value) => {
    const button = Array.from(calendar.querySelectorAll('button[name="day"]')).find(b => b.value === value && !b.disabled);
    if (!button || !slots[value] || !slots[value].length) return false;
    day.value = value;
    heading.textContent = button.dataset.dayLabel;
    options.replaceChildren();
    slots[value].forEach((slot, index) => {
      const label = document.createElement('label');
      const input = document.createElement('input');
      input.type = 'radio'; input.name = 'slot'; input.value = slot.value;
      input.required = true; input.id = 'booking-time-' + index;
      label.append(input, document.createTextNode(slot.label));
      options.append(label);
    });
    calendar.querySelectorAll('button[name="day"]').forEach(b => {
      b.classList.toggle('selected', b === button);
      if (b === button) b.setAttribute('aria-pressed', 'true'); else b.removeAttribute('aria-pressed');
    });
    meeting.hidden = false;
    heading.focus();
    meeting.scrollIntoView({block:'start'});
    return true;
  };
  calendar.addEventListener('submit', (event) => {
    if (event.submitter && choose(event.submitter.value)) event.preventDefault();
  });
  document.querySelectorAll('[data-calendar-day]').forEach(link => link.addEventListener('click', event => {
    if (choose(link.dataset.calendarDay)) event.preventDefault();
  }));
  document.querySelectorAll('[data-calendar-navigation]').forEach(link => link.addEventListener('click', event => {
    if (dirty && !window.confirm('Changing month will clear unsaved meeting details. Continue?')) event.preventDefault();
  }));
})();
