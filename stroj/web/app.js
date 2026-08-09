'use strict';

/* ------------------------------------------------------------------ utils */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const ESCAPES = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ESCAPES[c]);

class ApiError extends Error {
  constructor(message, status) { super(message); this.status = status; }
}

async function api(path, { method = 'GET', body, form } = {}) {
  const opts = { method, headers: {}, credentials: 'same-origin' };
  if (form) opts.body = form;
  else if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { /* not JSON */ }
  if (!res.ok) {
    let detail = data && data.detail;
    if (Array.isArray(detail)) detail = detail.map((d) => d.msg || JSON.stringify(d)).join('; ');
    throw new ApiError(detail || `${res.status} ${res.statusText}`, res.status);
  }
  return data;
}

function toast(message, kind = '') {
  const node = document.createElement('div');
  node.className = `toast ${kind}`;
  node.textContent = message;
  $('#toasts').appendChild(node);
  setTimeout(() => node.remove(), 4200);
}

/* Timestamps come back as ISO-8601 UTC. */
const parseTime = (s) => (s ? new Date(s.endsWith('Z') ? s : s + 'Z') : null);

function relative(iso) {
  const then = parseTime(iso);
  if (!then) return '—';
  const seconds = Math.round((Date.now() - then.getTime()) / 1000);
  if (seconds < 45) return 'just now';
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)}h ago`;
  if (seconds < 86400 * 30) return `${Math.round(seconds / 86400)}d ago`;
  return then.toLocaleDateString();
}

const absolute = (iso) => (parseTime(iso) ? parseTime(iso).toLocaleString() : '');

/** A stored UTC timestamp as the local wall-clock string `datetime-local` wants. */
function localField(iso) {
  const at = parseTime(iso);
  if (!at) return '';
  return new Date(at.getTime() - at.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}

function duration(ms) {
  if (ms <= 0) return '00:00:00';
  const total = Math.floor(ms / 1000);
  const pad = (n) => String(n).padStart(2, '0');
  return `${pad(Math.floor(total / 3600))}:${pad(Math.floor(total / 60) % 60)}:${pad(total % 60)}`;
}

/**
 * Which section of a subtask-grouped test table is executing right now.
 *
 * A subtask problem awards partial credit, so every test runs, in order:
 * samples first, then subtask 1, 2, … The test now running is therefore
 * `done + 1` in that order, and it belongs to the first section not yet full.
 * Returns 0 for the samples, a subtask index, or null once nothing is left.
 */
function runningSection(done, total, groups) {
  const grouped = groups.reduce((n, g) => n + g.tests, 0);
  let ahead = done;
  const samples = Math.max(0, total - grouped);
  if (ahead < samples) return 0;
  ahead -= samples;
  for (const group of groups) {
    if (ahead < group.tests) return group.idx;
    ahead -= group.tests;
  }
  return null;
}

const verdictBadge = (v, name) => `<span class="badge v-${esc(v)}">${esc(name || v)}</span>`;

/** One renderer for every username in the app, so admins are marked
 *  consistently rather than in whichever views happened to remember. */
function userLink(username, role) {
  if (!username) return '<span class="muted">—</span>';
  const admin = role === 'admin' ? ' user-admin' : '';
  return `<a class="user-link${admin}" href="#/user/${encodeURIComponent(username)}">${esc(username)}</a>`;
}

const pointsPill = (points) => `<span class="points-pill">${Number(points) || 0}</span>`;

const typePills = (types) => ((types || []).length
  ? types.map((t) => `<span class="pill">${esc(t)}</span>`).join(' ')
  : '<span class="muted">—</span>');

/* Multiselect of problem types, as toggleable chips. Assigning types and
 * filtering by them are the same control; only authoring passes `creatable`,
 * which adds a field for types nothing uses yet. */
function typeChips(id, all, selected = [], creatable = false) {
  const chip = (t) => `<button type="button" class="chip${selected.includes(t) ? ' on' : ''}"
    data-type="${esc(t)}">${esc(t)}</button>`;
  return `<div class="chips" id="${id}">${all.map(chip).join('')}
    ${creatable ? '<input class="chip-add" placeholder="+ type">' : ''}</div>`;
}

const chosenTypes = (id) => $$(`#${id} .chip.on`).map((c) => c.dataset.type);

function bindTypeChips(id, onChange = () => {}) {
  const root = $(`#${id}`);
  root.onclick = (event) => {
    const chip = event.target.closest('.chip');
    if (!chip) return;
    chip.classList.toggle('on');
    onChange();
  };
  const add = $('.chip-add', root);
  if (!add) return;
  add.onkeydown = (event) => {
    if (event.key !== 'Enter' && event.key !== ',') return;
    event.preventDefault();
    const value = add.value.trim().toLowerCase();
    add.value = '';
    if (!value) return;
    const existing = $$('.chip', root).find((c) => c.dataset.type === value);
    if (existing) existing.classList.add('on');
    else add.insertAdjacentHTML('beforebegin',
      `<button type="button" class="chip on" data-type="${esc(value)}">${esc(value)}</button>`);
    onChange();
  };
}

function memory(kb) {
  if (!kb) return '—';
  return kb >= 1024 ? `${(kb / 1024).toFixed(1)} MiB` : `${kb} KiB`;
}

/* ------------------------------------------------------- tiny markdown */

function inlineMarkdown(text) {
  const codes = [];
  let out = text.replace(/`([^`]+)`/g, (_, code) => {
    codes.push(code);
    return `\u0000${codes.length - 1}\u0000`;
  });

  // Math comes out next, before the emphasis rules run: `$a_1$` and `$a*b$`
  // are full of characters markdown would otherwise read as formatting.
  // Extracting after code spans keeps `` `$x$` `` literal, which is how you
  // write about the syntax itself. A lone `\$` stays a dollar sign.
  const maths = [];
  const hold = (src, display) => {
    maths.push(renderMath(src, display));
    return `\u0001${maths.length - 1}\u0001`;
  };
  // No space just inside the delimiters, and no digit just after the closing
  // one — otherwise "costs $5 and $10" reads as one math span.
  out = out
    .replace(/\$\$([^$]+)\$\$/g, (_, src) => hold(src, true))
    .replace(/(^|[^\\$])\$(?!\s)([^$\n]*[^\s$])\$(?!\d)/g,
             (_, before, src) => before + hold(src, false));

  out = out
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[^*])\*([^*]+)\*/g, '$1<em>$2</em>')
    .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, '<a href="$2" rel="noopener noreferrer">$1</a>')
    .replace(/\\\$/g, '$');
  return out
    .replace(/\u0001(\d+)\u0001/g, (_, i) => maths[i])
    .replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${codes[i]}</code>`);
}

/** A deliberately small Markdown subset: headings, lists, code, emphasis. */
function markdown(source) {
  // Strip NULs so statement text can never forge a code-span sentinel.
  const lines = esc((source || '').replace(/[\u0000\u0001]/g, ''))
    .replace(/\r\n/g, '\n').split('\n');
  const html = [];
  let paragraph = [];
  let list = null;
  let fence = null;

  const flushParagraph = () => {
    if (paragraph.length) {
      html.push(`<p>${inlineMarkdown(paragraph.join(' '))}</p>`);
      paragraph = [];
    }
  };
  const flushList = () => {
    if (list) { html.push(`</${list}>`); list = null; }
  };

  let mathBlock = null;

  for (const line of lines) {
    if (fence !== null) {
      if (/^\s*```/.test(line)) { html.push(`<pre><code>${fence.join('\n')}</code></pre>`); fence = null; }
      else fence.push(line);
      continue;
    }
    // A `$$` on its own line opens display math, which may run over several
    // lines and must not end up inside a <p>.
    if (mathBlock !== null) {
      if (/^\s*\$\$\s*$/.test(line)) {
        html.push(renderMath(mathBlock.join(' '), true));
        mathBlock = null;
      } else mathBlock.push(line);
      continue;
    }
    if (/^\s*\$\$\s*$/.test(line)) { flushParagraph(); flushList(); mathBlock = []; continue; }
    if (/^\s*```/.test(line)) { flushParagraph(); flushList(); fence = []; continue; }

    const heading = line.match(/^(#{1,4})\s+(.*)$/);
    if (heading) {
      flushParagraph(); flushList();
      const level = Math.min(heading[1].length + 1, 4);
      html.push(`<h${level}>${inlineMarkdown(heading[2])}</h${level}>`);
      continue;
    }
    const bullet = line.match(/^\s*[-*+]\s+(.*)$/);
    const numbered = line.match(/^\s*\d+[.)]\s+(.*)$/);
    if (bullet || numbered) {
      flushParagraph();
      const want = bullet ? 'ul' : 'ol';
      if (list !== want) { flushList(); html.push(`<${want}>`); list = want; }
      html.push(`<li>${inlineMarkdown((bullet || numbered)[1])}</li>`);
      continue;
    }
    if (!line.trim()) { flushParagraph(); flushList(); continue; }
    flushList();
    paragraph.push(line.trim());
  }
  if (fence !== null) html.push(`<pre><code>${fence.join('\n')}</code></pre>`);
  if (mathBlock !== null) html.push(renderMath(mathBlock.join(' '), true));
  flushParagraph();
  flushList();
  return html.join('\n');
}

/* ------------------------------------------------------------------ state */

const state = {
  user: null,
  languages: [],
  defaultLanguage: 'cpp',
  config: {},
  clockSkewMs: 0,       // server time minus browser time
};

let timers = [];
function clearTimers() {
  timers.forEach((t) => clearInterval(t));
  timers = [];
}
function every(ms, fn) { timers.push(setInterval(fn, ms)); }

/* ------------------------------------------------------------------- auth */

function renderAccount() {
  const box = $('#account');
  if (state.user) {
    // Same renderer as everywhere else, so the header links to the profile and
    // marks admins identically rather than having its own private styling.
    box.innerHTML = `
      ${userLink(state.user.username, state.user.role)}
      <button class="small ghost" id="logout">Sign out</button>`;
    $('#logout').onclick = async () => {
      await api('/api/auth/logout', { method: 'POST' });
      state.user = null;
      renderAccount();
      route();
      toast('Signed out.');
    };
  } else {
    const canRegister = state.config.registration !== 'closed';
    box.innerHTML = `
      ${canRegister ? '<button class="small ghost" id="show-register">Register</button>' : ''}
      <button class="small primary" id="show-login">Sign in</button>`;
    $('#show-login').onclick = () => openAuth('login');
    if (canRegister) $('#show-register').onclick = () => openAuth('register');
  }
  $$('.admin-only').forEach((node) => {
    node.style.display = state.user && state.user.is_admin ? '' : 'none';
  });
}

function openAuth(mode) {
  const dialog = $('#auth-dialog');
  const form = $('#auth-form');
  const isLogin = mode === 'login';
  $('#auth-title').textContent = isLogin ? 'Sign in' : 'Create an account';
  $('#auth-submit').textContent = isLogin ? 'Sign in' : 'Register';
  $('#auth-error').hidden = true;
  form.password.autocomplete = isLogin ? 'current-password' : 'new-password';

  const needsInvite = !isLogin && state.config.registration === 'invite';
  $('#invite-field').hidden = !needsInvite;
  form.invite.required = needsInvite;
  const closed = state.config.registration === 'closed';
  $('#auth-switch').innerHTML = isLogin
    ? (closed
      ? '<span class="muted">Registration is closed — ask an organiser for an account.</span>'
      : 'No account yet? <a href="#" data-switch="register">Register</a>')
    : 'Already registered? <a href="#" data-switch="login">Sign in</a>';
  const switcher = $('#auth-switch').querySelector('a');
  if (switcher) {
    switcher.onclick = (e) => {
      e.preventDefault();
      dialog.close();
      openAuth(e.target.dataset.switch);
    };
  }

  form.onsubmit = async (event) => {
    if (event.submitter && event.submitter.value === 'cancel') return;
    event.preventDefault();
    const body = { username: form.username.value.trim(), password: form.password.value };
    if (needsInvite) body.invite = form.invite.value.trim();
    try {
      const result = await api(`/api/auth/${mode}`, { method: 'POST', body });
      state.user = result.user;
      dialog.close();
      form.reset();
      renderAccount();
      route();
      toast(`Welcome, ${result.user.username}.`, 'good');
    } catch (err) {
      const box = $('#auth-error');
      box.textContent = err.message;
      box.hidden = false;
    }
  };
  dialog.showModal();
}

function requireSignIn(message) {
  return `<div class="empty">${esc(message)}<br><br>
    <button class="primary" onclick="document.getElementById('show-login').click()">Sign in</button></div>`;
}

/* ------------------------------------------------------------------ views */

const view = () => $('#view');

function setView(html, { wide = false } = {}) {
  const main = view();
  main.classList.toggle('wide', wide);
  main.innerHTML = html;
}

/** Wire a Markdown textarea to a live preview, rendered by the same function
 *  that publishes it, with Tab inserting spaces instead of leaving the field. */
function markdownEditor(editor, preview) {
  const render = () => { preview.innerHTML = markdown(editor.value); };
  render();
  editor.oninput = render;
  editor.onkeydown = (event) => {
    if (event.key !== 'Tab') return;
    event.preventDefault();
    const { selectionStart: a, selectionEnd: b, value } = editor;
    editor.value = value.slice(0, a) + '    ' + value.slice(b);
    editor.selectionStart = editor.selectionEnd = a + 4;
    render();
  };
}

/* ---- stream ---- */

function postCard(p) {
  return `
    <article class="card post">
      <h2><a href="#/post/${encodeURIComponent(p.slug)}">${esc(p.title)}</a></h2>
      <div class="post-meta small muted">
        ${userLink(p.author, p.author_role)}
        <span title="${esc(absolute(p.created_at))}">posted ${esc(relative(p.created_at))}</span>
        ${p.updated_at !== p.created_at ? '<span>· edited</span>' : ''}
        ${p.pinned ? '<span class="pill">pinned</span>' : ''}
        ${p.published ? '' : '<span class="pill">draft</span>'}
        ${state.user && state.user.is_admin
          ? `<a class="pill" href="#/admin/post/${encodeURIComponent(p.slug)}">edit</a>` : ''}
      </div>
      <div class="statement">${markdown(p.body)}</div>
    </article>`;
}

async function viewHome() {
  const { posts } = await api('/api/posts');
  setView(`
    <div class="page-head">
      <h1>stroj</h1>
      <span class="muted small">news and announcements</span>
    </div>
    ${posts.length
      ? posts.map(postCard).join('')
      : `<div class="empty">Nothing posted yet.${state.user && state.user.is_admin
          ? ' Write one from the <a href="#/admin">admin page</a>.' : ''}</div>`}`);
}

async function viewPost(slug) {
  const p = await api(`/api/posts/${encodeURIComponent(slug)}`);
  setView(`<div class="page-head"><a href="#/home">← Stream</a></div>${postCard(p)}`);
}

/* ---- problems ---- */

async function viewProblems() {
  const { problems } = await api('/api/problems');
  if (!problems.length) {
    setView(`<div class="page-head"><h1>Problems</h1></div>
      <div class="empty">No problems yet. ${state.user && state.user.is_admin
        ? 'Add one from the <a href="#/admin">admin page</a>.'
        : 'Run <code class="mono">python -m stroj seed</code> to load the samples.'}</div>`);
    return;
  }
  const row = (p) => `
    <tr>
      <td class="wide">
        ${state.user ? `<span class="dot ${esc(p.status)}" title="${esc(p.status)}"></span>` : ''}
        <a href="#/problem/${encodeURIComponent(p.slug)}">${esc(p.title)}</a>
        ${p.visible ? '' : ' <span class="pill">hidden</span>'}
      </td>
      <td class="small">${typePills(p.types)}</td>
      <td class="num">${pointsPill(p.points)}</td>
      <td class="small">${p.author ? userLink(p.author) : '<span class="muted">—</span>'}</td>
      <td class="num">${p.time_limit_ms} ms</td>
      <td class="num">${p.memory_limit_mb} MiB</td>
      <td class="small muted">${esc(p.checker)}${p.partial ? ' · partial' : ''}</td>
    </tr>`;

  const types = [...new Set(problems.flatMap((p) => p.types))].sort();
  setView(`
    <div class="page-head"><h1>Problems</h1><span class="muted small" id="p-count"></span></div>
    <div class="filters">
      <div class="row">
        <input id="f-q" type="search" placeholder="Search problems…" style="flex:2;min-width:180px">
        <input id="f-min" type="number" min="0" placeholder="Min points" style="width:120px">
        <input id="f-max" type="number" min="0" placeholder="Max points" style="width:120px">
      </div>
      ${types.length ? `<div class="row" style="margin-top:8px">
        <span class="muted small">Types</span>${typeChips('f-types', types)}</div>` : ''}
    </div>
    <div class="table-wrap"><table>
      <thead><tr><th>Problem</th><th>Types</th><th class="num">Points</th><th>Author</th><th class="num">Time</th><th class="num">Memory</th><th>Checker</th></tr></thead>
      <tbody id="p-rows"></tbody>
    </table></div>`);

  const apply = () => {
    const q = $('#f-q').value.trim().toLowerCase();
    // No type selected means no type filter; several mean any of them.
    const chosen = types.length ? chosenTypes('f-types') : [];
    const min = Number($('#f-min').value) || 0;
    const max = Number($('#f-max').value) || Infinity;
    const shown = problems.filter((p) =>
      (!q || p.title.toLowerCase().includes(q) || p.slug.includes(q))
      && (!chosen.length || chosen.some((t) => p.types.includes(t)))
      && p.points >= min && p.points <= max);
    $('#p-rows').innerHTML = shown.map(row).join('')
      || '<tr><td colspan="7" class="muted">Nothing matches those filters.</td></tr>';
    $('#p-count').textContent = shown.length === problems.length
      ? `${problems.length} total` : `${shown.length} of ${problems.length}`;
  };
  $$('.filters input').forEach((el) => { el.oninput = apply; });
  if (types.length) bindTypeChips('f-types', apply);
  apply();
}

const draftKey = (slug, language) => `stroj:draft:${slug}:${language}`;

async function viewProblem(slug, params) {
  const contestSlug = params.get('contest');
  const problem = await api(`/api/problems/${encodeURIComponent(slug)}`);

  const samples = problem.samples.map((s, i) => `
    <div class="sample-grid">
      <div><h4>Input ${problem.samples.length > 1 ? i + 1 : ''}</h4><pre class="io">${esc(s.input)}</pre></div>
      <div><h4>Output ${problem.samples.length > 1 ? i + 1 : ''}</h4><pre class="io">${esc(s.output)}</pre></div>
    </div>`).join('');

  const languageOptions = state.languages.map((l) => `
    <option value="${esc(l.id)}" ${l.available ? '' : 'disabled'}>
      ${esc(l.name)}${l.available ? '' : ' — not installed'}
    </option>`).join('');

  setView(`
    <div class="page-head">
      <h1>${esc(problem.title)}</h1>
      ${contestSlug ? `<a class="pill" href="#/contest/${encodeURIComponent(contestSlug)}">← contest</a>` : ''}
    </div>
    <div class="row small muted" style="margin-bottom:18px">
      <span class="points-pill">${problem.points} points</span>
      ${problem.author ? `<span class="pill">by ${userLink(problem.author)}</span>` : ''}
      ${(problem.types || []).map((t) => `<span class="pill">${esc(t)}</span>`).join('')}
      <span class="pill">${problem.time_limit_ms} ms</span>
      <span class="pill">${problem.memory_limit_mb} MiB</span>
      <span class="pill">${esc(problem.checker)} checker</span>
      <span class="pill">${problem.test_count} tests</span>
      ${problem.partial ? '<span class="pill">partial scoring</span>' : ''}
    </div>

    <div class="grid-2">
      <div>
        <div class="card"><div class="statement">${markdown(problem.statement)}</div></div>
        ${(problem.subtasks || []).length ? `
          <div class="card">
            <h2 style="margin-top:0">Subtasks</h2>
            <p class="muted small">Solve every test in a subtask to earn its share
              of the ${problem.points} points. Partial credit counts toward your score.</p>
            <div class="table-wrap"><table><tbody>${problem.subtasks.map((st) => `
              <tr><td class="mono" style="width:1%">${st.idx}</td>
                  <td class="wide muted small">${st.tests} test${st.tests === 1 ? '' : 's'}</td>
                  <td class="num">${st.percent}%</td>
                  <td class="num">${pointsPill(Math.round(problem.points * st.percent / 100))}</td>
              </tr>`).join('')}</tbody></table></div>
          </div>` : (problem.partial ? `
          <div class="card">
            <h2 style="margin-top:0">Partial scoring</h2>
            <p class="muted small">No subtasks, so you earn the share of tests you
              pass — 10 of 20 tests earns half of the ${problem.points} points.</p>
          </div>` : '')}
        ${samples ? `<div class="card"><h2 style="margin-top:0">Samples</h2>${samples}</div>` : ''}
      </div>
      <div>
        <div class="card">
          <h2 style="margin-top:0">Submit</h2>
          ${state.user ? `
            <label>Language
              <select id="language">${languageOptions}</select>
            </label>
            <p class="small muted" id="lang-limits"></p>
            <label>Source
              <textarea class="code" id="source" spellcheck="false" autocapitalize="off" autocomplete="off"></textarea>
            </label>
            <div class="row end">
              <span class="muted small spacer" id="submit-hint">⌘/Ctrl + Enter to submit</span>
              <button class="primary" id="submit">Submit</button>
            </div>`
            : requireSignIn('Sign in to submit a solution.')}
        </div>
        <div class="card" id="my-subs"><h2 style="margin-top:0">Your submissions</h2>
          <div class="loading">Loading…</div></div>
      </div>
    </div>`);

  if (!state.user) return;

  const select = $('#language');
  const editor = $('#source');
  select.value = localStorage.getItem('stroj:language') || state.defaultLanguage;
  if (!select.value || select.selectedIndex < 0) select.value = state.defaultLanguage;

  const loadDraft = () => {
    const language = select.value;
    const saved = localStorage.getItem(draftKey(slug, language));
    const template = (state.languages.find((l) => l.id === language) || {}).template || '';
    editor.value = saved !== null ? saved : template;
    const limits = problem.limits[language];
    $('#lang-limits').textContent = limits
      ? `Effective limits for this language: ${limits.time_limit_ms} ms, ${limits.memory_limit_mb} MiB.`
      : '';
  };
  loadDraft();

  select.onchange = () => {
    localStorage.setItem('stroj:language', select.value);
    loadDraft();
  };
  editor.oninput = () => localStorage.setItem(draftKey(slug, select.value), editor.value);
  editor.onkeydown = (event) => {
    if (event.key === 'Tab') {
      event.preventDefault();
      const { selectionStart: a, selectionEnd: b, value } = editor;
      editor.value = value.slice(0, a) + '    ' + value.slice(b);
      editor.selectionStart = editor.selectionEnd = a + 4;
    } else if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      $('#submit').click();
    }
  };

  $('#submit').onclick = async () => {
    const button = $('#submit');
    button.disabled = true;
    try {
      const result = await api('/api/submissions', {
        method: 'POST',
        body: {
          problem: slug,
          language: select.value,
          source: editor.value,
          contest: contestSlug || null,
        },
      });
      toast(`Submission #${result.id} queued.`, 'good');
      location.hash = `#/submission/${result.id}`;
    } catch (err) {
      toast(err.message, 'bad');
    } finally {
      button.disabled = false;
    }
  };

  let lastMine = null;
  const refreshMine = async () => {
    const { submissions } = await api(
      `/api/submissions?mine=true&problem=${encodeURIComponent(slug)}&limit=10`);
    const box = $('#my-subs');
    if (!box) return;
    // Skip the DOM write when nothing moved, so the panel does not flicker.
    const fingerprint = JSON.stringify(submissions);
    if (fingerprint === lastMine) return;
    lastMine = fingerprint;
    box.innerHTML = `<h2 style="margin-top:0">Your submissions</h2>` + (submissions.length
      ? `<div class="table-wrap"><table><tbody>${submissions.map((s) => `
          <tr>
            <td><a href="#/submission/${s.id}">#${s.id}</a></td>
            <td>${verdictBadge(s.verdict, s.verdict_name)}</td>
            <td class="num">${s.score}/${s.max_score}</td>
            <td class="num muted">${s.time_ms} ms</td>
            <td class="muted small" title="${esc(absolute(s.created_at))}">${esc(relative(s.created_at))}</td>
          </tr>`).join('')}</tbody></table></div>`
      : '<p class="muted small">Nothing yet.</p>');
  };
  await refreshMine();
  every(2000, refreshMine);
}

/* ---- submissions ---- */

async function viewSubmissions(params) {
  const mine = params.get('mine') === '1';
  const query = new URLSearchParams({ limit: '60' });
  if (mine) query.set('mine', 'true');
  ['problem', 'contest', 'username'].forEach((k) => {
    if (params.get(k)) query.set(k, params.get(k));
  });

  const render = async () => {
    const { submissions } = await api(`/api/submissions?${query}`);
    const rows = submissions.map((s) => `
      <tr>
        <td><a href="#/submission/${s.id}">#${s.id}</a></td>
        <td>${userLink(s.username, s.user_role)}</td>
        <td class="wide"><a href="#/problem/${encodeURIComponent(s.problem_slug)}">${esc(s.problem_title)}</a>
          ${s.contest_slug ? `<a class="pill" href="#/contest/${encodeURIComponent(s.contest_slug)}">${esc(s.contest_slug)}</a>` : ''}</td>
        <td class="small muted">${esc(s.language)}</td>
        <td>${verdictBadge(s.verdict, s.verdict_name)}</td>
        <td class="num">${s.score}/${s.max_score}</td>
        <td class="num muted">${s.time_ms} ms</td>
        <td class="num muted">${memory(s.memory_kb)}</td>
        <td class="muted small" title="${esc(absolute(s.created_at))}">${esc(relative(s.created_at))}</td>
      </tr>`).join('');

    const table = `
      <div class="table-wrap"><table>
        <thead><tr><th>ID</th><th>Who</th><th>Problem</th><th>Lang</th><th>Verdict</th>
          <th class="num">Score</th><th class="num">Time</th><th class="num">Memory</th><th>When</th></tr></thead>
        <tbody>${rows}</tbody></table></div>`;

    const body = submissions.length ? table : '<div class="empty">No submissions yet.</div>';
    const existing = $('#sub-list');
    if (existing) existing.innerHTML = body;
    else setView(`
      <div class="page-head">
        <h1>Submissions</h1>
        <div class="spacer"></div>
        <div class="row">
          <a class="btn ${mine ? '' : 'primary'}" href="#/submissions">Everyone</a>
          <a class="btn ${mine ? 'primary' : ''}" href="#/submissions?mine=1">Mine</a>
        </div>
      </div>
      <div id="sub-list">${body}</div>`, { wide: true });
  };

  await render();
  every(4000, render);
}

async function viewSubmission(id) {
  let lastPainted = null;
  const render = async () => {
    const s = await api(`/api/submissions/${id}`);
    // Polling three times a second would otherwise rebuild the DOM — and lose
    // any text selection — even when no new test has finished.
    const fingerprint = JSON.stringify([s.verdict, s.score, s.time_ms,
      (s.tests || []).map((t) => [t.idx, t.verdict])]);
    if (fingerprint === lastPainted) return s.verdict === 'PENDING' || s.verdict === 'JUDGING';
    lastPainted = fingerprint;
    const running = s.verdict === 'PENDING' || s.verdict === 'JUDGING';
    const canAbort = running && state.user
      && (state.user.is_admin || state.user.username === s.username);

    const done = (s.tests || []).length;
    const total = s.test_count || 0;
    // Judging stops at the first failure unless the problem awards partial
    // credit, so "4 of 18" is only meaningful while nothing has failed yet.
    const stillPassing = (s.tests || []).every((t) => t.verdict === 'AC');
    const progress = running && total
      ? `<span class="muted small">${done}${stillPassing ? ` of ${total}` : ''} run</span>`
      : '';
    const runningRow = running
      ? `<tr><td class="num muted">${done + 1}</td>
           <td>${verdictBadge('JUDGING', 'Running')}</td>
           <td colspan="4" class="muted small">…</td></tr>`
      : '';

    const testRow = (t) => `
      <tr>
        <td class="num">${t.idx}</td>
        <td>${verdictBadge(t.verdict, t.verdict_name)}</td>
        <td class="num muted">${t.time_ms} ms</td>
        <td class="num muted">${memory(t.memory_kb)}</td>
        <td class="num muted">${t.points}</td>
        <td class="wide muted small mono">${esc(t.message)}</td>
      </tr>`;

    // With subtasks the run is only readable when it is grouped: a submission
    // that failed one group and passed another looks like noise as a flat list.
    const groups = s.subtasks || [];
    let tests;
    let runningPlaced = false;
    if (groups.length) {
      const rows = [];
      const of = (n) => (s.tests || []).filter((t) => (t.subtask || 0) === n);
      const samples = of(0);

      // A subtask problem awards partial credit, so every test runs, in order:
      // samples, then subtask 1, 2, … That makes the section now executing the
      // first one that is not yet full. Appending the running row to the table
      // instead would park it under the last subtask for the whole judge.
      const runningIn = running ? runningSection(done, total, groups) : null;
      const body = (mine, idx) => {
        const out = mine.map(testRow);
        if (runningIn === idx) { out.push(runningRow); runningPlaced = true; }
        else if (!mine.length) {
          out.push(`<tr><td colspan="6" class="muted small">not reached</td></tr>`);
        }
        return out;
      };

      if (samples.length || runningIn === 0) {
        rows.push(`<tr class="subtask-head"><td colspan="6">Samples
          <span class="muted small">— not scored</span></td></tr>`);
        rows.push(...body(samples, 0));
      }

      for (const group of groups) {
        const mine = of(group.idx);
        const failed = mine.some((t) => t.verdict !== 'AC');
        // Only claim a subtask once every one of its tests has actually run —
        // mid-judge, three passes out of five is not yet an earned subtask.
        const complete = mine.length === group.tests;
        const status = failed
          ? `<span class="badge v-WA">0%</span>`
          : complete
            ? `<span class="badge v-AC">${group.percent}%</span>`
            : `<span class="muted small">${mine.length}/${group.tests} run</span>`;
        rows.push(`<tr class="subtask-head"><td colspan="5">Subtask ${group.idx}
            <span class="muted small">— worth ${group.percent}%</span></td>
          <td class="num">${status}</td></tr>`);
        rows.push(...body(mine, group.idx));
      }
      tests = rows.join('');
    } else {
      tests = (s.tests || []).map(testRow).join('');
    }

    setView(`
      <div class="page-head">
        <h1>Submission #${s.id}</h1>
        ${verdictBadge(s.verdict, s.verdict_name)}
        <div class="spacer"></div>
        ${canAbort ? '<button class="small danger" id="abort-submission">Abort</button>' : ''}
        <a class="pill" href="#/problem/${encodeURIComponent(s.problem_slug)}">${esc(s.problem_title)}</a>
      </div>

      <div class="card">
        <div class="row small">
          <span class="pill">${esc(s.username || '')}</span>
          <span class="pill">${esc(s.language)}</span>
          <span class="pill">score ${s.score}/${s.max_score}</span>
          ${typeof s.earned_percent === 'number' && !running
            ? `<span class="points-pill">${s.earned_percent}% earned</span>` : ''}
          <span class="pill">${s.time_ms} ms</span>
          <span class="pill">${memory(s.memory_kb)}</span>
          <span class="muted" title="${esc(absolute(s.created_at))}">submitted ${esc(relative(s.created_at))}</span>
          ${s.contest_slug ? `<a class="pill" href="#/contest/${encodeURIComponent(s.contest_slug)}">${esc(s.contest_slug)}</a>` : ''}
        </div>
        ${s.message ? `<h3>Judge output</h3><pre class="io">${esc(s.message)}</pre>` : ''}
      </div>

      ${tests || running ? `<h2>Tests ${progress}</h2><div class="table-wrap"><table>
          <thead><tr><th class="num">#</th><th>Verdict</th><th class="num">Time</th>
            <th class="num">Memory</th><th class="num">Points</th><th>Detail</th></tr></thead>
          <tbody>${tests}${runningPlaced ? '' : runningRow}</tbody></table></div>` : ''}

      ${s.source !== undefined
        ? `<h2>Source</h2><pre class="source">${esc(s.source)}</pre>`
        : '<p class="muted small">Source is only visible to its author.</p>'}
    `, { wide: true });

    if (canAbort) {
      const button = $('#abort-submission');
      button.onclick = async () => {
        if (!confirm(`Abort submission #${s.id}? It will not be judged.`)) return;
        button.disabled = true;
        try {
          await api(`/api/submissions/${s.id}/abort`, { method: 'POST' });
          toast('Aborting…');
          lastPainted = null;   // force the next poll to repaint
        } catch (err) {
          toast(err.message, 'bad');
          button.disabled = false;
        }
      };
    }
    return running;
  };

  // Only poll while the verdict is still in flight; a judged submission is final.
  if (await render()) {
    every(700, async () => {
      if (!(await render())) clearTimers();
    });
  }
}

/* ---- contests ---- */

const STATE_LABEL = { before: 'Not started', running: 'Running', ended: 'Finished' };

async function viewContests() {
  const { contests, server_time } = await api('/api/contests');
  state.clockSkewMs = parseTime(server_time).getTime() - Date.now();
  if (!contests.length) {
    setView('<div class="page-head"><h1>Contests</h1></div><div class="empty">No contests scheduled.</div>');
    return;
  }
  const rows = contests.map((c) => `
    <tr>
      <td class="wide"><a href="#/contest/${encodeURIComponent(c.slug)}">${esc(c.title)}</a></td>
      <td><span class="badge state-${esc(c.state)}">${esc(STATE_LABEL[c.state])}</span></td>
      <td class="muted small">${esc(absolute(c.starts_at))}</td>
      <td class="muted small">${esc(absolute(c.ends_at))}</td>
      <td class="mono small muted">${esc(c.scoring.toUpperCase())}</td>
      <td class="countdown mono small" data-starts="${esc(c.starts_at)}" data-ends="${esc(c.ends_at)}" data-state="${esc(c.state)}"></td>
    </tr>`).join('');

  setView(`
    <div class="page-head"><h1>Contests</h1></div>
    <div class="table-wrap"><table>
      <thead><tr><th>Contest</th><th>Status</th><th>Starts</th><th>Ends</th><th>Scoring</th><th>Clock</th></tr></thead>
      <tbody>${rows}</tbody></table></div>`);

  const tick = () => $$('.countdown').forEach(updateCountdown);
  tick();
  every(1000, tick);
}

function updateCountdown(node) {
  const now = Date.now() + state.clockSkewMs;
  const starts = parseTime(node.dataset.starts).getTime();
  const ends = parseTime(node.dataset.ends).getTime();
  if (now < starts) node.textContent = `starts in ${duration(starts - now)}`;
  else if (now < ends) node.textContent = `${duration(ends - now)} left`;
  else node.textContent = 'finished';
}

async function viewContest(slug) {
  const c = await api(`/api/contests/${encodeURIComponent(slug)}`);
  state.clockSkewMs = parseTime(c.server_time).getTime() - Date.now();

  const problems = c.problems.map((p) => `
    <tr>
      <td class="mono" style="width:1%">${esc(p.label)}</td>
      <td class="wide">
        ${state.user ? `<span class="dot ${esc(p.status || 'untouched')}"></span>` : ''}
        <a href="#/problem/${encodeURIComponent(p.slug)}?contest=${encodeURIComponent(slug)}">${esc(p.title)}</a>
      </td>
      <td class="num">${p.time_limit_ms} ms</td>
      <td class="num">${p.memory_limit_mb} MiB</td>
    </tr>`).join('');

  setView(`
    <div class="page-head">
      <h1>${esc(c.title)}</h1>
      <span class="badge state-${esc(c.state)}">${esc(STATE_LABEL[c.state])}</span>
      <div class="spacer"></div>
      <span class="countdown" data-starts="${esc(c.starts_at)}" data-ends="${esc(c.ends_at)}"></span>
    </div>

    <div class="card">
      <div class="statement">${markdown(c.description)}</div>
      <div class="row small muted">
        <span class="pill">${esc(c.scoring.toUpperCase())} scoring</span>
        ${c.scoring === 'icpc' ? `<span class="pill">${c.penalty_minutes} min penalty</span>` : ''}
        <span class="pill">${esc(absolute(c.starts_at))} → ${esc(absolute(c.ends_at))}</span>
      </div>
    </div>

    <div class="row" style="margin-bottom:14px">
      <a class="btn primary" href="#/contest/${encodeURIComponent(slug)}/scoreboard">Scoreboard</a>
      <a class="btn" href="#/submissions?contest=${encodeURIComponent(slug)}">Submissions</a>
    </div>

    ${c.sealed
      ? '<div class="empty">The problem set is sealed until the contest starts.</div>'
      : (problems
        ? `<div class="table-wrap"><table>
             <thead><tr><th>#</th><th>Problem</th><th class="num">Time</th><th class="num">Memory</th></tr></thead>
             <tbody>${problems}</tbody></table></div>`
        : '<div class="empty">No problems have been added to this contest yet.</div>')}`);

  const tick = () => $$('.countdown').forEach(updateCountdown);
  tick();
  every(1000, tick);
}

async function viewScoreboard(slug) {
  const render = async () => {
    const board = await api(`/api/contests/${encodeURIComponent(slug)}/scoreboard`);
    const isIcpc = board.scoring === 'icpc';

    const headCells = board.problems.map((p) => `
      <th class="num" title="${esc(p.title)}">
        <a href="#/problem/${encodeURIComponent(p.slug)}?contest=${encodeURIComponent(slug)}">${esc(p.label)}</a>
        <div class="muted" style="font-weight:400">${p.solved_by}</div>
      </th>`).join('');

    const rows = board.rows.map((r) => {
      const cells = board.problems.map((p) => {
        const cell = r.cells[p.label];
        if (!cell || (!cell.attempts && !cell.pending && !cell.frozen)) {
          return '<td class="cell"></td>';
        }
        let cls = cell.solved ? 'solved' : (cell.pending ? 'pending' : 'failed');
        let main;
        let sub;
        if (isIcpc) {
          main = cell.solved ? `+${cell.attempts > 1 ? cell.attempts - 1 : ''}` : `−${cell.attempts}`;
          sub = cell.solved ? `${cell.minutes}` : '';
        } else {
          main = `${cell.score}`;
          sub = `${cell.attempts} sub`;
        }
        // Attempts made after the freeze are counted but not resolved — the
        // cell has to look unresolved, not empty and not failed.
        if (cell.frozen) {
          cls += ' frozen';
          sub = `+${cell.frozen} hidden`;
          if (!cell.attempts) main = '?';
        }
        return `<td class="cell ${cls}">${esc(main)}${sub ? `<span class="sub">${esc(sub)}</span>` : ''}</td>`;
      }).join('');
      const me = state.user && state.user.id === r.user_id;
      return `<tr${me ? ' style="outline:2px solid var(--accent);outline-offset:-2px"' : ''}>
        <td class="rank">${r.rank}</td>
        <td class="wide">${userLink(r.username, r.role)}</td>
        <td class="num"><strong>${isIcpc ? r.solved : r.total_score}</strong></td>
        ${isIcpc ? `<td class="num muted">${r.penalty}</td>` : ''}
        ${cells}
      </tr>`;
    }).join('');

    // Inside #board so it appears and disappears as the freeze takes effect,
    // rather than only on first render.
    const banner = board.frozen
      ? `<div class="freeze-banner">❄ Scoreboard frozen for the final
           ${board.freeze_minutes} minutes. Submissions still count — you just
           cannot see them resolve.</div>`
      : (board.freeze_minutes && board.state === 'running'
        ? `<div class="muted small" style="margin-bottom:10px">Freezes for the
             final ${board.freeze_minutes} minutes.</div>`
        : '');

    const html = banner + (board.rows.length
      ? `<div class="table-wrap"><table class="scoreboard">
           <thead><tr>
             <th class="num">#</th><th>Who</th>
             <th class="num">${isIcpc ? 'Solved' : 'Score'}</th>
             ${isIcpc ? '<th class="num">Penalty</th>' : ''}
             ${headCells}
           </tr></thead>
           <tbody>${rows}</tbody></table></div>`
      : '<div class="empty">Nobody has submitted yet.</div>');

    const existing = $('#board');
    if (existing) existing.innerHTML = html;
    else setView(`
      <div class="page-head">
        <a href="#/contest/${encodeURIComponent(slug)}">← ${esc(board.contest.title)}</a>
        <div class="spacer"></div>
        <span class="badge state-${esc(board.state)}">${esc(STATE_LABEL[board.state])}</span>
        <span class="countdown" data-starts="${esc(board.contest.starts_at)}" data-ends="${esc(board.contest.ends_at)}"></span>
      </div>
      <h1 style="margin-bottom:14px">Scoreboard</h1>
      <div id="board">${html}</div>
      <p class="muted small">${isIcpc
        ? `ICPC scoring: <code>+n</code> means solved after n extra attempts; the small number is the minute it was solved. Penalty adds ${board.penalty_minutes} minutes per rejected attempt.`
        : 'IOI scoring: each cell shows the best percentage of tests passed.'}</p>`, { wide: true });

    $$('.countdown').forEach(updateCountdown);
  };

  await render();
  every(5000, render);
  every(1000, () => $$('.countdown').forEach(updateCountdown));
}

/* ---- users ---- */

async function viewUsers() {
  const board = await api('/api/leaderboard');
  const total = board.standings.length;
  const columns = [['rank', '#', ''], ['username', 'Who', ''], ['score', 'Score', 'num'],
                   ['solved', 'Solved', 'num'], ['hardest', 'Hardest', 'num']];
  // Numeric columns read best largest-first, so they start descending; rank and
  // username start ascending. Re-clicking the active column flips it.
  let sort = { key: 'rank', dir: 1 };

  const row = (s) => {
    const me = state.user && state.user.username === s.username;
    return `<tr${me ? ' style="outline:2px solid var(--accent);outline-offset:-2px"' : ''}>
      <td class="rank">${s.rank}</td>
      <td class="wide">${userLink(s.username, s.role)}</td>
      <td class="num"><strong>${s.score}</strong></td>
      <td class="num muted">${s.solved}</td>
      <td class="num">${pointsPill(s.hardest)}</td>
    </tr>`;
  };

  setView(`
    <div class="page-head"><h1>Users</h1>
      <span class="info" tabindex="0" role="note" aria-label="How the score works">i
        <span class="info-pop">
          <p>Solved problems are sorted hardest first, and the <em>k</em>-th one counts
            for <code>points × ${board.decay}<sup>k</sup></code> — so the hardest solve
            counts in full, the tenth about ${Math.round(Math.pow(board.decay, 9) * 100)}%,
            and the fiftieth about ${Math.round(Math.pow(board.decay, 49) * 100)}%.</p>
          <p>Grinding a difficulty tier has a ceiling: repeating <code>p</code>-point
            problems forever converges to <code>${Math.round(1 / (1 - board.decay))} × p</code>.
            The only way past it is a harder problem, which lands near the front and
            counts nearly in full.</p>
        </span></span>
      <div class="spacer"></div>
      ${total ? '<input id="user-search" class="search" type="search" placeholder="Search users" autocomplete="off">' : ''}
      <span class="muted small" id="user-count">${total} ranked</span></div>

    ${total
      ? `<div class="table-wrap"><table>
           <thead><tr>${columns.map(([key, label, cls]) =>
             `<th class="${cls} sort" data-key="${key}">${label}<span class="arrow"></span></th>`).join('')}</tr></thead>
           <tbody id="user-rows"></tbody></table></div>
         <div class="empty" id="user-nomatch" hidden style="margin-top:18px">No user matches that.</div>`
      : '<div class="empty">Nobody has solved anything yet.</div>'}`);

  if (!total) return;
  const search = $('#user-search');

  const render = () => {
    const q = search.value.trim().toLowerCase();
    const shown = board.standings
      .filter((s) => s.username.toLowerCase().includes(q))
      .sort((a, b) => sort.dir * (sort.key === 'username'
        ? a.username.localeCompare(b.username)
        : a[sort.key] - b[sort.key] || a.username.localeCompare(b.username)));

    $('#user-rows').innerHTML = shown.map(row).join('');
    $('#user-count').textContent = q ? `${shown.length} of ${total}` : `${total} ranked`;
    $('#user-nomatch').hidden = shown.length > 0;
    $$('th.sort').forEach((th) => {
      const active = th.dataset.key === sort.key;
      th.classList.toggle('active', active);
      $('.arrow', th).textContent = active ? (sort.dir > 0 ? '↑' : '↓') : '';
    });
  };

  search.oninput = render;
  $$('th.sort').forEach((th) => {
    th.onclick = () => {
      const key = th.dataset.key;
      sort = sort.key === key
        ? { key, dir: -sort.dir }
        : { key, dir: key === 'rank' || key === 'username' ? 1 : -1 };
      render();
    };
  });
  render();
}

/* ---- user profile ---- */

async function viewUser(username) {
  const u = await api(`/api/users/${encodeURIComponent(username)}`);
  const maxPoints = Math.max(1, ...u.solved.map((s) => s.earned));

  const solvedRows = u.solved.map((s, i) => `
    <tr>
      <td class="num muted">${i + 1}</td>
      <td class="wide"><a href="#/problem/${encodeURIComponent(s.slug)}">${esc(s.title)}</a>
        ${s.earned_percent < 100 ? `<span class="muted small">${s.earned_percent}%</span>` : ''}</td>
      <td class="num">${pointsPill(s.earned)}${s.earned_percent < 100
        ? `<span class="muted small"> of ${s.points}</span>` : ''}</td>
      <td class="num muted">×${s.weight}</td>
      <td class="num"><strong>${s.contribution}</strong>
        <span class="weight-bar" style="width:${Math.round(60 * s.contribution / maxPoints)}px"></span></td>
    </tr>`).join('');

  setView(`
    <div class="page-head">
      <h1>${esc(u.username)}</h1>
      ${u.is_admin ? '<span class="badge v-CE">admin</span>' : ''}
      <div class="spacer"></div>
      <span class="muted small">joined ${esc(absolute(u.created_at).split(',')[0])}</span>
    </div>

    <div class="grid-2">
      <div class="card">
        <div class="row" style="align-items:flex-end;gap:26px">
          <div><div class="muted small">Score</div><div class="score-big">${u.score}</div></div>
          <div><div class="muted small">Rank</div><div class="score-big">${u.rank ? '#' + u.rank : '—'}</div>
            ${u.rank ? `<div class="muted small">of ${u.ranked_of}</div>` : ''}</div>
          <div><div class="muted small">Solved</div><div class="score-big">${u.solved_count}</div></div>
        </div>
      </div>
      <div class="card">
        <div class="row" style="justify-content:space-between">
          <h2 style="margin:0">About</h2>
          ${u.editable ? '<button class="small" id="edit-bio">Edit</button>' : ''}
        </div>
        <div class="statement" id="bio-view">${u.bio.trim()
          ? markdown(u.bio)
          : '<p class="muted small">Nothing here yet.</p>'}</div>
        <div id="bio-edit" hidden>
          <textarea id="bio-text" class="code" style="min-height:160px">${esc(u.bio)}</textarea>
          <div class="row end" style="margin-top:8px">
            <button class="small" id="bio-cancel">Cancel</button>
            <button class="small primary" id="bio-save">Save</button>
          </div>
        </div>
      </div>
    </div>

    ${u.authored.length ? `
      <h2>Problems written</h2>
      <div class="table-wrap"><table><tbody>${u.authored.map((p) => `
        <tr><td class="wide"><a href="#/problem/${encodeURIComponent(p.slug)}">${esc(p.title)}</a></td>
            <td class="num">${pointsPill(p.points)}</td></tr>`).join('')}</tbody></table></div>` : ''}

    <h2>Solved</h2>
    ${u.solved.length
      ? `<div class="table-wrap"><table>
           <thead><tr><th class="num">Rank</th><th>Problem</th><th class="num">Points</th>
             <th class="num">Weight</th><th class="num">Counts for</th></tr></thead>
           <tbody>${solvedRows}</tbody></table></div>
         <p class="muted small">Each solve is weighted by its rank among your own
           solves, so the total shows why it is what it is.</p>`
      : '<div class="empty">Nothing solved yet.</div>'}`, { wide: true });

  if (!u.editable) return;
  const view = $('#bio-view');
  const editor = $('#bio-edit');
  $('#edit-bio').onclick = () => { view.hidden = true; editor.hidden = false; };
  $('#bio-cancel').onclick = () => { view.hidden = false; editor.hidden = true; };
  $('#bio-save').onclick = async () => {
    try {
      const saved = await api('/api/users/me', {
        method: 'PATCH', body: { bio: $('#bio-text').value },
      });
      view.innerHTML = saved.bio.trim()
        ? markdown(saved.bio)
        : '<p class="muted small">Nothing here yet.</p>';
      view.hidden = false; editor.hidden = true;
      toast('Profile updated.', 'good');
    } catch (err) { toast(err.message, 'bad'); }
  };
}

/* ---- admin: edit one problem ---- */

async function viewAdminProblem(slug) {
  if (!state.user || !state.user.is_admin) {
    setView('<div class="empty">Admins only.</div>');
    return;
  }
  // The list comes along for the type vocabulary: chips offer what other
  // problems already use, so authors reuse a type instead of coining one.
  const [p, { problems }, { limits }] = await Promise.all([
    api(`/api/problems/${encodeURIComponent(slug)}`), api('/api/problems'),
    api(`/api/admin/problems/${encodeURIComponent(slug)}/limits`),
  ]);
  const allTypes = [...new Set(problems.flatMap((x) => x.types).concat(p.types))].sort();
  const checkerOption = (value, label) =>
    `<option value="${value}" ${p.checker === value ? 'selected' : ''}>${label}</option>`;

  setView(`
    <div class="page-head">
      <a href="#/admin">← Admin</a>
      <div class="spacer"></div>
      <a class="pill" href="#/problem/${encodeURIComponent(slug)}">view as solver</a>
    </div>
    <h1 style="margin-bottom:4px">Edit ${esc(p.title)}</h1>
    <p class="muted small mono" style="margin-top:0">${esc(slug)} · ${p.test_count} tests
      — the slug is the problem's identity and cannot be changed here.</p>

    <div class="card">
      <div class="row">
        <label style="flex:3;min-width:220px">Title <input id="e-title" value="${esc(p.title)}"></label>
        <label style="flex:1;min-width:110px">Time (ms)
          <input id="e-tl" type="number" min="100" max="60000" value="${p.time_limit_ms}"></label>
        <label style="flex:1;min-width:110px">Memory (MiB)
          <input id="e-ml" type="number" min="16" max="4096" value="${p.memory_limit_mb}"></label>
        <label style="flex:1;min-width:120px">Checker
          <select id="e-checker">
            ${checkerOption('token', 'token')}${checkerOption('exact', 'exact')}${checkerOption('float', 'float')}
          </select></label>
        <label style="flex:1;min-width:130px">Float epsilon
          <input id="e-eps" type="number" step="any" value="${p.float_eps}"></label>
        <label style="flex:1;min-width:110px">Points
          <input id="e-points" type="number" min="1" max="10000" value="${p.points}"></label>
        <label style="flex:2;min-width:160px">Author
          <input id="e-author" value="${esc(p.author || '')}" placeholder="(unattributed)"></label>
      </div>
      <label>Types ${typeChips('e-types', allTypes, p.types, true)}</label>
    </div>

    <details class="card" id="e-limits-card"${Object.values(limits).some((l) => l.measured) ? ' open' : ''}>
      <summary>Per-language limits</summary>
      <p class="muted small">Set these from a measured run of the intended
        solution in each language — the gap between runtimes depends on the
        problem, not just the language. Clear a row to fall back to the base
        limit above, scaled by that language's multiplier.</p>
      <div class="table-wrap"><table>
        <thead><tr><th>Language</th><th class="num">Time (ms)</th>
          <th class="num">Memory (MiB)</th><th>Source</th><th></th></tr></thead>
        <tbody>${Object.entries(limits).map(([id, l]) => `
          <tr>
            <td class="wide">${esc(l.name)}</td>
            <td><input class="lim" data-lang="${esc(id)}" data-f="time" type="number"
                  min="100" max="60000" value="${l.time_limit_ms}" style="width:110px"></td>
            <td><input class="lim" data-lang="${esc(id)}" data-f="memory" type="number"
                  min="16" max="4096" value="${l.memory_limit_mb}" style="width:110px"></td>
            <td><span class="pill" data-source="${esc(id)}">${l.measured ? 'measured' : 'derived'}</span></td>
            <td>${l.measured
              ? `<button class="small" data-clear-limit="${esc(id)}">Clear</button>` : ''}</td>
          </tr>`).join('')}</tbody>
      </table></div>
      <div class="row end"><button class="small primary" id="e-save-limits">Save limits</button></div>
      <div class="row">
        <label class="row" style="gap:6px"><input type="checkbox" id="e-partial" style="width:auto"
          ${p.partial ? 'checked' : ''}> partial scoring</label>
        <label class="row" style="gap:6px"><input type="checkbox" id="e-visible" style="width:auto"
          ${p.visible ? 'checked' : ''}> visible</label>
      </div>
    </div>

    <div class="grid-2">
      <div>
        <h2 style="margin-top:0">Statement (Markdown)</h2>
        <textarea id="e-statement" class="code" style="min-height:460px"
          spellcheck="false">${esc(p.statement)}</textarea>
      </div>
      <div>
        <h2 style="margin-top:0">Preview</h2>
        <div class="card statement" id="e-preview" style="min-height:460px"></div>
      </div>
    </div>

    <div class="row end" style="margin-top:14px">
      <span class="muted small spacer" id="e-status"></span>
      <button id="e-save" class="primary">Save changes</button>
    </div>`, { wide: true });

  bindTypeChips('e-types');

  // Only the rows an author actually touches are sent, so opening the editor
  // and saving does not silently pin every language to its derived value.
  const touched = new Set();
  $$('.lim').forEach((input) => {
    input.oninput = () => touched.add(input.dataset.lang);
  });

  $('#e-save-limits').onclick = async () => {
    const button = $('#e-save-limits');
    button.disabled = true;
    try {
      const body = {};
      for (const lang of touched) {
        const field = (f) => $(`.lim[data-lang="${CSS.escape(lang)}"][data-f="${f}"]`);
        body[lang] = {
          time_limit_ms: Number(field('time').value),
          memory_limit_mb: Number(field('memory').value),
        };
      }
      if (!Object.keys(body).length) { toast('Nothing changed.'); return; }
      await api(`/api/admin/problems/${encodeURIComponent(slug)}/limits`, {
        method: 'PUT', body: { limits: body },
      });
      toast(`Set limits for ${Object.keys(body).length} language(s).`, 'good');
      route();
    } catch (err) {
      toast(err.message, 'bad');
    } finally {
      button.disabled = false;
    }
  };

  $$('[data-clear-limit]').forEach((button) => {
    button.onclick = async () => {
      const lang = button.dataset.clearLimit;
      try {
        await api(`/api/admin/problems/${encodeURIComponent(slug)}/limits`, {
          method: 'PUT', body: { limits: { [lang]: null } },
        });
        toast(`${lang} back to the derived limit.`);
        route();
      } catch (err) { toast(err.message, 'bad'); }
    };
  });

  const editor = $('#e-statement');
  markdownEditor(editor, $('#e-preview'));

  $('#e-save').onclick = async () => {
    const button = $('#e-save');
    button.disabled = true;
    $('#e-status').textContent = 'Saving…';
    try {
      await api(`/api/admin/problems/${encodeURIComponent(slug)}`, {
        method: 'PATCH',
        body: {
          title: $('#e-title').value.trim(),
          statement: editor.value,
          time_limit_ms: Number($('#e-tl').value),
          memory_limit_mb: Number($('#e-ml').value),
          checker: $('#e-checker').value,
          float_eps: Number($('#e-eps').value),
          partial: $('#e-partial').checked,
          visible: $('#e-visible').checked,
          points: Number($('#e-points').value),
          author: $('#e-author').value.trim() || null,
          types: chosenTypes('e-types'),
        },
      });
      $('#e-status').textContent = 'Saved.';
      toast('Problem updated.', 'good');
    } catch (err) {
      $('#e-status').textContent = '';
      toast(err.message, 'bad');
    } finally {
      button.disabled = false;
    }
  };
}

/* ---- admin: edit one post ---- */

async function viewAdminPost(slug) {
  if (!state.user || !state.user.is_admin) {
    setView('<div class="empty">Admins only.</div>');
    return;
  }
  const p = await api(`/api/posts/${encodeURIComponent(slug)}`);

  setView(`
    <div class="page-head">
      <a href="#/admin">← Admin</a>
      <div class="spacer"></div>
      <a class="pill" href="#/post/${encodeURIComponent(slug)}">view in stream</a>
    </div>
    <h1 style="margin-bottom:4px">Edit post</h1>
    <p class="muted small mono" style="margin-top:0">${esc(slug)}</p>

    <div class="card">
      <div class="row">
        <label style="flex:3;min-width:240px">Title <input id="e-title" value="${esc(p.title)}"></label>
        <label class="row" style="gap:6px"><input type="checkbox" id="e-pinned" style="width:auto"
          ${p.pinned ? 'checked' : ''}> pinned</label>
        <label class="row" style="gap:6px"><input type="checkbox" id="e-published" style="width:auto"
          ${p.published ? 'checked' : ''}> published</label>
      </div>
    </div>

    <div class="grid-2">
      <div>
        <h2 style="margin-top:0">Body (Markdown)</h2>
        <textarea id="e-body" class="code" style="min-height:420px"
          spellcheck="false">${esc(p.body)}</textarea>
      </div>
      <div>
        <h2 style="margin-top:0">Preview</h2>
        <div class="card statement" id="e-preview" style="min-height:420px"></div>
      </div>
    </div>

    <div class="row end" style="margin-top:14px">
      <span class="muted small spacer" id="e-status"></span>
      <button id="e-save" class="primary">Save changes</button>
    </div>`, { wide: true });

  markdownEditor($('#e-body'), $('#e-preview'));

  $('#e-save').onclick = async () => {
    const button = $('#e-save');
    button.disabled = true;
    $('#e-status').textContent = 'Saving…';
    try {
      await api(`/api/admin/posts/${encodeURIComponent(slug)}`, {
        method: 'PATCH',
        body: {
          title: $('#e-title').value.trim(),
          body: $('#e-body').value,
          pinned: $('#e-pinned').checked,
          published: $('#e-published').checked,
        },
      });
      $('#e-status').textContent = 'Saved.';
      toast('Post updated.', 'good');
    } catch (err) {
      $('#e-status').textContent = '';
      toast(err.message, 'bad');
    } finally {
      button.disabled = false;
    }
  };
}

/* ---- admin ---- */

async function viewAdmin() {
  if (!state.user || !state.user.is_admin) {
    setView('<div class="empty">Admins only.</div>');
    return;
  }
  const [{ problems }, { contests }, { users }, { posts }] = await Promise.all([
    api('/api/problems'), api('/api/contests'), api('/api/admin/users'),
    api('/api/posts?limit=100'),
  ]);

  const allTypes = [...new Set(problems.flatMap((p) => p.types))].sort();
  const postRows = posts.map((p) => `
    <tr>
      <td class="wide"><a href="#/post/${encodeURIComponent(p.slug)}">${esc(p.title)}</a></td>
      <td class="mono small muted">${esc(p.slug)}</td>
      <td><span class="pill">${p.published ? 'published' : 'draft'}</span>
        ${p.pinned ? '<span class="pill">pinned</span>' : ''}</td>
      <td class="muted small">${esc(relative(p.created_at))}</td>
      <td>
        <div class="row">
          <a class="btn small" href="#/admin/post/${encodeURIComponent(p.slug)}">Edit</a>
          <button class="small" data-pin="${esc(p.slug)}" data-pinned="${p.pinned ? 1 : 0}">${p.pinned ? 'Unpin' : 'Pin'}</button>
          <button class="small danger" data-delete-post="${esc(p.slug)}">Delete</button>
        </div>
      </td>
    </tr>`).join('');

  const problemRows = problems.map((p) => `
    <tr>
      <td class="wide"><a href="#/problem/${encodeURIComponent(p.slug)}">${esc(p.title)}</a></td>
      <td class="mono small muted">${esc(p.slug)}</td>
      <td>${p.visible ? '<span class="pill">visible</span>' : '<span class="pill">hidden</span>'}</td>
      <td>
        <div class="row">
          <a class="btn small" href="#/admin/problem/${encodeURIComponent(p.slug)}">Edit</a>
          <input type="file" accept=".zip" class="small" data-upload="${esc(p.slug)}" style="width:190px">
          <button class="small" data-toggle="${esc(p.slug)}" data-visible="${p.visible ? 1 : 0}">${p.visible ? 'Hide' : 'Show'}</button>
          <button class="small" data-rejudge="${esc(p.slug)}">Rejudge</button>
          <button class="small danger" data-delete="${esc(p.slug)}">Delete</button>
        </div>
      </td>
    </tr>`).join('');

  const contestRows = contests.map((c) => `
    <tr>
      <td class="wide"><a href="#/contest/${encodeURIComponent(c.slug)}">${esc(c.title)}</a></td>
      <td class="mono small muted">${esc(c.slug)}</td>
      <td><span class="badge state-${esc(c.state)}">${esc(STATE_LABEL[c.state])}</span></td>
      <td>
        <div class="row">
          <button class="small" data-edit-contest="${esc(c.slug)}">Edit</button>
          <button class="small danger" data-delete-contest="${esc(c.slug)}">Delete</button>
        </div>
      </td>
    </tr>
    <tr class="editor-row" data-editor="${esc(c.slug)}" hidden>
      <td colspan="4">
        <div class="grid-2">
          <div>
            <label>Title <input data-f="title" value="${esc(c.title)}"></label>
            <div class="row">
              <label style="flex:1">Scoring
                <select data-f="scoring">
                  <option value="icpc"${c.scoring === 'icpc' ? ' selected' : ''}>ICPC</option>
                  <option value="ioi"${c.scoring === 'ioi' ? ' selected' : ''}>IOI</option>
                </select>
              </label>
              <label style="flex:1">Penalty (min)
                <input data-f="penalty_minutes" type="number" min="0" max="1440" value="${c.penalty_minutes}"></label>
            </div>
            <label>Scoreboard freeze (min before end, 0 = none)
              <input data-f="freeze_minutes" type="number" min="0" max="1440" value="${c.freeze_minutes}"></label>
            <label>Problem set (comma-separated slugs, in order)
              <input data-f="problems" placeholder="loading…" disabled></label>
          </div>
          <div>
            <label>Starts (your local time)
              <input data-f="starts_at" type="datetime-local" value="${localField(c.starts_at)}"></label>
            <label>Ends (your local time)
              <input data-f="ends_at" type="datetime-local" value="${localField(c.ends_at)}"></label>
            <label>Description <textarea data-f="description" style="min-height:60px" placeholder="loading…" disabled></textarea></label>
          </div>
        </div>
        <p class="small muted" data-warn="${esc(c.slug)}"></p>
        <div class="row end">
          <button class="small" data-cancel-contest="${esc(c.slug)}">Cancel</button>
          <button class="small primary" data-save-contest="${esc(c.slug)}">Save changes</button>
        </div>
      </td>
    </tr>`).join('');

  const now = new Date(Date.now() + state.clockSkewMs);
  const isoLocal = (d) => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);

  setView(`
    <div class="page-head"><h1>Admin</h1></div>

    <details class="admin-section card">
      <summary>New post</summary>
      <div class="row">
        <label style="flex:2;min-width:220px">Title <input id="n-title" placeholder="Round 2 results"></label>
        <label style="flex:2;min-width:200px">Slug <input id="n-slug" placeholder="round-2-results"></label>
        <label class="row" style="gap:6px"><input type="checkbox" id="n-pinned" style="width:auto"> pinned</label>
        <label class="row" style="gap:6px"><input type="checkbox" id="n-published" checked style="width:auto"> published</label>
      </div>
      <div class="grid-2">
        <label>Body (Markdown)
          <textarea id="n-body" class="code" style="min-height:220px"></textarea></label>
        <div>
          <label>Preview</label>
          <div class="card statement" id="n-preview" style="min-height:220px"></div>
        </div>
      </div>
      <div class="row end"><button class="primary" id="create-post">Publish</button></div>
    </details>

    <h2>Posts</h2>
    <div class="table-wrap"><table>
      <thead><tr><th>Title</th><th>Slug</th><th>State</th><th>Posted</th><th>Actions</th></tr></thead>
      <tbody>${postRows || '<tr><td colspan="5" class="muted">None yet.</td></tr>'}</tbody></table></div>

    <details class="admin-section card" open>
      <summary>New problem</summary>
      <div class="grid-2">
        <div>
          <label>Slug <input id="p-slug" placeholder="two-sum" required></label>
          <label>Title <input id="p-title" placeholder="Two Sum"></label>
          <div class="row">
            <label style="flex:1">Points <input id="p-points" type="number" value="100" min="1" max="10000"></label>
            <label style="flex:2">Author <input id="p-author" placeholder="(you)"></label>
          </div>
          <label>Types ${typeChips('p-types', allTypes, [], true)}</label>
          <div class="row">
            <label style="flex:1">Time limit (ms) <input id="p-tl" type="number" value="1000" min="100" max="60000"></label>
            <label style="flex:1">Memory (MiB) <input id="p-ml" type="number" value="256" min="16" max="4096"></label>
          </div>
          <div class="row">
            <label style="flex:1">Checker
              <select id="p-checker"><option value="token">token</option><option value="exact">exact</option><option value="float">float</option></select>
            </label>
            <label style="flex:1">Float epsilon <input id="p-eps" type="number" step="any" value="0.000001"></label>
          </div>
          <div class="row">
            <label class="row" style="gap:6px"><input type="checkbox" id="p-partial" style="width:auto"> partial scoring</label>
            <label class="row" style="gap:6px"><input type="checkbox" id="p-visible" checked style="width:auto"> visible</label>
          </div>
        </div>
        <div>
          <label>Statement (Markdown)
            <textarea id="p-statement" class="code" style="min-height:230px"></textarea>
          </label>
          <label>Test data — zip of <code>1.in</code> / <code>1.out</code> pairs (optional)
            <input type="file" id="p-tests" accept=".zip">
          </label>
          <p class="small" id="p-tests-status" style="margin-top:-6px"></p>
        </div>
      </div>
      <div class="row end"><button class="primary" id="create-problem">Create problem</button></div>
      <p class="muted small">The archive is checked as soon as you pick it, before anything is created.
        Files whose name contains <code>sample</code> become visible samples; the rest stay hidden.
        You can also add or replace test data later from the table below.</p>
    </details>

    <h2>Problems</h2>
    <div class="table-wrap"><table>
      <thead><tr><th>Title</th><th>Slug</th><th>State</th><th>Actions</th></tr></thead>
      <tbody>${problemRows || '<tr><td colspan="4" class="muted">None yet.</td></tr>'}</tbody></table></div>

    <details class="admin-section card">
      <summary>New contest</summary>
      <div class="grid-2">
        <div>
          <label>Slug <input id="c-slug" placeholder="round-2"></label>
          <label>Title <input id="c-title" placeholder="stroj Open Round 2"></label>
          <div class="row">
            <label style="flex:1">Scoring
              <select id="c-scoring"><option value="icpc">ICPC</option><option value="ioi">IOI</option></select>
            </label>
            <label style="flex:1">Penalty (min) <input id="c-penalty" type="number" value="20" min="0" max="1440"></label>
          </div>
          <label>Scoreboard freeze (min before end, 0 = none)
            <input id="c-freeze" type="number" value="0" min="0" max="1440"></label>
        </div>
        <div>
          <label>Starts (your local time) <input id="c-start" type="datetime-local" value="${isoLocal(now)}"></label>
          <label>Ends (your local time) <input id="c-end" type="datetime-local" value="${isoLocal(new Date(now.getTime() + 3 * 3600e3))}"></label>
          <label>Description <textarea id="c-desc" style="min-height:60px"></textarea></label>
        </div>
      </div>
      <div class="row end"><button class="primary" id="create-contest">Create contest</button></div>
    </details>

    <h2>Contests</h2>
    <div class="table-wrap"><table>
      <thead><tr><th>Title</th><th>Slug</th><th>State</th><th>Problem set</th></tr></thead>
      <tbody>${contestRows || '<tr><td colspan="4" class="muted">None yet.</td></tr>'}</tbody></table></div>

    <h2>Users</h2>
    <div class="table-wrap"><table>
      <thead><tr><th>User</th><th>Role</th><th>Joined</th><th></th></tr></thead>
      <tbody>${users.map((u) => `
        <tr>
          <td class="wide">${esc(u.username)}</td>
          <td><span class="pill">${esc(u.role)}</span></td>
          <td class="muted small">${esc(absolute(u.created_at))}</td>
          <td><button class="small" data-role="${esc(u.username)}" data-next="${u.role === 'admin' ? 'user' : 'admin'}">
            Make ${u.role === 'admin' ? 'user' : 'admin'}</button></td>
        </tr>`).join('')}</tbody></table></div>`, { wide: true });

  const guard = (fn) => async (...args) => {
    try { await fn(...args); } catch (err) { toast(err.message, 'bad'); }
  };

  markdownEditor($('#n-body'), $('#n-preview'));

  // The slug is the post's permalink; keep it following the title until an
  // author types one by hand.
  $('#n-title').oninput = () => {
    if ($('#n-slug').dataset.touched) return;
    $('#n-slug').value = $('#n-title').value.toLowerCase()
      .replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 64);
  };
  $('#n-slug').oninput = () => { $('#n-slug').dataset.touched = '1'; };

  $('#create-post').onclick = guard(async () => {
    await api('/api/admin/posts', {
      method: 'POST',
      body: {
        slug: $('#n-slug').value.trim(),
        title: $('#n-title').value.trim(),
        body: $('#n-body').value,
        pinned: $('#n-pinned').checked,
        published: $('#n-published').checked,
      },
    });
    toast('Posted.', 'good');
    route();
  });

  $$('[data-pin]').forEach((button) => {
    button.onclick = guard(async () => {
      await api(`/api/admin/posts/${encodeURIComponent(button.dataset.pin)}`, {
        method: 'PATCH', body: { pinned: button.dataset.pinned !== '1' },
      });
      route();
    });
  });

  $$('[data-delete-post]').forEach((button) => {
    button.onclick = guard(async () => {
      const slug = button.dataset.deletePost;
      if (!confirm(`Delete post "${slug}"?`)) return;
      await api(`/api/admin/posts/${encodeURIComponent(slug)}`, { method: 'DELETE' });
      toast('Deleted.');
      route();
    });
  });

  // Validated archive, held in the browser until the problem exists to attach
  // it to. Null means either nothing chosen or the last check failed.
  let pendingTests = null;

  $('#p-tests').onchange = async () => {
    const input = $('#p-tests');
    const status = $('#p-tests-status');
    pendingTests = null;
    if (!input.files.length) { status.textContent = ''; return; }

    status.className = 'small muted';
    status.textContent = 'Checking archive…';
    const form = new FormData();
    form.append('archive', input.files[0]);
    try {
      const found = await api('/api/admin/testdata/inspect', { method: 'POST', form });
      pendingTests = input.files[0];
      status.className = 'small';
      status.style.color = 'var(--ok)';
      status.textContent =
        `✓ ${found.tests} test${found.tests === 1 ? '' : 's'}, ` +
        `${found.samples} sample${found.samples === 1 ? '' : 's'} — first input starts "` +
        `${found.preview.input.trim().slice(0, 40)}"`;
    } catch (err) {
      status.className = 'small error';
      status.style.color = '';
      status.textContent = err.message;
    }
  };

  bindTypeChips('p-types');

  $('#create-problem').onclick = guard(async () => {
    const slug = $('#p-slug').value.trim();
    if ($('#p-tests').files.length && !pendingTests) {
      throw new Error('That test archive was rejected — fix it or clear the field.');
    }

    await api('/api/admin/problems', {
      method: 'POST',
      body: {
        slug,
        title: $('#p-title').value.trim() || slug,
        statement: $('#p-statement').value,
        time_limit_ms: Number($('#p-tl').value),
        memory_limit_mb: Number($('#p-ml').value),
        checker: $('#p-checker').value,
        float_eps: Number($('#p-eps').value),
        partial: $('#p-partial').checked,
        visible: $('#p-visible').checked,
        points: Number($('#p-points').value),
        author: $('#p-author').value.trim() || null,
        types: chosenTypes('p-types'),
      },
    });

    if (pendingTests) {
      const form = new FormData();
      form.append('archive', pendingTests);
      try {
        const result = await api(
          `/api/admin/problems/${encodeURIComponent(slug)}/tests/upload`,
          { method: 'POST', form });
        toast(`Created with ${result.tests} test case(s).`, 'good');
      } catch (err) {
        // The archive passed inspection, so this is unexpected — but never
        // leave a testless problem behind because the second call failed.
        await api(`/api/admin/problems/${encodeURIComponent(slug)}`, { method: 'DELETE' })
          .catch(() => {});
        throw new Error(`Test data failed to attach, problem not created: ${err.message}`);
      }
    } else {
      toast('Problem created — it needs test data before it can be judged.', 'good');
    }
    route();
  });

  $('#create-contest').onclick = guard(async () => {
    const toUtc = (value) => new Date(value).toISOString();
    await api('/api/admin/contests', {
      method: 'POST',
      body: {
        slug: $('#c-slug').value.trim(),
        title: $('#c-title').value.trim() || $('#c-slug').value.trim(),
        description: $('#c-desc').value,
        starts_at: toUtc($('#c-start').value),
        ends_at: toUtc($('#c-end').value),
        scoring: $('#c-scoring').value,
        penalty_minutes: Number($('#c-penalty').value),
        freeze_minutes: Number($('#c-freeze').value),
      },
    });
    toast('Contest created.', 'good');
    route();
  });

  $$('[data-upload]').forEach((input) => {
    input.onchange = guard(async () => {
      if (!input.files.length) return;
      const form = new FormData();
      form.append('archive', input.files[0]);
      const result = await api(
        `/api/admin/problems/${encodeURIComponent(input.dataset.upload)}/tests/upload`,
        { method: 'POST', form });
      toast(`Loaded ${result.tests} test case(s).`, 'good');
      input.value = '';
    });
  });

  $$('[data-toggle]').forEach((button) => {
    button.onclick = guard(async () => {
      await api(`/api/admin/problems/${encodeURIComponent(button.dataset.toggle)}`, {
        method: 'PATCH', body: { visible: button.dataset.visible !== '1' },
      });
      route();
    });
  });

  $$('[data-rejudge]').forEach((button) => {
    button.onclick = guard(async () => {
      const result = await api(
        `/api/admin/rejudge?problem=${encodeURIComponent(button.dataset.rejudge)}`, { method: 'POST' });
      toast(`Requeued ${result.requeued} submission(s).`, 'good');
    });
  });

  $$('[data-delete]').forEach((button) => {
    button.onclick = guard(async () => {
      const slug = button.dataset.delete;
      // Ask the server what this would actually destroy: deleting a problem
      // cascades to every submission against it, which a generic warning makes
      // far too easy to click through.
      const impact = await api(`/api/admin/problems/${encodeURIComponent(slug)}/impact`);
      const lines = [`Delete "${slug}" and its test data?`];
      if (impact.submissions) {
        lines.push('', `This also deletes ${impact.submissions} submission` +
          `${impact.submissions === 1 ? '' : 's'} from ${impact.users} ` +
          `user${impact.users === 1 ? '' : 's'}. That cannot be undone.`);
      }
      if (impact.contests.length) {
        lines.push('', `It is used in: ${impact.contests.map((c) => c.title).join(', ')}.` +
          ' Those scoreboards will change.');
      }
      if (!confirm(lines.join('\n'))) return;
      await api(`/api/admin/problems/${encodeURIComponent(slug)}`, { method: 'DELETE' });
      toast('Deleted.');
      route();
    });
  });

  $$('[data-delete-contest]').forEach((button) => {
    button.onclick = guard(async () => {
      const slug = button.dataset.deleteContest;
      if (!confirm(`Delete contest "${slug}"?`)) return;
      await api(`/api/admin/contests/${encodeURIComponent(slug)}`, { method: 'DELETE' });
      toast('Deleted.');
      route();
    });
  });

  // Editing a contest, rather than deleting and recreating it. The two fields
  // the list response does not carry — description and the current problem set
  // — are filled in from the detail endpoint the first time an editor opens.
  const editorOf = (slug) => $(`[data-editor="${CSS.escape(slug)}"]`);
  const fieldsOf = (slug) => {
    const found = {};
    $$('[data-f]', editorOf(slug)).forEach((el) => { found[el.dataset.f] = el; });
    return found;
  };
  const loaded = new Set();

  $$('[data-edit-contest]').forEach((button) => {
    button.onclick = guard(async () => {
      const slug = button.dataset.editContest;
      const row = editorOf(slug);
      row.hidden = !row.hidden;
      if (row.hidden || loaded.has(slug)) return;

      const detail = await api(`/api/contests/${encodeURIComponent(slug)}`);
      const fields = fieldsOf(slug);
      fields.description.value = detail.description || '';
      fields.description.disabled = false;
      fields.description.placeholder = '';
      // Seeing the current set is the point: otherwise saving an empty box
      // silently empties the contest.
      fields.problems.value = (detail.problems || []).map((pr) => pr.slug).join(', ');
      fields.problems.disabled = false;
      fields.problems.placeholder = 'slug-a, slug-b, …';
      if (detail.sealed) {
        fields.problems.value = '';
        fields.problems.placeholder = 'unavailable — reopen after the contest starts';
        fields.problems.disabled = true;
      }
      loaded.add(slug);

      const warn = $(`[data-warn="${CSS.escape(slug)}"]`);
      if (detail.state === 'running') {
        warn.textContent = 'This contest is running. Moving the start time' +
          ' recomputes every penalty on the scoreboard.';
      } else if (detail.state === 'ended') {
        warn.textContent = 'This contest has ended. Editing it rewrites results' +
          ' that entrants have already seen.';
      }
    });
  });

  $$('[data-cancel-contest]').forEach((button) => {
    button.onclick = () => { editorOf(button.dataset.cancelContest).hidden = true; };
  });

  $$('[data-save-contest]').forEach((button) => {
    button.onclick = guard(async () => {
      const slug = button.dataset.saveContest;
      const fields = fieldsOf(slug);
      const toUtc = (value) => new Date(value).toISOString();

      await api(`/api/admin/contests/${encodeURIComponent(slug)}`, {
        method: 'PATCH',
        body: {
          title: fields.title.value.trim(),
          description: fields.description.value,
          starts_at: toUtc(fields.starts_at.value),
          ends_at: toUtc(fields.ends_at.value),
          scoring: fields.scoring.value,
          penalty_minutes: Number(fields.penalty_minutes.value),
          freeze_minutes: Number(fields.freeze_minutes.value),
        },
      });

      if (!fields.problems.disabled) {
        const slugs = fields.problems.value.split(',').map((x) => x.trim()).filter(Boolean);
        await api(`/api/admin/contests/${encodeURIComponent(slug)}/problems`, {
          method: 'PUT', body: { problems: slugs.map((x) => ({ slug: x })) },
        });
      }
      toast('Contest updated.', 'good');
      route();
    });
  });

  $$('[data-role]').forEach((button) => {
    button.onclick = guard(async () => {
      await api(`/api/admin/users/${encodeURIComponent(button.dataset.role)}/role?role=${button.dataset.next}`,
        { method: 'POST' });
      route();
    });
  });
}

/* ----------------------------------------------------------------- router */

async function route() {
  clearTimers();
  const raw = location.hash.slice(1) || '/home';
  const [path, queryString] = raw.split('?');
  const params = new URLSearchParams(queryString || '');
  const parts = path.split('/').filter(Boolean);

  // Detail routes are singular ("#/problem/x"); highlight their list nav entry.
  const section = { problem: 'problems', contest: 'contests', submission: 'submissions',
                    user: 'users' }[parts[0]] || parts[0];
  $$('#nav a').forEach((a) => a.classList.toggle('active', a.dataset.route === section));

  try {
    switch (parts[0]) {
      case 'home': await viewHome(); break;
      case 'post': await viewPost(decodeURIComponent(parts[1] || '')); break;
      case 'problems': await viewProblems(); break;
      case 'problem': await viewProblem(decodeURIComponent(parts[1] || ''), params); break;
      case 'submissions': await viewSubmissions(params); break;
      case 'submission': await viewSubmission(parts[1]); break;
      case 'contests': await viewContests(); break;
      case 'contest':
        if (parts[2] === 'scoreboard') await viewScoreboard(decodeURIComponent(parts[1]));
        else await viewContest(decodeURIComponent(parts[1] || ''));
        break;
      case 'users': await viewUsers(); break;
      // The page was called the leaderboard until it grew search and sorting.
      // Links to it are already out there, and the default case would land
      // them on the stream instead of the page they asked for.
      case 'leaderboard': location.hash = '#/users'; break;
      case 'user': await viewUser(decodeURIComponent(parts[1] || '')); break;
      case 'admin':
        if (parts[1] === 'problem' && parts[2]) await viewAdminProblem(decodeURIComponent(parts[2]));
        else if (parts[1] === 'post' && parts[2]) await viewAdminPost(decodeURIComponent(parts[2]));
        else await viewAdmin();
        break;
      default: location.hash = '#/home';
    }
  } catch (err) {
    if (err.status === 401) setView(requireSignIn('You need to sign in to see this.'));
    else setView(`<div class="empty"><strong>${esc(err.message)}</strong></div>`);
  }
}

/* ------------------------------------------------------------------- boot */

async function boot() {
  try {
    const [me, langs, config, version] = await Promise.all([
      api('/api/auth/me'), api('/api/languages'), api('/api/config'),
      api('/api/version').catch(() => null),
    ]);
    state.user = me.user;
    state.languages = langs.languages;
    state.defaultLanguage = langs.default;
    state.config = config;
    state.version = version;
  } catch (err) {
    // The static frontend can be deployed before the judge backend exists, so
    // say which half is missing instead of showing a bare fetch error.
    setView(`
      <div class="empty" style="text-align:left;max-width:620px;margin:40px auto">
        <h2 style="margin-top:0">No judge backend connected</h2>
        <p class="muted">The site loaded, but <code>/api/*</code> is not reaching a
        judge. The frontend is static; compiling and running submissions needs a
        separate always-on backend.</p>
        <p class="muted small mono">${esc(err.message)}</p>
        <p class="muted small">If you are deploying this: stand up the judge
        container, then point the <code>/api/*</code> rewrite at its origin and
        redeploy. See <code>DEPLOY.md</code>.</p>
      </div>`);
    // Nothing here is actionable without a backend — don't imply otherwise.
    $$('.admin-only').forEach((node) => { node.style.display = 'none'; });
    $('#account').innerHTML = '';
    $('#footer-info').textContent = 'stroj · frontend only — no judge connected';
    return;
  }

  // The two halves deploy independently, so they can drift apart. Show it.
  const metaCommit = (document.querySelector('meta[name="stroj-commit"]') || {}).content;
  const frontendCommit =
    (!metaCommit || metaCommit.startsWith('__')) ? null : metaCommit;
  const backendCommit = (state.version && state.version.commit) || null;
  let versionNote = '';
  if (frontendCommit && backendCommit && backendCommit !== 'unknown') {
    versionNote = frontendCommit === backendCommit
      ? ` · ${frontendCommit.slice(0, 7)}`
      : ` · ⚠ frontend ${frontendCommit.slice(0, 7)} ≠ backend ${backendCommit.slice(0, 7)}`;
  } else if (backendCommit && backendCommit !== 'unknown') {
    versionNote = ` · backend ${backendCommit.slice(0, 7)}`;
  }

  const installed = state.languages.filter((l) => l.available).map((l) => l.name);
  // Reports overall posture, not just the in-process sandbox mechanism —
  // privilege separation is what does the work inside a Linux container.
  const isolation = {
    full: 'isolation: full',
    'separated+netns': 'isolation: separate account, no network',
    separated: 'isolation: separate account',
    'network-only': 'isolation: network only',
    none: 'isolation: NONE',
  }[state.config.protection] || 'isolation: unknown';
  $('#footer-info').textContent =
    `stroj · ${installed.join(' · ') || 'no languages installed'} · ` +
    `${isolation} · ${state.config.workers} judge worker(s)${versionNote}`;

  renderAccount();
  window.addEventListener('hashchange', route);
  await route();
}

boot();
