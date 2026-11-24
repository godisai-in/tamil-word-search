// Progressive enhancement: search-box autocomplete. No inline scripts (the CSP forbids them).
'use strict';

const q = document.getElementById('q') || document.getElementById('mini-q');
const dl = document.getElementById('sugs');
if (q && dl) {
  let timer, controller;
  q.addEventListener('input', () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const term = q.value.trim();
      if (term.length < 2) return;
      if (controller) controller.abort();  // drop answers to older keystrokes
      controller = new AbortController();
      try {
        const res = await fetch('/suggest/?q=' + encodeURIComponent(term), {signal: controller.signal});
        if (!res.ok) return;
        const {items} = await res.json();
        dl.replaceChildren(...items.map(text => {
          const opt = document.createElement('option');
          opt.value = text;  // a property, never parsed as HTML
          return opt;
        }));
      } catch { /* aborted or offline: keep the old list */ }
    }, 120);
  });
}
