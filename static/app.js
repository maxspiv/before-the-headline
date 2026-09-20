'use strict';

const INV = document.body.dataset.investigationId;
const STORY_LABEL = document.body.dataset.storyLabel || 'the story';
const API = '/api/investigations/' + encodeURIComponent(INV);

const els = {
  cards: document.getElementById('evidence-cards'),
  resultCount: document.getElementById('result-count'),
  status: document.getElementById('evidence-status'),
  retryEvidence: document.getElementById('retry-evidence'),
  summary: document.getElementById('summary-counters'),
  scopeNotice: document.getElementById('scope-notice'),
  aggregateCount: document.getElementById('aggregate-count'),
  aggregateMeta: document.getElementById('aggregate-meta'),
  controls: {
    unrelated: document.getElementById('show-unrelated'),
    possible: document.getElementById('show-possible'),
    fold: document.getElementById('fold-confirmed'),
    inventory: document.getElementById('include-uninspected'),
  },
  filterForm: document.getElementById('filter-controls'),
  focusStory: document.getElementById('focus-story'),
  resetFilters: document.getElementById('reset-filters'),
  dialog: document.getElementById('source-dialog'),
  dialogInner: null,
  dialogClose: document.getElementById('dialog-close'),
  dayButtons: document.getElementById('day-buttons'),
  replayTimeline: document.getElementById('replay-timeline'),
  replayCount: document.getElementById('replay-count'),
  replayScope: document.getElementById('replay-scope'),
  replayEmptyNote: document.getElementById('replay-empty-note'),
  uncertainties: document.getElementById('uncertainty-cards'),
  hashes: document.getElementById('artifact-hashes'),
};
els.dialogInner = els.dialog.querySelector('.dialog-inner');

const DEFAULT_FILTERS = { unrelated: true, possible: true, fold: false, inventory: false };
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
let lastOpener = null;
let evidenceSeq = 0;
let replaySeq = 0;
let sourceSeq = 0;
let currentSourceId = null;
let evidenceAbort = null;
let replayAbort = null;
let sourceAbort = null;
let currentDay = 'all';
let dayButtonsBuilt = false;

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function badge(text, cls) {
  return el('span', 'badge ' + cls, text);
}

function langLabel(code) {
  return code ? code.charAt(0).toUpperCase() + code.slice(1) : '';
}

function isSafeUrl(value) {
  try {
    const u = new URL(value);
    return (u.protocol === 'https:' || u.protocol === 'http:') &&
      !u.username && !u.password;
  } catch (e) {
    return false;
  }
}

function currentFlags() {
  return {
    unrelated: els.controls.unrelated.checked ? '1' : '0',
    possible: els.controls.possible.checked ? '1' : '0',
    fold: els.controls.fold.checked ? '1' : '0',
    inventory: els.controls.inventory.checked ? '1' : '0',
  };
}

function evidenceUrl() {
  const f = currentFlags();
  return API + '/evidence?unrelated=' + f.unrelated + '&possible=' + f.possible +
    '&fold=' + f.fold + '&inventory=' + f.inventory;
}

function renderSummary(summary) {
  els.summary.textContent = '';
  const items = [
    [summary.inspected_total, 'inspected pages'],
    [summary.related_inspected, 'story-related'],
    [summary.unrelated_inspected, 'other stories'],
    [summary.inspected_gdelt_pages, 'GDELT results'],
    [summary.inspected_external_pages, 'external / contextual'],
  ];
  for (const [n, label] of items) {
    const box = el('div', 'counter');
    box.appendChild(el('strong', null, n));
    box.appendChild(document.createTextNode(label));
    els.summary.appendChild(box);
  }
}

function renderCounts(counts) {
  els.resultCount.textContent =
    'Showing ' + counts.visible_count + ' of ' + counts.cohort_count +
    ' cards (' + counts.visible_inspected + ' inspected, ' +
    counts.visible_uninspected + ' uninspected) · ' +
    counts.filtered_out_count + ' filtered out · ' +
    counts.folded_copy_count + ' folded as confirmed copied text';
}

function cardFor(row) {
  const card = el('article', 'evidence-card');
  card.appendChild(el('p', 'card-title', row.title));
  card.appendChild(el('p', 'card-meta',
    row.publisher + ' · ' + langLabel(row.language) + ' · ' + row.origin_label));
  const badges = el('div', 'badges');
  badges.appendChild(badge(row.category_label,
    row.category === 'related' ? 'related' :
    row.category === 'unrelated' ? 'unrelated' : 'uncertain'));
  if (row.confirmed_duplicate) {
    badges.appendChild(badge(
      'Confirmed copied text' +
      (row.confirmed_group_size ? ' · ' + row.confirmed_group_size + ' pages' : ''),
      'confirmed'));
  } else if (row.possible_shared) {
    badges.appendChild(badge('Possible shared reporting', 'possible'));
  }
  if (!row.inspected) badges.appendChild(badge('Uninspected', 'uninspected'));
  card.appendChild(badges);
  if (row.claim_label) {
    card.appendChild(el('p', 'card-meta', 'Publisher-claimed: ' + row.claim_label));
  }
  const btn = el('button', 'inspect-btn',
    row.inspected ? 'Inspect source' : 'Inspect inventory record');
  btn.dataset.sourceId = row.id;
  btn.setAttribute('aria-label', 'Inspect source: ' + row.title);
  card.appendChild(btn);
  return card;
}

function renderEmpty() {
  const box = el('div', 'empty-state');
  box.appendChild(el('p', null,
    'No cards match the current filters. Hiding possible shared reporting ' +
    'can hide retained ' + STORY_LABEL + ' pages, and hiding other stories removes ' +
    'the contextual checks.'));
  const btn = el('button', null, 'Reset filters');
  btn.addEventListener('click', resetFilters);
  box.appendChild(btn);
  els.cards.appendChild(box);
}

function renderEvidenceError(message) {
  els.cards.textContent = '';
  els.resultCount.textContent = 'Current filtered results unavailable';
  els.status.setAttribute('role', 'alert');
  els.status.textContent = 'Could not load cached evidence. ' + message;
  els.retryEvidence.classList.remove('hidden');
}

async function loadEvidence() {
  const seq = ++evidenceSeq;
  if (evidenceAbort) evidenceAbort.abort();
  evidenceAbort = new AbortController();
  els.status.setAttribute('role', 'status');
  els.status.textContent = 'Loading cached evidence…';
  els.retryEvidence.classList.add('hidden');
  els.cards.setAttribute('aria-busy', 'true');
  try {
    const res = await fetch(evidenceUrl(), { signal: evidenceAbort.signal });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const data = await res.json();
    if (seq !== evidenceSeq) return;
    renderSummary(data.summary);
    renderCounts(data.counts);
    els.scopeNotice.textContent = data.scope_notice || '';
    const aggregate = data.aggregate_context;
    if (aggregate && els.aggregateCount && els.aggregateMeta) {
      els.aggregateCount.textContent = aggregate.count;
      els.aggregateMeta.textContent = aggregate.source + ' · ' +
        aggregate.language + ' · ' + aggregate.date;
    }
    renderHashes(data.artifact_hashes);
    renderUncertainties(data.uncertainties);
    els.cards.textContent = '';
    for (const row of data.cards) els.cards.appendChild(cardFor(row));
    if (!data.cards.length) renderEmpty();
    els.status.textContent = '';
  } catch (err) {
    if (err.name === 'AbortError' || seq !== evidenceSeq) return;
    renderEvidenceError(err.message);
  } finally {
    if (seq === evidenceSeq) els.cards.removeAttribute('aria-busy');
  }
}

function renderHashes(hashes) {
  if (els.hashes.childElementCount) return;
  for (const [name, hash] of Object.entries(hashes || {})) {
    els.hashes.appendChild(el('dt', null, name));
    els.hashes.appendChild(el('dd', null, hash));
  }
}

function sourceLabel(src) {
  return src.publisher + ' (' + langLabel(src.language) + ')';
}

function renderUncertainties(list) {
  els.uncertainties.textContent = '';
  for (const u of list || []) {
    const card = el('article', 'evidence-card');
    card.appendChild(el('p', 'card-title', u.title));
    card.appendChild(el('p', 'card-meta', u.detail));
    const sources = el('div', 'uncertainty-sources');
    for (const src of u.sources || []) {
      const b = el('button', 'group-member-btn', sourceLabel(src));
      b.dataset.sourceId = src.id;
      sources.appendChild(b);
    }
    card.appendChild(sources);
    els.uncertainties.appendChild(card);
  }
}

function clearDialogContents() {
  document.getElementById('source-title').textContent = 'Loading…';
  document.getElementById('source-meta').textContent = '';
  document.getElementById('source-claim').textContent = '';
  const link = document.getElementById('source-external-link');
  link.removeAttribute('href');
  link.textContent = 'Open original page (requires internet)';
  document.getElementById('source-url-text').textContent = '';
  document.getElementById('source-excerpt').textContent = '';
  document.getElementById('source-excerpt-caption').textContent = '';
  document.getElementById('source-scope').textContent = '';
  document.getElementById('source-syndication').textContent = '';
  document.getElementById('source-group-members').textContent = '';
  document.getElementById('source-event-times').textContent = '';
  document.querySelector('#source-timestamps tbody').textContent = '';
  document.getElementById('source-seendate').textContent = '';
  document.getElementById('source-retrieved').textContent = '';
  document.getElementById('source-gkg').textContent = '';
  document.getElementById('source-uncertainties').textContent = '';
  document.getElementById('timestamp-details').classList.add('hidden');
  document.getElementById('source-uncertainty-section').classList.add('hidden');
}

function renderSourceError(message, id) {
  document.getElementById('source-title').textContent =
    'Source could not be loaded';
  const p = el('p', null, message);
  const retry = el('button', null, 'Retry');
  retry.addEventListener('click', () => openSource(id, null));
  const scope = document.getElementById('source-scope');
  scope.textContent = '';
  scope.appendChild(p);
  scope.appendChild(retry);
}

async function openSource(id, opener) {
  if (opener && !els.dialog.contains(opener)) lastOpener = opener;
  const seq = ++sourceSeq;
  if (sourceAbort) sourceAbort.abort();
  sourceAbort = new AbortController();
  const switching = currentSourceId !== id;
  currentSourceId = id;
  clearDialogContents();
  if (!els.dialog.open) els.dialog.showModal();
  if (switching) els.dialogInner.scrollTop = 0;
  els.dialogClose.focus();
  try {
    const res = await fetch(API + '/source/' + encodeURIComponent(id),
      { signal: sourceAbort.signal });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const s = await res.json();
    if (seq !== sourceSeq || !els.dialog.open || currentSourceId !== id) return;
    populateSource(s);
  } catch (err) {
    if (err.name === 'AbortError') return;
    if (seq !== sourceSeq || !els.dialog.open) return;
    renderSourceError('Could not load source: ' + err.message, id);
  }
}

function populateSource(s) {
  document.getElementById('source-title').textContent = s.title;
  document.getElementById('source-meta').textContent =
    s.publisher + ' · ' + langLabel(s.language) + ' · ' + s.origin_label +
    (s.inspected ? ' · inspected' : ' · uninspected');

  const claim = document.getElementById('source-claim');
  claim.textContent = '';
  const claimValue = el('span', 'claim-value',
    s.claim_label || 'No publisher-claimed timestamp');
  const claimNote = el('span', 'claim-note',
    'Publisher-visible timestamp: ' +
    (s.publisher_visible_timestamp || 'unknown') +
    ' · Not verified first publication');
  claim.appendChild(claimValue);
  claim.appendChild(claimNote);

  const link = document.getElementById('source-external-link');
  const urlText = document.getElementById('source-url-text');
  if (s.external_url && isSafeUrl(s.external_url)) {
    link.href = s.external_url;
    link.textContent = 'Open original page (requires internet)';
  } else {
    link.removeAttribute('href');
    link.textContent = 'External link unavailable';
  }
  urlText.textContent = s.url ? 'URL: ' + s.url : '';

  const excerptEl = document.getElementById('source-excerpt');
  const capEl = document.getElementById('source-excerpt-caption');
  if (s.excerpt && s.excerpt.text) {
    excerptEl.textContent = s.excerpt.text;
    capEl.textContent = 'Cached excerpt · ' + (s.excerpt.source_file || 'supplied with import') +
      (s.excerpt.range_label ? ' lines ' + s.excerpt.range_label : '') +
      (s.excerpt.truncated ? ' · truncated' : '') +
      (s.excerpt.meaning ? ' · ' + s.excerpt.meaning : '');
  } else {
    capEl.textContent = 'No reviewed excerpt is cached for this record.';
  }

  document.getElementById('source-scope').textContent =
    s.scope_notes || 'No scope notes.';

  document.getElementById('source-syndication').textContent =
    'Duplicate group ' + s.duplicate_group + ' — ' +
    (s.syndication_assessment || 'Not assessed.');
  const members = document.getElementById('source-group-members');
  for (const m of s.confirmed_group_members || []) {
    const b = el('button', 'group-member-btn', m.publisher + ' — ' + m.title);
    b.dataset.sourceId = m.id;
    members.appendChild(b);
  }

  document.getElementById('source-event-times').textContent =
    s.reported_event_timestamps_and_uncertainty || 'None recorded.';

  const tbody = document.querySelector('#source-timestamps tbody');
  if ((s.publisher_embedded_timestamps || []).length) {
    document.getElementById('timestamp-details').classList.remove('hidden');
  }
  for (const t of s.publisher_embedded_timestamps || []) {
    const tr = el('tr');
    tr.appendChild(el('td', null, t.field));
    tr.appendChild(el('td', null, t.value));
    tr.appendChild(el('td', null, t.utc_if_explicit_offset || 'Not supplied in cached artifact'));
    tr.appendChild(el('td', null,
      (t.meaning || '') + (t.location ? ' · ' + t.location : '')));
    tbody.appendChild(tr);
  }

  document.getElementById('source-seendate').textContent =
    'GDELT seendate (platform observation): ' +
    (s.gdelt_seendate || 'unavailable') +
    (s.gdelt_seendate_meaning ? ' — ' + s.gdelt_seendate_meaning : '');
  document.getElementById('source-retrieved').textContent =
    'Captured by this project (retrieved_at_utc): ' +
    (s.retrieved_at_utc || 'unavailable') +
    (s.retrieval_meaning ? ' — ' + s.retrieval_meaning : '');
  document.getElementById('source-gkg').textContent =
    (s.gkg_document_date || s.gkg_record_id)
      ? 'GKG metadata labels (not verified publisher time): document-date field ' + (s.gkg_document_date || 'n/a') +
        ' · record ' + (s.gkg_record_id || 'n/a')
      : 'No GKG record fields for this source.';

  const ulist = document.getElementById('source-uncertainties');
  const uncs = s.uncertainties || [];
  if (uncs.length) {
    document.getElementById('source-uncertainty-section').classList.remove('hidden');
  }
  for (const u of uncs) {
    const li = el('li');
    li.appendChild(el('strong', null, u.title + ' '));
    li.appendChild(document.createTextNode(u.detail));
    for (const src of u.sources || []) {
      if (src.id === s.id) continue;
      li.appendChild(document.createTextNode(' '));
      const b = el('button', 'group-member-btn',
        'Compare: ' + sourceLabel(src));
      b.dataset.sourceId = src.id;
      li.appendChild(b);
    }
    ulist.appendChild(li);
  }
}

function closeDialog() {
  if (els.dialog.open) els.dialog.close();
}

els.dialog.addEventListener('close', () => {
  sourceSeq += 1;
  if (sourceAbort) sourceAbort.abort();
  currentSourceId = null;
  const target = (lastOpener && document.contains(lastOpener))
    ? lastOpener : els.focusStory;
  lastOpener = null;
  target.focus();
});
els.dialogClose.addEventListener('click', closeDialog);
els.dialog.addEventListener('click', (e) => {
  if (e.target === els.dialog) closeDialog();
});
els.dialog.addEventListener('keydown', (e) => {
  if (e.key !== 'Tab') return;
  const focusables = Array.from(els.dialog.querySelectorAll(
    'a[href], button:not([disabled]), summary, [tabindex]:not([tabindex="-1"])'))
    .filter((n) => n.offsetParent !== null);
  if (!focusables.length) return;
  const first = focusables[0];
  const last = focusables[focusables.length - 1];
  if (e.shiftKey && document.activeElement === first) {
    e.preventDefault(); last.focus();
  } else if (!e.shiftKey && document.activeElement === last) {
    e.preventDefault(); first.focus();
  }
});

document.addEventListener('click', (e) => {
  const btn = e.target.closest('[data-source-id]');
  if (btn) {
    openSource(btn.dataset.sourceId, btn);
    return;
  }
  const card = e.target.closest('#evidence-cards .evidence-card');
  if (card) {
    const inspect = card.querySelector('.inspect-btn');
    if (inspect) openSource(inspect.dataset.sourceId, inspect);
  }
});

function setFilters(f) {
  els.controls.unrelated.checked = f.unrelated;
  els.controls.possible.checked = f.possible;
  els.controls.fold.checked = f.fold;
  els.controls.inventory.checked = f.inventory;
}
function resetFilters() {
  setFilters(DEFAULT_FILTERS);
  loadEvidence();
}
els.filterForm.addEventListener('submit', (e) => e.preventDefault());
for (const key of Object.keys(els.controls)) {
  els.controls[key].addEventListener('change', loadEvidence);
}
els.resetFilters.addEventListener('click', resetFilters);
els.retryEvidence.addEventListener('click', loadEvidence);
els.focusStory.addEventListener('click', () => {
  setFilters({ unrelated: false, possible: true, fold: true, inventory: false });
  loadEvidence();
});

function replayEventButton(ev) {
  const btn = el('button', 'replay-event');
  btn.dataset.replaySource = ev.id;
  btn.dataset.sourceId = ev.id;
  const marker = el('span', 'replay-marker' + (ev.date_only ? ' date-only' : ''));
  marker.setAttribute('aria-hidden', 'true');
  const body = el('span', 'replay-event-body');
  body.appendChild(el('span', 'replay-event-title',
    ev.publisher + ' — ' + ev.title));
  body.appendChild(el('span', 'replay-event-claim', ev.claim_label));
  btn.appendChild(marker);
  btn.appendChild(body);
  return btn;
}

function formatDay(iso) {
  const parts = iso.split('-');
  return String(Number(parts[2])) + ' ' + MONTHS[Number(parts[1]) - 1];
}

function buildDayButtons(days) {
  if (dayButtonsBuilt) return;
  dayButtonsBuilt = true;
  for (const day of days || []) {
    const btn = el('button', 'day-btn', formatDay(day));
    btn.type = 'button';
    btn.dataset.day = day;
    btn.setAttribute('aria-pressed', 'false');
    els.dayButtons.appendChild(btn);
  }
}

function renderReplayError(message, day) {
  els.replayTimeline.textContent = '';
  els.replayCount.textContent = 'Replay view unavailable';
  const box = el('div', 'empty-state');
  box.appendChild(el('p', null, 'Could not load replay data. ' + message));
  const retry = el('button', null, 'Retry');
  retry.addEventListener('click', () => loadReplay(day));
  box.appendChild(retry);
  els.replayTimeline.appendChild(box);
}

async function loadReplay(day) {
  currentDay = day;
  const seq = ++replaySeq;
  if (replayAbort) replayAbort.abort();
  replayAbort = new AbortController();
  els.replayTimeline.setAttribute('aria-busy', 'true');
  try {
    const res = await fetch(API + '/replay?day=' + encodeURIComponent(day),
      { signal: replayAbort.signal });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const data = await res.json();
    if (seq !== replaySeq) return;
    buildDayButtons(data.days);
    els.replayScope.textContent = data.filter_scope;
    els.replayCount.textContent = 'Showing ' + data.visible_count + ' of ' +
      data.total_count + ' retained pages';
    const emptyMeaning = data.empty_interval_meaning || '';
    els.replayEmptyNote.textContent =
      'Blank intervals before and after dated pages are ' +
      (emptyMeaning
        ? emptyMeaning.charAt(0).toLowerCase() + emptyMeaning.slice(1)
        : emptyMeaning) + '. ' + (data.date_axis_meaning || '');
    els.replayTimeline.textContent = '';
    const byDay = new Map();
    for (const ev of data.events) {
      if (!byDay.has(ev.display_date)) byDay.set(ev.display_date, []);
      byDay.get(ev.display_date).push(ev);
    }
    for (const [d, events] of byDay) {
      const dayBox = el('div', 'replay-day');
      dayBox.appendChild(el('p', 'replay-day-head',
        d + ' · publisher-claimed date'));
      const list = el('div', 'replay-events');
      for (const ev of events) list.appendChild(replayEventButton(ev));
      dayBox.appendChild(list);
      els.replayTimeline.appendChild(dayBox);
    }
    if (!data.events.length) {
      els.replayTimeline.appendChild(el('div', 'empty-state',
        data.total_count === 0
          ? 'No replay entries were supplied for this investigation.'
          : 'No retained pages carry this publisher-claimed date.'));
    }
  } catch (err) {
    if (err.name === 'AbortError' || seq !== replaySeq) return;
    renderReplayError(err.message, day);
  } finally {
    if (seq === replaySeq) els.replayTimeline.removeAttribute('aria-busy');
  }
}

els.dayButtons.addEventListener('click', (e) => {
  const btn = e.target.closest('.day-btn');
  if (!btn) return;
  for (const b of els.dayButtons.querySelectorAll('.day-btn')) {
    const active = b === btn;
    b.classList.toggle('active', active);
    b.setAttribute('aria-pressed', active ? 'true' : 'false');
  }
  loadReplay(btn.dataset.day);
});
for (const b of els.dayButtons.querySelectorAll('.day-btn')) {
  b.setAttribute('aria-pressed', b.classList.contains('active') ? 'true' : 'false');
}

loadEvidence();
loadReplay('all');
