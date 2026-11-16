/* Cookbook Challenge: the page. Plain JavaScript, no build step.
 *
 * The server keeps no recipe. This file holds everything the person is working on in `S`, and
 * sends the transcript or the recipe back with each request. Text is only ever put on the page
 * with textContent (through h()), never as HTML, because it comes from a model.
 */
'use strict';

const S = {
  screen: 'upload',      // 'upload' | 'loading' | 'steps'
  step: 1, maxStep: 1,   // the step shown, and the furthest one unlocked
  error: null, info: null,
  job: null, message: '',
  pages: [], models: null, kind: 'pdf',
  transcript: null,      // as the server sent it (never changed)
  text: {},              // line id -> the text as the person has it now
  resolved: {},          // line id -> Set of the note indexes the person settled
  reviewed: new Set(),   // line ids the person typed in or confirmed whole
  pageChecked: {},       // page -> the person checked the whole page against the image
  page: 1,
  sent: null,            // the transcript the recipe was built from
  recipe: null, issues: [], recipeDirty: false,
  checkedIssues: new Set(), // issues the person marked as checked (hidden until they reset the warnings)
  busy: null,            // text shown over a panel while the server works
  manip: null, scaleOn: true, veganOn: false, servings: '', base: '',
};

const SEVERITIES = ['high', 'medium', 'low'];

// ---------------------------------------------------------------- helpers
function h(tag, props, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (k === 'value' || k === 'checked' || k === 'disabled' || k === 'hidden') el[k] = v;
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid == null || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

async function api(path, options) {
  let response;
  try { response = await fetch(path, options); }
  catch { throw new Error('cannot reach the server: is it still running?'); }
  let body = null;
  try { body = await response.json(); } catch { /* not JSON */ }
  if (!response.ok) {
    const d = body && body.detail;
    throw new Error(typeof d === 'string' ? d : d ? 'the request was not valid' : `error ${response.status}`);
  }
  return body;
}

const postJson = (path, data) =>
  api(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });

const pageOf = (id) => { const m = /^p(\d+)/.exec(id); return m ? +m[1] : 1; };
const clone = (x) => JSON.parse(JSON.stringify(x));
const pct = (x) => `${Math.round(x * 100)}%`;
const rank = (s) => SEVERITIES.indexOf(s);

function fmt(n) {  // a number as a person writes it: 0.5, 1.25, 600
  if (n == null) return '';
  return String(Math.round(n * 1000) / 1000);
}

function legend() {
  return h('div', { class: 'legend' }, 'Doubt level:',
    h('span', { class: 'chip high' }, 'High'), h('span', { class: 'chip medium' }, 'Medium'),
    h('span', { class: 'chip low' }, 'Low'));
}

function banner(kind, text) { return text ? h('div', { class: `banner ${kind}`, role: 'alert' }, text) : null; }

// ---------------------------------------------------------------- render
function render() {
  closePopover();
  const main = document.getElementById('main');
  main.replaceChildren();
  if (S.screen === 'upload') main.append(uploadView());
  else if (S.screen === 'loading') main.append(loadingView());
  else main.append(stepsView());
}

// ---------------------------------------------------------------- upload
function uploadView() {
  const input = h('input', { type: 'file', accept: '.pdf,.png,.jpg,.jpeg,.gif,.webp,.tif,.tiff,.txt,.md,text/plain,image/*,application/pdf',
                             onchange: () => input.files[0] && upload(input.files[0]) });
  const drop = h('label', { class: 'drop' },
    h('strong', {}, 'Drop a recipe here'),
    h('span', {}, 'a PDF, a photo or scan, or a text file, or click to choose one'), input);
  drop.addEventListener('dragover', (e) => { e.preventDefault(); drop.classList.add('over'); });
  drop.addEventListener('dragleave', () => drop.classList.remove('over'));
  drop.addEventListener('drop', (e) => {
    e.preventDefault(); drop.classList.remove('over');
    if (e.dataTransfer.files[0]) upload(e.dataTransfer.files[0]);
  });
  return h('section', { class: 'upload' },
    h('h2', {}, 'Turn a cookbook page into a recipe you can change'),
    h('p', {}, 'Two models read the page, you check what they disagree on, then scale the servings.'),
    banner('error', S.error), drop);
}

async function upload(file) {
  S.error = null; S.screen = 'loading'; S.message = `Reading ${file.name} with two models…`; S.started = Date.now();
  render();
  const form = new FormData();
  form.append('file', file);
  try {
    S.job = await api('/api/jobs', { method: 'POST', body: form });
    S.kind = S.job.kind;
    poll();
  } catch (e) { fail(e); }
}

function fail(e) { S.screen = 'upload'; S.error = e.message; render(); }

async function poll() {
  try {
    const state = await api(`/api/jobs/${S.job.job_id}`);
    if (state.status === 'reading') {
      S.message = state.message;
      const el = document.getElementById('elapsed');
      if (el) el.textContent = `${Math.round((Date.now() - S.started) / 1000)} s`;
      setTimeout(poll, 1000);
      return;
    }
    if (state.status === 'error') throw new Error(state.message);
    loaded(state.result);
  } catch (e) { fail(e); }
}

function loadingView() {
  return h('section', { class: 'loading', 'aria-live': 'polite' },
    h('div', { class: 'spinner' }),
    h('h2', {}, 'The models are reading'),
    h('p', {}, S.message), h('p', { id: 'elapsed' }, '0 s'));
}

function loaded(result) {
  S.transcript = result.transcript; S.pages = result.pages; S.models = result.models;
  S.text = clone(result.transcript.lines); S.resolved = {}; S.reviewed = new Set(); S.pageChecked = {};
  S.recipe = null; S.issues = []; S.manip = null; S.recipeDirty = false; S.sent = null; S.checkedIssues = new Set();
  S.page = 1; S.step = 1; S.maxStep = 1; S.screen = 'steps'; S.busy = null;
  render();
}

// ---------------------------------------------------------------- stepper
const STEPS = ['Check the text', 'Check the recipe', 'Change the recipe'];

function stepsView() {
  const nav = h('ol', { class: 'stepper' }, STEPS.map((label, i) => {
    const n = i + 1;
    const state = n === S.step ? 'active' : n <= S.maxStep ? 'done' : 'locked';
    return h('li', { class: state, onclick: () => { if (n <= S.maxStep && n !== S.step) { S.step = n; render(); } } },
      h('span', { class: 'num' }, state === 'done' ? '✓' : n), h('span', { class: 'label' }, label));
  }));
  const body = S.step === 1 ? textStep() : S.step === 2 ? recipeStep() : manipulateStep();
  return h('section', {}, nav, banner('error', S.error), banner('info', S.info), body,
    h('div', { class: 'actions' }, h('button', { class: 'btn small', onclick: restart }, '← Start with another file')));
}

function restart() {
  if ((S.recipeDirty || S.reviewed.size || Object.keys(S.resolved).length) && !confirm('Discard your changes and start over?')) return;
  S.screen = 'upload'; S.error = null; S.info = null; render();
}

function busyView(text) {
  return h('div', { class: 'panel loading', style: 'margin-top:0', 'aria-live': 'polite' },
    h('div', { class: 'spinner' }), h('p', {}, text));
}

// ---------------------------------------------------------------- step 1: the text
function notesOf(id) { return S.transcript.uncertain[id] || []; }

function openNotes(id) {  // the doubts on a line the person has not settled, as [index, note]
  if (S.reviewed.has(id)) return [];
  const done = S.resolved[id] || new Set();
  return notesOf(id).map((n, i) => [i, n]).filter(([i, n]) => !done.has(i) && (n.kind != null || !S.pageChecked[pageOf(id)]));
}

function doubtCount() {
  const out = { high: 0, medium: 0 };
  for (const id of Object.keys(S.transcript.uncertain)) {
    for (const [, n] of openNotes(id)) if (n.kind != null) out[n.severity] += 1;
  }
  return out;
}

// every doubt on every page, most serious first, so none is found only by visiting its page
function allDoubts() {
  const out = [];
  for (const id of Object.keys(S.transcript.uncertain)) {
    for (const [i, n] of openNotes(id)) if (n.kind != null) out.push({ page: pageOf(id), id, index: i, note: n, severity: n.severity });
  }
  for (const p of S.pages) {
    if (S.pageChecked[p.page]) continue;
    for (const c of p.comparisons) {
      if (c.unreliable) out.push({ page: p.page, severity: 'high', pageWide: true, text: `Page ${p.page} is unreliable: the two models agree on only ${pct(c.agreement)} of the words. Check every line.` });
      else if (c.reordered) out.push({ page: p.page, severity: 'medium', pageWide: true, text: `Page ${p.page}: ${c.reordered} block(s) of words are in a different order in the two readings (columns?). Lines may mix columns.` });
    }
  }
  return out.sort((a, b) => rank(a.severity) - rank(b.severity) || a.page - b.page);
}

function goToDoubt(d) {
  S.page = d.page; render();
  const row = d.id ? document.querySelector(`.line[data-line="${d.id}"]`) : document.querySelector('.meter');
  if (!row) return;
  row.scrollIntoView({ block: 'center' });
  row.classList.add('flash');
  setTimeout(() => row.classList.remove('flash'), 1600);
  const mark = d.id && row.querySelector('mark, .flagbtn');
  if (mark) mark.click();  // opens the choice of readings
}

function keepScroll(fn) { const keep = window.scrollY; fn(); render(); window.scrollTo(0, keep); }

// bring every warning back (the text the person edited stays as it is)
function resetTextWarnings() {
  keepScroll(() => { S.reviewed = new Set(); S.resolved = {}; S.pageChecked = {}; });
}

const hasTextWarnings = () => S.reviewed.size > 0 || Object.keys(S.resolved).length > 0 || Object.keys(S.pageChecked).length > 0;

function resetButton(onclick, disabled) {
  return h('button', { class: 'btn small', onclick, disabled, title: 'Show the warnings you checked again, so you can check them again' }, 'Reset warnings');
}

function doubtList() {
  const list = allDoubts();
  const reset = resetButton(resetTextWarnings, !hasTextWarnings());
  if (!list.length) return h('div', { class: 'panel' }, h('h2', {}, 'Doubts'),
    h('p', { class: 'empty' }, 'Nothing left to check: the two models agree everywhere, or you checked every doubt.'),
    h('div', { class: 'actions', style: 'margin-top:8px' }, reset));
  return h('div', { class: 'panel' }, h('h2', {}, `Doubts (${list.length})`),
    h('p', { class: 'hint' }, 'Everything the two models disagree on, in all pages. Click one to go to it. A warning goes away when you check its line (✓) or fix it.'),
    h('div', { class: 'actions', style: 'margin:0 0 10px' }, reset),
    h('ul', { class: 'doubts' }, list.map((d) => h('li', {},
      h('button', { class: `doubt ${d.severity}`, onclick: () => goToDoubt(d) },
        h('span', { class: `tag ${d.severity}` }, d.severity),
        h('span', { class: 'where' }, d.id ? `page ${d.page} · ${d.id.replace(/^p\d+:/, '')}` : `page ${d.page}`),
        h('span', { class: 'what' }, d.pageWide ? d.text
          : d.note.primary || d.note.secondary ? `${d.note.kind}: “${d.note.primary || ''}” vs “${d.note.secondary || ''}”` : d.note.reason))))));
}

function pageMeter(info) {
  const out = [];
  for (const c of info.comparisons) {
    const level = c.unreliable ? 'high' : c.agreement < 0.95 ? 'medium' : 'low';
    out.push(h('div', { class: `meter ${level === 'low' ? '' : level}` },
      h('b', {}, `Agreement of the two models${info.comparisons.length > 1 ? ` (${c.label})` : ''}: ${pct(c.agreement)}`),
      c.unreliable ? h('span', { class: 'tag high', style: 'margin-left:8px' }, 'unreliable') : null,
      h('div', { class: 'bar' }, h('i', { style: `width:${pct(c.agreement)}` })),
      c.unreliable ? h('div', { class: 'hint' }, 'The two models mostly disagree, so this reading is probably wrong. Check every line against the image.') : null,
      c.reordered ? h('div', { class: 'hint' }, `${c.reordered} block(s) of words are in a different order in the two readings (columns?). Lines may mix columns.`) : null));
  }
  if (!out.length) {
    out.push(h('div', { class: 'meter' }, h('b', {}, info.source === 'text_file' ? 'A text file: used as written, nothing to compare.'
      : info.source === 'text_layer' ? 'Typed text: read straight from the file, nothing to compare.' : 'Read by one model only: nothing to compare.')));
  }
  for (const w of info.warnings) out.push(banner('warn', w));
  return out;
}

function textStep() {
  if (S.busy) return busyView(S.busy);
  const pages = S.pages.map((p) => p.page);
  const info = S.pages.find((p) => p.page === S.page) || S.pages[0];
  const ids = Object.keys(S.text).filter((id) => pageOf(id) === info.page);
  const left = doubtCount();
  const hasImage = S.kind !== 'text' && info.source !== 'text_file';

  const lines = h('div', { class: 'lines' }, ids.length ? ids.map((id) => lineView(id)) : h('p', { class: 'empty' }, 'No text on this page.'));
  const pageLevel = ids.some((id) => notesOf(id).some((n) => n.kind == null));

  return h('div', {},
    h('div', { class: 'panel' },
      h('h2', {}, 'Is the text right?'),
      h('p', { class: 'hint' }, 'Words the two models read differently are underlined. Click one to choose a reading or type your own. Click anywhere else on a line to edit it.'),
      h('div', { class: 'legend' }, legend(),
        h('span', {}, `Left to check: `, h('span', { class: 'chip high' }, `${left.high} high`), ' ', h('span', { class: 'chip medium' }, `${left.medium} medium`)),
        S.models ? h('span', {}, `Models: ${S.models.primary}` + (S.models.second ? ` and ${S.models.second}` : '')) : null)),
    doubtList(),
    h('div', { class: 'panel' },
      pages.length > 1 ? h('div', { class: 'pages' }, pages.map((n) => {
        const k = allDoubts().filter((d) => d.page === n).length;
        return h('button', { class: n === S.page ? 'on' : '', onclick: () => { S.page = n; render(); } }, `Page ${n}`, k ? h('span', { class: 'chip medium', style: 'margin-left:6px;padding:0 6px' }, k) : null);
      })) : null,
      pageMeter(info),
      pageLevel && !S.pageChecked[info.page] ? h('div', { class: 'actions', style: 'margin:0 0 12px' },
        h('button', { class: 'btn small', onclick: () => { S.pageChecked[info.page] = true; render(); } }, 'I checked this whole page against the image')) : null,
      h('div', { class: 'reading' },
        hasImage ? h('div', { class: 'source' }, h('img', { src: `/api/jobs/${S.job.job_id}/page/${info.page}.jpg`, alt: `The original page ${info.page}` })) : null,
        lines)),
    h('div', { class: 'actions' },
      h('button', { class: 'btn primary', onclick: confirmText }, 'The text is OK → build the recipe'),
      left.high + left.medium ? h('span', { class: 'hint', style: 'margin:0' }, `${left.high + left.medium} doubt(s) not settled: they will be marked in the recipe.`) : null));
}

function lineView(id) {
  const row = h('div', { class: 'line', 'data-line': id });
  fillLine(row, id);
  return row;
}

function fillLine(row, id) {
  const text = S.text[id];
  const open = openNotes(id).filter(([, n]) => n.kind != null);
  const worst = open.reduce((w, [, n]) => (w == null || rank(n.severity) < rank(w) ? n.severity : w), null);
  const checked = S.reviewed.has(id) || (notesOf(id).some((n) => n.kind != null) && !open.length);
  row.className = 'line' + (checked ? ' checked' : '');
  row.replaceChildren(h('span', { class: 'id' }, id.replace(/^p\d+:/, '')));

  const body = h('div', { class: 'body', title: 'Click to edit this line' });
  // underline the word each open doubt is about; a doubt whose word cannot be found marks the whole line
  const spans = [], whole = [];
  for (const [i, n] of open) {
    const at = n.primary ? text.indexOf(n.primary) : -1;
    if (at >= 0) spans.push({ at, end: at + n.primary.length, i, n }); else whole.push([i, n]);
  }
  spans.sort((a, b) => a.at - b.at);
  let pos = 0;
  for (const s of spans) {
    if (s.at < pos) { whole.push([s.i, s.n]); continue; }
    body.append(text.slice(pos, s.at));
    body.append(h('mark', { class: s.n.severity, tabindex: '0', role: 'button',
                            title: `${s.n.severity}: ${s.n.reason}`,
                            onclick: (e) => { e.stopPropagation(); openPopover(e.currentTarget, id, s.i, s.n, row); },
                            onkeydown: (e) => { if (e.key === 'Enter') { e.stopPropagation(); openPopover(e.currentTarget, id, s.i, s.n, row); } } },
                          text.slice(s.at, s.end)));
    pos = s.end;
  }
  body.append(text.slice(pos));
  if (!text) body.append(h('span', { class: 'empty' }, '(empty line)'));
  for (const [i, n] of whole) {
    row.classList.add('wholeline', n.severity);
    body.append(h('button', { class: `flagbtn ${n.severity}`, title: n.reason,
                              onclick: (e) => { e.stopPropagation(); openPopover(e.currentTarget, id, i, n, row); } }, `${n.kind}?`));
  }
  if (checked) body.append(h('span', { class: 'ok' }, '✓ checked'));
  body.addEventListener('click', () => editLine(row, id));
  row.append(body);
  if (open.length) {  // a line that still has a warning: one click says "I checked it, it is right"
    row.append(h('button', { class: 'btn small checkbtn', title: 'I checked this line against the page: it is right',
                             onclick: (e) => { e.stopPropagation(); keepScroll(() => S.reviewed.add(id)); } }, '✓ Check'));
    row.classList.add('hasbtn');
  }
}

function editLine(row, id) {
  closePopover();
  const before = S.text[id];
  const input = h('input', { type: 'text', value: before, 'aria-label': `Edit line ${id}` });
  const commit = () => {
    input.removeEventListener('blur', commit);
    S.text[id] = input.value;
    S.reviewed.add(id);  // typing in a line, or leaving it as it is, means a person has read it
    S.recipeDirty = S.recipeDirty || (S.recipe != null && input.value !== before);
    fillLine(row, id);
    refreshCounters();
  };
  input.addEventListener('blur', commit);
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') input.blur();
    if (e.key === 'Escape') { input.value = before; input.blur(); }
  });
  row.querySelector('.body').replaceWith(h('div', { class: 'body' }, input));
  input.focus(); input.select();
}

function refreshCounters() {  // redraw so the counters at the top follow, and stay where the person was
  if (S.screen === 'steps' && S.step === 1) { const keep = window.scrollY; render(); window.scrollTo(0, keep); }
}

// the popover for one doubtful word
function openPopover(anchor, id, index, note, row) {
  const pop = document.getElementById('popover');
  pop.replaceChildren();
  const text = S.text[id];
  const canReplace = note.primary && text.includes(note.primary);
  const settle = (newText) => {
    S.text[id] = newText;
    (S.resolved[id] = S.resolved[id] || new Set()).add(index);
    S.recipeDirty = S.recipeDirty || S.recipe != null;
    closePopover();
    const keep = window.scrollY; render(); window.scrollTo(0, keep);
  };
  const replaceWith = (word) => settle(text.replace(note.primary, word));

  pop.append(h('p', {}, h('span', { class: `tag ${note.severity}` }, note.severity), ' ', note.reason));
  if (canReplace) {
    pop.append(h('button', { class: 'btn opt', onclick: () => replaceWith(note.primary) }, `Model 1 read: “${note.primary}” (keep)`));
    if (note.secondary) pop.append(h('button', { class: 'btn opt', onclick: () => replaceWith(note.secondary) }, `Model 2 read: “${note.secondary}” (use this)`));
    else pop.append(h('p', {}, 'Model 2 has nothing here.'));
  } else {
    pop.append(h('p', {}, `Model 1: “${note.primary || ''}”   Model 2: “${note.secondary || ''}”`));
    pop.append(h('button', { class: 'btn opt', onclick: () => settle(text) }, 'The line is right as it is'));
  }
  const custom = h('input', { type: 'text', placeholder: 'or type the word', 'aria-label': 'Your own reading' });
  custom.addEventListener('keydown', (e) => { if (e.key === 'Enter' && canReplace && custom.value) replaceWith(custom.value); });
  pop.append(h('div', { class: 'row' }, canReplace ? [custom, h('button', { class: 'btn small', onclick: () => custom.value && replaceWith(custom.value) }, 'Use')] : null,
    h('button', { class: 'btn small', onclick: () => { closePopover(); editLine(row, id); } }, 'Edit the line')));
  pop.hidden = false;
  const r = anchor.getBoundingClientRect();
  pop.style.left = `${Math.max(8, Math.min(window.scrollX + r.left, window.scrollX + document.documentElement.clientWidth - pop.offsetWidth - 8))}px`;
  pop.style.top = `${window.scrollY + r.bottom + 6}px`;
}

function closePopover() { const p = document.getElementById('popover'); if (p) p.hidden = true; }
document.addEventListener('mousedown', (e) => {
  const p = document.getElementById('popover');
  if (p && !p.hidden && !p.contains(e.target) && !e.target.closest('mark, .flagbtn')) closePopover();
});
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closePopover(); });

// what the server needs: the text as it is now, and only the doubts nobody settled
function buildTranscript() {
  const t = clone(S.transcript);
  t.lines = { ...S.text };
  const checked = new Set();
  for (const id of Object.keys(t.uncertain)) {
    const keep = [];
    t.uncertain[id].forEach((n, i) => {
      const settled = S.reviewed.has(id) || (S.resolved[id] && S.resolved[id].has(i));
      if (n.kind == null) { if (!S.pageChecked[pageOf(id)] && !S.reviewed.has(id)) keep.push(n); else checked.add(id); }
      else if (!settled) keep.push(n); else checked.add(id);
    });
    if (keep.length) t.uncertain[id] = keep; else delete t.uncertain[id];
  }
  for (const id of S.reviewed) checked.add(id);
  t.human_checked = [...checked];
  return t;
}

async function confirmText() {
  if (S.recipeDirty && !confirm('Building the recipe again discards the changes you made to it. Continue?')) return;
  S.error = null; S.info = null; S.busy = 'The second model is turning the text into a recipe…'; render();
  try {
    const t = buildTranscript();
    const res = await postJson('/api/structure', { transcript: t, edits: {} });
    S.sent = res.transcript; S.recipe = res.recipe; S.issues = res.issues; S.recipeDirty = false; S.manip = null;
    S.checkedIssues = new Set();
    S.maxStep = Math.max(S.maxStep, 2); S.step = 2;
    S.servings = ''; S.base = '';
  } catch (e) { S.error = e.message; }
  S.busy = null; render();
}

// ---------------------------------------------------------------- step 2: the recipe
const issueKey = (i) => `${i.code}|${i.refers_to || ''}|${i.message}`;
const visibleIssues = () => S.issues.filter((i) => !S.checkedIssues.has(issueKey(i)));

function issuesByRef() {
  const map = {};
  for (const i of visibleIssues()) (map[i.refers_to || ''] = map[i.refers_to || ''] || []).push(i);
  return map;
}

function checkIssue(i) { keepScroll(() => S.checkedIssues.add(issueKey(i))); }
function resetIssues() { keepScroll(() => { S.checkedIssues = new Set(); }); }

function checkButton(i) {
  return h('button', { class: 'btn small', style: "margin-left:8px", title: 'I checked this: it is right',
                       onclick: (e) => { e.stopPropagation(); checkIssue(i); } }, '✓ Check');
}

function worstOf(list) { return (list || []).reduce((w, i) => (w == null || rank(i.severity) < rank(w) ? i.severity : w), null); }

function itemBox(ref, issues, ...content) {
  const list = issues[ref] || [];
  return h('div', { class: `item ${worstOf(list) || ''}` }, content,
    list.length ? h('ul', { class: 'msgs' }, list.map((i) => h('li', {}, h('span', { class: `tag ${i.severity}` }, i.severity), ' ', i.message, checkButton(i)))) : null);
}

const quoteLine = (x) => h('div', { class: 'quote' }, x.quote ? ['“', h('code', {}, x.quote), '” '] : 'no quote ', x.lines && x.lines.length ? `(${x.lines.join(', ')})` : '');

function recipeStep() {
  if (S.busy) return busyView(S.busy);
  const r = S.recipe, issues = issuesByRef();
  const shown = visibleIssues();
  const hidden = S.issues.length - shown.length;
  const counts = Object.fromEntries(SEVERITIES.map((s) => [s, shown.filter((i) => i.severity === s).length]));

  const ingredient = (ing) => itemBox(ing.id, issues,
    h('div', { class: 'fields' },
      field('amount', h('input', { type: 'number', step: 'any', value: ing.quantity == null ? '' : ing.quantity, 'aria-label': `Amount of ${ing.name}`,
        oninput: (e) => { ing.quantity = e.target.value === '' ? null : parseFloat(e.target.value); dirty(); } })),
      field('unit', h('input', { type: 'text', value: ing.unit || '', 'aria-label': `Unit of ${ing.name}`, oninput: (e) => { ing.unit = e.target.value || null; dirty(); } })),
      field('amount as written', h('input', { type: 'text', value: ing.quantity_text || '', 'aria-label': `Amount as written for ${ing.name}`,
        oninput: (e) => { ing.quantity_text = e.target.value || null; dirty(); } })),
      field('ingredient', h('input', { type: 'text', value: ing.name, 'aria-label': 'Ingredient name', oninput: (e) => { ing.name = e.target.value; dirty(); } })),
      h('label', { class: 'small' }, h('input', { type: 'checkbox', checked: ing.optional, onchange: (e) => { ing.optional = e.target.checked; dirty(); } }), ' optional')),
    quoteLine(ing));

  const tool = (t) => itemBox(t.id, issues,
    h('div', { class: 'fields', style: 'grid-template-columns:1fr auto' },
      field('tool' + (t.implied ? ' (implied by the text, not named)' : ''), h('input', { type: 'text', value: t.name, 'aria-label': 'Tool name', oninput: (e) => { t.name = e.target.value; dirty(); } }))),
    quoteLine(t));

  const step = (s) => itemBox(`step ${s.n}`, issues,
    h('label', { class: 'small' }, `Step ${s.n}`),
    h('textarea', { 'aria-label': `Step ${s.n}`, value: s.text, oninput: (e) => { s.text = e.target.value; dirty(); } }),
    s.duration_text || s.condition ? h('div', { class: 'quote' }, [s.duration_text && `time: ${s.duration_text}`, s.condition && `condition: ${s.condition}`].filter(Boolean).join(' · ')) : null,
    quoteLine(s));

  const sv = r.servings;
  const servings = itemBox('servings', issues,
    h('div', { class: 'servings' },
      h('b', {}, 'Servings'), sv.value != null ? h('span', {}, String(sv.value)) : h('span', { class: 'empty' }, 'unknown'),
      sv.stated ? h('span', { class: 'chip low' }, 'stated in the text') : h('span', { class: 'estimate' }, 'estimate by the model: not in the text'),
      h('span', { class: 'hint', style: 'margin:0' }, 'You can set the real number in the next step.')),
    sv.stated ? quoteLine(sv) : h('div', { class: 'quote' }, sv.basis || ''));

  const side = h('aside', { class: 'panel sidepanel' }, h('h2', {}, 'Doubts'),
    h('p', { class: 'hint' }, 'Found by plain code checking the recipe against the text, not by a model.'),
    h('div', { style: 'margin-bottom:10px' }, SEVERITIES.map((s) => h('span', { class: `chip ${s}`, style: 'margin-right:6px' }, `${counts[s]} ${s}`))),
    h('div', { class: 'actions', style: 'margin:0 0 10px' }, resetButton(resetIssues, hidden === 0),
      hidden ? h('span', { class: 'hint', style: 'margin:0' }, `${hidden} checked`) : null),
    shown.length ? shown.map((i) => h('div', { class: `issue ${i.severity}` }, h('b', {}, `${i.severity} · ${i.code}${i.refers_to ? ' · ' + i.refers_to : ''}`), i.message,
      h('div', { style: 'margin-top:4px' }, checkButton(i))))
      : h('p', { class: 'empty' }, S.issues.length ? 'Everything is checked.' : 'Nothing to check.'));

  return h('div', {},
    h('div', { class: 'panel' }, h('h2', {}, r.title || 'The recipe'),
      h('p', { class: 'hint' }, 'Check each value against the quote under it. Fix what is wrong, then press Re-check: your changes are checked against the text again.'),
      legend()),
    h('div', { class: 'layout2' },
      h('div', { class: 'panel' },
        servings,
        h('h3', { class: 'sec' }, `Ingredients (${r.ingredients.length})`), r.ingredients.map(ingredient),
        h('h3', { class: 'sec' }, `Tools (${r.tools.length})`), r.tools.length ? r.tools.map(tool) : h('p', { class: 'empty' }, 'No tools.'),
        h('h3', { class: 'sec' }, `Steps (${r.steps.length})`), r.steps.map(step)),
      side),
    h('div', { class: 'actions' },
      h('button', { class: 'btn', onclick: recheck }, 'Re-check my changes'),
      h('button', { class: 'btn', onclick: () => openPreview(false) }, 'Preview recipe'),
      h('button', { class: 'btn primary', onclick: () => { S.maxStep = Math.max(S.maxStep, 3); S.step = 3; S.error = null; render(); } }, 'The recipe is OK → change it')));
}

function field(label, control) { return h('div', {}, h('label', { class: 'small' }, label), control); }
function dirty() { S.recipeDirty = true; S.manip = null; }  // a change after the manipulations makes their result out of date

async function recheck() {
  S.error = null; S.busy = 'Checking the recipe against the text…'; render();
  try {
    const res = await postJson('/api/check', { transcript: S.sent, recipe: S.recipe });
    S.recipe = res.recipe; S.issues = res.issues;
  } catch (e) { S.error = e.message; }
  S.busy = null; render();
}

// ---------------------------------------------------------------- step 3: change the recipe
function manipulateStep() {
  if (S.busy) return busyView(S.busy);
  const r = S.recipe, sv = r.servings;
  if (S.servings === '' && sv.value) S.servings = String(sv.value);
  const baseNote = S.base !== '' ? 'set by you' : sv.value == null ? 'unknown: type it' : sv.stated ? 'stated in the text' : 'estimated by the model';

  const scale = h('div', { class: 'choice' },
    h('label', {}, h('input', { type: 'checkbox', checked: S.scaleOn, onchange: (e) => { S.scaleOn = e.target.checked; } }), ' ', h('b', {}, 'Change the number of servings')),
    h('span', {}, 'to ', h('input', { type: 'number', min: '1', value: S.servings, 'aria-label': 'New number of servings', oninput: (e) => { S.servings = e.target.value; } })),
    h('span', {}, 'the recipe makes ', h('input', { type: 'number', min: '1', placeholder: sv.value == null ? '?' : String(sv.value), value: S.base, 'aria-label': 'Servings the recipe makes now',
      oninput: (e) => { S.base = e.target.value; } }), ' ',
      h('span', { class: sv.stated || S.base !== '' ? 'hint' : 'estimate' }, `(${baseNote})`)));
  const vegan = h('div', { class: 'choice' },
    h('label', {}, h('input', { type: 'checkbox', checked: S.veganOn, onchange: (e) => { S.veganOn = e.target.checked; } }), ' ', h('b', {}, 'Make it vegan')),
    h('span', { class: 'hint', style: 'margin:0' }, 'animal ingredients get plant-based stand-ins and the steps are rewritten (a model writes the words, the program checks them)'));

  const form = h('div', { class: 'panel' }, h('h2', {}, 'What should change?'),
    h('p', { class: 'hint' }, 'Choose what to change. Everything the program does is listed afterwards, with the reason.'),
    scale, vegan,
    h('h3', { class: 'sec' }, 'Order'),
    h('ol', { class: 'order' }, h('li', {}, 'Make it vegan'), h('li', {}, 'Change the servings')),
    h('p', { class: 'hint' }, 'The order is fixed. Making a recipe vegan keeps each ingredient’s kind (a seasoning stays a seasoning), so the result is the same whichever you ask for first.'),
    h('div', { class: 'actions' }, h('button', { class: 'btn primary', onclick: applyManip }, 'Apply'),
      h('button', { class: 'btn', onclick: () => openPreview(true) }, 'Preview recipe')));

  return h('div', {}, form, S.manip ? manipResult() : null);
}

async function applyManip() {
  S.error = null;
  if (!S.scaleOn && !S.veganOn) { S.error = 'Choose something to change.'; render(); return; }
  let servings = null, base = null;
  if (S.scaleOn) {
    servings = parseInt(S.servings, 10);
    if (!(servings > 0)) { S.error = 'Type the number of servings you want.'; render(); return; }
    base = S.base === '' ? null : parseInt(S.base, 10);
  }
  S.busy = S.veganOn ? 'The model is rewriting the steps, and the program is checking them…' : 'Changing the recipe…'; render();
  try { S.manip = await postJson('/api/manipulate', { recipe: S.recipe, servings, base_servings: base, vegan: S.veganOn }); }
  catch (e) { S.error = e.message; S.manip = null; }
  S.busy = null; render();
}

function amountText(i) {
  if (i.quantity == null) return i.quantity_text || '';
  return `${fmt(i.quantity)}${i.unit ? ' ' + i.unit : ''}`;
}

function manipResult() {
  const m = S.manip, before = S.recipe;
  const changed = new Set(m.changes.filter((c) => c.kind === 'changed').map((c) => c.target));
  const table = (recipe, mark) => h('table', { class: 'amounts' }, recipe.ingredients.map((i) =>
    h('tr', { class: mark && changed.has(i.id) ? 'changed' : '' }, h('td', { class: 'q' }, amountText(i)), h('td', {}, i.name, i.optional ? ' (optional)' : ''))));
  const steps = (recipe, mark) => h('ol', { class: 'steps' }, recipe.steps.map((s) => h('li', { class: mark && changed.has(`step ${s.n}`) ? 'changed' : '' }, s.text)));

  const order = { warning: 0, changed: 1, unchanged: 2 };
  const log = [...m.changes].sort((a, b) => order[a.kind] - order[b.kind]);
  const counts = { changed: 0, unchanged: 0, warning: 0 };
  log.forEach((c) => { counts[c.kind] += 1; });
  const logItem = (c) => h('li', { class: c.kind },
    c.severity ? h('span', { class: `tag ${c.severity}`, style: 'margin-right:6px' }, c.severity) : null,
    h('b', {}, `${c.kind === 'warning' ? 'Check: ' : ''}${c.target} · ${c.field}`),
    c.kind === 'changed' && c.field !== 'text' ? h('span', { class: 'arrow' }, `  ${c.before} → ${c.after}`) : null,
    h('span', { class: 'why' }, c.reason));

  return h('div', {},
    m.base_is_estimate ? banner('warn', `The amounts rest on an estimate of ${m.base_servings} servings made by the model, not on the text. Set the real number above and apply again if you know it.`) : null,
    h('div', { class: 'panel' },
      h('h2', {}, [m.rules.includes('make_vegan') ? 'Vegan' : null, m.target_servings != null ? `${m.base_servings} → ${m.target_servings} servings` : null].filter(Boolean).join(' · ')),
      h('div', { class: 'compare', style: 'margin-top:12px' },
        h('div', {}, h('h3', { class: 'sec' }, 'Original' + (m.base_servings != null ? ` (${m.base_servings})` : '')), table(before, false), h('h3', { class: 'sec' }, 'Steps'), steps(before, false)),
        h('div', {}, h('h3', { class: 'sec' }, 'New' + (m.target_servings != null ? ` (${m.target_servings})` : '')), table(m.recipe, true), h('h3', { class: 'sec' }, 'Steps'), steps(m.recipe, true))),
      h('div', { class: 'actions' },
        h('button', { class: 'btn primary', onclick: () => openPreview(true) }, 'Preview recipe'),
        h('button', { class: 'btn', onclick: () => downloadMarkdown(true) }, 'Download as Markdown'),
        h('button', { class: 'btn', onclick: download }, 'Download as JSON'))),
    h('div', { class: 'panel' },
      h('h2', {}, 'What was done and why'),
      h('p', { class: 'hint' }, `${counts.changed} changed, ${counts.unchanged} left as they are, ${counts.warning} to check by hand.`),
      h('ul', { class: 'log' }, log.map(logItem))));
}

function download() {
  const m = S.manip;
  const blob = new Blob([JSON.stringify({ recipe: m.recipe, changes: m.changes, base_servings: m.base_servings, target_servings: m.target_servings, base_is_estimate: m.base_is_estimate }, null, 2)], { type: 'application/json' });
  const a = h('a', { href: URL.createObjectURL(blob), download: `recipe${m.rules.includes('make_vegan') ? '.vegan' : ''}${m.target_servings != null ? '.servings' + m.target_servings : ''}.json` });
  document.body.append(a); a.click(); a.remove();
}

// ---------------------------------------------------------------- the cook's recipe: preview and download
// The Markdown is written by the server (one renderer for the page, the command line and the examples).
// It only uses headings, lists, **bold**, _italic_ and \ escapes, and this is all the page has to read.
// Everything is put on the page as text nodes, never as HTML.
function findClose(text, mark, from) {
  for (let j = from; j < text.length; j++) {
    if (text[j] === '\\') { j++; continue; }
    if (text.startsWith(mark, j)) return j;
  }
  return -1;
}

function inline(text) {
  const nodes = [];
  let buf = '';
  const flush = () => { if (buf) { nodes.push(buf); buf = ''; } };
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (ch === '\\' && i + 1 < text.length) { buf += text[++i]; continue; }
    if (text.startsWith('**', i)) {
      const end = findClose(text, '**', i + 2);
      if (end > 0) { flush(); nodes.push(h('strong', {}, inline(text.slice(i + 2, end)))); i = end + 1; continue; }
    }
    if (ch === '_') {
      const end = findClose(text, '_', i + 1);
      if (end > 0) { flush(); nodes.push(h('em', {}, inline(text.slice(i + 1, end)))); i = end; continue; }
    }
    buf += ch;
  }
  flush();
  return nodes;
}

function renderMarkdown(md) {
  const out = [];
  let list = null;
  const listOf = (tag) => { if (!list || list.tagName !== tag) { list = h(tag.toLowerCase()); out.push(list); } return list; };
  for (const line of md.split('\n')) {
    let m;
    if ((m = /^(#{1,2}) (.*)$/.exec(line))) { list = null; out.push(h(m[1].length === 1 ? 'h1' : 'h2', {}, inline(m[2]))); }
    else if ((m = /^- (.*)$/.exec(line))) listOf('UL').append(h('li', {}, inline(m[1])));
    else if ((m = /^(\d+)\. (.*)$/.exec(line))) listOf('OL').append(h('li', { value: m[1] }, inline(m[2])));
    else if (line.trim() === '') list = null;
    else { list = null; out.push(h('p', {}, inline(line))); }
  }
  return out;
}

// the recipe as it is now: the manipulated one if they were applied (and asked for), else the edited one
async function renderCurrent(useChanges) {
  const done = useChanges && S.manip ? S.manip : null;
  const recipe = done ? done.recipe : S.recipe;
  const res = await postJson('/api/render', { recipe, issues: visibleIssues(), changes: done ? done.changes : [] });
  return { markdown: res.markdown, title: recipe.title };
}

async function openPreview(useChanges) {
  S.error = null;
  let shown;
  try { shown = await renderCurrent(useChanges); }
  catch (e) { S.error = e.message; render(); return; }
  let dlg = document.getElementById('preview');
  if (!dlg) {
    dlg = h('dialog', { id: 'preview', class: 'preview', 'aria-label': 'The recipe, as a cook would read it' });
    dlg.addEventListener('click', (e) => { if (e.target === dlg) dlg.close(); });  // a click on the backdrop
    document.body.append(dlg);
  }
  dlg.replaceChildren(
    h('div', { class: 'preview-body' }, renderMarkdown(shown.markdown)),
    h('div', { class: 'actions' },
      h('button', { class: 'btn primary', onclick: () => saveFile(shown.markdown, shown.title, 'md', 'text/markdown') }, 'Download .md'),
      h('button', { class: 'btn', onclick: () => dlg.close() }, 'Close')));
  dlg.showModal();
}

async function downloadMarkdown(useChanges) {
  S.error = null;
  try { const shown = await renderCurrent(useChanges); saveFile(shown.markdown, shown.title, 'md', 'text/markdown'); }
  catch (e) { S.error = e.message; render(); }
}

function saveFile(text, title, extension, type) {
  const name = (title || 'recipe').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'recipe';
  const a = h('a', { href: URL.createObjectURL(new Blob([text], { type: `${type};charset=utf-8` })), download: `${name}.${extension}` });
  document.body.append(a); a.click(); a.remove();
}

render();
