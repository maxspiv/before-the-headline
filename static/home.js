'use strict';

const cardsEl = document.getElementById('investigation-cards');
const searchEl = document.getElementById('investigation-search');
const searchEmpty = document.getElementById('search-empty');
const statusEl = document.getElementById('home-status');
const fileEl = document.getElementById('import-file');
const textEl = document.getElementById('import-text');
const submitEl = document.getElementById('import-submit');
const resultEl = document.getElementById('import-result');
const errorsEl = document.getElementById('import-errors');

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function cardFor(inv) {
  const card = el('article', 'evidence-card investigation-card');
  card.dataset.investigationId = inv.id;
  card.dataset.search =
    (inv.title + ' ' + inv.topic + ' ' + inv.id).toLowerCase();
  card.appendChild(el('p', 'card-title', inv.title));
  card.appendChild(el('p', 'card-meta', inv.topic));
  const badges = el('div', 'badges');
  badges.appendChild(el('span', 'badge uncertain', inv.coverage_status));
  badges.appendChild(el('span', 'badge unrelated',
    inv.origin === 'bundled' ? 'Bundled' : 'Imported'));
  card.appendChild(badges);
  let meta = inv.article_count + ' articles · ' +
    inv.inspected_count + ' reviewed · ' + inv.related_count +
    ' on story';
  if (inv.imported_at) meta += ' · imported ' + inv.imported_at.slice(0, 10);
  card.appendChild(el('p', 'card-meta', meta));
  const actions = el('div', 'card-actions');
  const open = el('a', 'cta', 'Open');
  open.href = '/investigations/' + encodeURIComponent(inv.id);
  actions.appendChild(open);
  if (inv.origin === 'imported') {
    const remove = el('button', 'remove-btn', 'Remove');
    remove.type = 'button';
    remove.dataset.remove = inv.id;
    actions.appendChild(remove);
  }
  card.appendChild(actions);
  return card;
}

function applySearch() {
  const needle = searchEl.value.trim().toLowerCase();
  let visible = 0;
  for (const card of cardsEl.querySelectorAll('.investigation-card')) {
    const show = !needle || card.dataset.search.includes(needle);
    card.classList.toggle('hidden', !show);
    if (show) visible += 1;
  }
  searchEmpty.classList.toggle('hidden', visible !== 0);
}

searchEl.addEventListener('input', applySearch);

async function refreshList() {
  const res = await fetch('/api/investigations');
  if (!res.ok) throw new Error('HTTP ' + res.status);
  const data = await res.json();
  cardsEl.textContent = '';
  for (const inv of data.investigations) cardsEl.appendChild(cardFor(inv));
  applySearch();
}

function showErrors(errors) {
  errorsEl.textContent = '';
  errorsEl.appendChild(el('p', null, 'Fix these and try again:'));
  const list = el('ul');
  for (const message of errors) list.appendChild(el('li', null, message));
  errorsEl.appendChild(list);
}

submitEl.addEventListener('click', async () => {
  errorsEl.textContent = '';
  resultEl.textContent = '';
  let body = textEl.value;
  const file = fileEl.files && fileEl.files[0];
  if (file) {
    try {
      body = await file.text();
    } catch (err) {
      showErrors(['Could not read the selected file.']);
      return;
    }
  }
  if (!body.trim()) {
    showErrors(['Choose a file or paste JSON.']);
    return;
  }
  submitEl.disabled = true;
  try {
    const res = await fetch('/api/investigations/import', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: body,
    });
    if (res.status === 201) {
      const data = await res.json();
      resultEl.textContent = '';
      const p = el('p', null, 'Imported \u201c' + data.summary.title + '\u201d. ');
      const link = el('a', null, 'Open');
      link.href = '/investigations/' + encodeURIComponent(data.id);
      p.appendChild(link);
      p.appendChild(document.createTextNode('.'));
      resultEl.appendChild(p);
      textEl.value = '';
      fileEl.value = '';
      await refreshList();
    } else if (res.status === 413) {
      showErrors(['File is larger than 1 MiB.']);
    } else {
      const data = await res.json().catch(() => null);
      showErrors((data && data.errors) ||
        ['Import failed with HTTP ' + res.status]);
    }
  } catch (err) {
    showErrors(['Import request failed: ' + err.message]);
  } finally {
    submitEl.disabled = false;
  }
});

cardsEl.addEventListener('click', async (e) => {
  const btn = e.target.closest('[data-remove]');
  if (!btn) return;
  const id = btn.dataset.remove;
  if (!confirm('Remove \u201c' + id + '\u201d? Its JSON file will be deleted.')) return;
  const res = await fetch('/api/investigations/' + encodeURIComponent(id),
    {method: 'DELETE'});
  if (res.status === 204) {
    statusEl.textContent = 'Removed ' + id + '.';
    await refreshList();
  } else {
    const data = await res.json().catch(() => null);
    statusEl.textContent = 'Remove failed: ' +
      ((data && data.error) || 'HTTP ' + res.status);
  }
});
