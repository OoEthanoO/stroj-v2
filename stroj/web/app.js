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

/** One row per language in the per-language limits table. */
const limitRows = (limits) => Object.entries(limits).map(([id, l]) => `
  <tr>
    <td class="wide">${esc(l.name)}</td>
    <td><input class="lim" data-lang="${esc(id)}" data-f="time" type="number"
          min="100" max="60000" value="${l.time_limit_ms}" style="width:110px"></td>
    <td><input class="lim" data-lang="${esc(id)}" data-f="memory" type="number"
          min="16" max="4096" value="${l.memory_limit_mb}" style="width:110px"></td>
    <td><span class="pill">${l.measured ? 'measured' : 'derived'}</span></td>
    <td>${l.measured
      ? `<button class="small" data-clear-limit="${esc(id)}">Clear</button>` : ''}</td>
  </tr>`).join('');

/* A rough orientation aid for solvers who came from DMOJ.
 *
 * There is deliberately no conversion formula. The two judges score unrelated
 * things — DMOJ points are a difficulty rating, stroj points feed a decayed
 * ranking — and pretending otherwise would invite people to "convert" a rating
 * that was never meant to travel. These are difficulty bands that happen to
 * line up, nothing more. */
const DMOJ_BANDS = [
  { dmoj: '1 – 3', from: 1, to: 50,
    gist: 'read the input, apply a formula' },
  { dmoj: '5 – 7', from: 51, to: 150,
    gist: 'one standard technique, applied directly' },
  { dmoj: '10 – 15', from: 151, to: 300,
    gist: 'a technique plus a step that is not obvious' },
  { dmoj: '17 – 25', from: 301, to: 600,
    gist: 'several ideas combined' },
  { dmoj: '30 +', from: 601, to: Infinity,
    gist: 'olympiad territory' },
];

/** Which band a stroj point value sits in, or null if it is out of range. */
function dmojBand(points) {
  if (!Number.isFinite(points) || points < 1) return null;
  return DMOJ_BANDS.find((b) => points >= b.from && points <= b.to) || null;
}

/** The table, with each band showing whichever problems currently sit in it. */
function dmojTable(problems) {
  const rows = DMOJ_BANDS.map((band) => {
    // Filled from the live problem list, so it can never drift out of date the
    // way a hand-written example would.
    const here = (problems || [])
      .filter((p) => dmojBand(p.points) === band)
      .sort((a, b) => a.points - b.points)
      .map((p) => `<a href="#/problem/${encodeURIComponent(p.slug)}">${esc(p.title)}</a>`)
      .join(', ');
    const range = band.to === Infinity ? `${band.from}+` : `${band.from} – ${band.to}`;
    return `<tr>
      <td class="mono">${esc(band.dmoj)}</td>
      <td class="mono">${esc(range)}</td>
      <td class="muted small">${esc(band.gist)}</td>
      <td class="wide small">${here || '<span class="muted">—</span>'}</td>
    </tr>`;
  }).join('');

  return `
    <details class="card dmoj-card">
      <summary>Coming from DMOJ?</summary>
      <p class="muted small">The two judges are unrelated and there is no
        conversion between them — DMOJ points rate a problem's difficulty, while
        stroj points feed a ranking that discounts your easier solves. This is
        only a rough sense of which difficulties tend to land where, for people
        used to reading DMOJ numbers. Do not treat it as a formula.</p>
      <div class="table-wrap"><table class="dmoj-table">
        <thead><tr><th>DMOJ</th><th>stroj</th><th>Roughly</th>
          <th>Here right now</th></tr></thead>
        <tbody>${rows}</tbody>
      </table></div>
    </details>`;
}

const pointsPill = (points) => `<span class="points-pill">${Number(points) || 0}</span>`;

/** A competitor's rank, or the absence of one.
 *
 * Someone who has not entered a rated contest is Unranked rather than sitting
 * at the rank their starting rating would imply — that number is a placeholder,
 * and drawing it as a standing claims something the judge cannot support. */
function rankBadge(rank, { rating } = {}) {
  if (!rank) return '<span class="rank-badge rank-unranked">Unranked</span>';
  // Escaped even in the class: the tier is server data, and every other
  // interpolation on this page goes through esc. One that does not is the kind
  // of exception nobody remembers when the source of the data changes.
  const tier = esc(rank.tier.toLowerCase());
  const value = rating == null ? '' : `<span class="rank-rating">${esc(rating)}</span>`;
  return `<span class="rank-badge rank-${tier}" title="${esc(rank.name)}">`
    + `${esc(rank.name)}</span>${value}`;
}

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

function inlineMarkdown(text, mentions) {
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

  // `@name` becomes the same styled link the name gets anywhere else, but only
  // for names the server confirmed exist — an unknown one stays plain text
  // rather than a link to nobody. Held behind a sentinel like the others so
  // the emphasis rules cannot chew through the markup.
  if (mentions) {
    out = out.replace(/(^|[^\w.@-])@([A-Za-z0-9_.-]{3,32})/g, (whole, before, raw) => {
      // A trailing dot or dash is sentence punctuation, not part of the name.
      let name = raw;
      let trailing = '';
      while (name.length > 3 && /[.-]$/.test(name)) {
        trailing = name.slice(-1) + trailing;
        name = name.slice(0, -1);
      }
      const role = mentions[name];
      if (role === undefined) return whole;
      maths.push(userLink(name, role));
      return `${before}\u0002${maths.length - 1}\u0002${trailing}`;
    });
  }

  out = out
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[^*])\*([^*]+)\*/g, '$1<em>$2</em>')
    .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, '<a href="$2" rel="noopener noreferrer">$1</a>')
    .replace(/\\\$/g, '$');
  return out
    .replace(/[\u0001\u0002](\d+)[\u0001\u0002]/g, (_, i) => maths[i])
    .replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${codes[i]}</code>`);
}

/** A deliberately small Markdown subset: headings, lists, code, emphasis. */
/* Every username on the judge, so `@name` renders the same way in a statement,
 * a post, a bio, and in the live preview of each — which has no server round
 * trip to attach a resolved map to. Loaded once at boot; empty until then, and
 * an empty roster simply means no mention is linked. */
let mentionRoster = {};

function markdown(source, mentions = mentionRoster) {
  // Strip NULs so statement text can never forge a code-span sentinel.
  const lines = esc((source || '').replace(/[\u0000\u0001\u0002]/g, ''))
    .replace(/\r\n/g, '\n').split('\n');
  const html = [];
  let paragraph = [];
  let list = null;
  let fence = null;

  const flushParagraph = () => {
    if (paragraph.length) {
      html.push(`<p>${inlineMarkdown(paragraph.join(' '), mentions)}</p>`);
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
      html.push(`<h${level}>${inlineMarkdown(heading[2], mentions)}</h${level}>`);
      continue;
    }
    const bullet = line.match(/^\s*[-*+]\s+(.*)$/);
    const numbered = line.match(/^\s*\d+[.)]\s+(.*)$/);
    if (bullet || numbered) {
      flushParagraph();
      const want = bullet ? 'ul' : 'ol';
      if (list !== want) { flushList(); html.push(`<${want}>`); list = want; }
      html.push(`<li>${inlineMarkdown((bullet || numbered)[1], mentions)}</li>`);
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
      <td class="small">${userLink(p.author, p.author_role)}</td>
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
    </table></div>
    ${dmojTable(problems)}`);

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

/* Unsaved form state, kept across a reload.
 *
 * An update now forces a refresh rather than offering one, so anything a
 * person has typed and not yet saved has to survive it. Every editor on the
 * site registers its fields here; they are written on each keystroke and
 * dropped once the form saves successfully.
 */
const FORM_DRAFT_PREFIX = 'stroj:form:';

function keepDraft(name, fields) {
  const key = FORM_DRAFT_PREFIX + name;

  // Restore first, so a reload lands you back where you were.
  let restored = false;
  try {
    const saved = JSON.parse(localStorage.getItem(key) || 'null');
    if (saved) {
      for (const [id, el] of Object.entries(fields)) {
        if (!el || saved[id] === undefined) continue;
        if (el.type === 'checkbox') el.checked = saved[id];
        else el.value = saved[id];
        restored = true;
      }
    }
  } catch { /* a corrupt draft is not worth failing the page over */ }

  const snapshot = () => {
    const data = {};
    for (const [id, el] of Object.entries(fields)) {
      if (!el) continue;
      data[id] = el.type === 'checkbox' ? el.checked : el.value;
    }
    try { localStorage.setItem(key, JSON.stringify(data)); } catch { /* full */ }
  };
  for (const el of Object.values(fields)) {
    if (!el) continue;
    el.addEventListener('input', snapshot);
    el.addEventListener('change', snapshot);
  }
  return { restored, clear: () => localStorage.removeItem(key), snapshot };
}

const draftKey = (slug, language) => `stroj:draft:${slug}:${language}`;

async function viewProblem(slug, params) {
  const contestSlug = params.get('contest');
  const problem = await api(`/api/problems/${encodeURIComponent(slug)}`);
  // Points rate the problem against the archive and the type tags name the
  // technique, so a live contest withholds both. Subtask weights stay: you
  // need them to choose what to attempt, and a percentage gives nothing away.
  const sealed = problem.metadata_sealed;

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
      ${sealed ? '' : `<span class="points-pill">${problem.points} points</span>`}
      ${problem.author ? `<span class="pill">by ${userLink(problem.author, problem.author_role)}</span>` : ''}
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
        ${samples ? `<div class="card"><h2 style="margin-top:0">Samples</h2>${samples}</div>` : ''}
        ${(problem.subtasks || []).length ? `
          <div class="card">
            <h2 style="margin-top:0">Subtasks</h2>
            <p class="muted small">Solve every test in a subtask to earn its share
              of ${sealed ? "the problem's points" : `the ${problem.points} points`}.
              Partial credit counts toward your score.</p>
            <div class="table-wrap"><table><tbody>${problem.subtasks.map((st) => `
              <tr><td class="mono" style="width:1%">${st.idx}</td>
                  <td class="wide muted small">${st.tests} test${st.tests === 1 ? '' : 's'}</td>
                  <td class="num">${st.percent}%</td>
                  ${sealed ? '' : `<td class="num">${pointsPill(Math.round(problem.points * st.percent / 100))}</td>`}
              </tr>`).join('')}</tbody></table></div>
          </div>` : (problem.partial ? `
          <div class="card">
            <h2 style="margin-top:0">Partial scoring</h2>
            <p class="muted small">No subtasks, so you earn the share of tests you
              pass — 10 of 20 tests earns half
              ${sealed ? 'the points' : `of the ${problem.points} points`}.</p>
          </div>` : '')}
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
        ${state.user && state.user.is_admin ? `
        <details class="card">
          <summary>Bulk submit a solution archive</summary>
          <p class="muted small">Every source file in the zip is queued as its own
            submission, with the language taken from its extension. Runs as
            practice, never as a contest entry — this is for reading off the time
            and memory each language needs.</p>
          <input type="file" accept=".zip" id="bulk-file">
          <div id="bulk-result"></div>
        </details>` : ''}
        <div class="card">
          <div class="row">
            <a class="btn small" href="#/problem/${encodeURIComponent(slug)}/ranking">Ranking</a>
            <a class="btn small" href="#/submissions?problem=${encodeURIComponent(slug)}">All submissions</a>
          </div>
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
            <td class="num muted">${memory(s.memory_kb)}</td>
            <td class="muted small" title="${esc(absolute(s.created_at))}">${esc(relative(s.created_at))}</td>
          </tr>`).join('')}</tbody></table></div>`
      : '<p class="muted small">Nothing yet.</p>');
  };
  await refreshMine();
  every(2000, refreshMine);

  const bulk = $('#bulk-file');
  if (bulk) {
    bulk.onchange = async () => {
      if (!bulk.files.length) return;
      const form = new FormData();
      form.append('archive', bulk.files[0]);
      const box = $('#bulk-result');
      box.innerHTML = '<p class="muted small">Queueing…</p>';
      try {
        const result = await api(
          `/api/admin/problems/${encodeURIComponent(slug)}/bulk-submit`,
          { method: 'POST', form });
        // Skipped files are listed rather than summarised: "2 skipped" leaves
        // you wondering whether the one you cared about was among them.
        box.innerHTML = `
          <div class="table-wrap"><table><tbody>${result.submitted.map((s) => `
            <tr><td class="mono small">${esc(s.file)}</td>
                <td class="muted small">${esc(s.language)}</td>
                <td><a href="#/submission/${s.id}">#${s.id}</a></td></tr>`).join('')}
          </tbody></table></div>
          ${result.skipped.length ? `<p class="muted small">Skipped:
            ${result.skipped.map((s) => `${esc(s.file)} — ${esc(s.reason)}`).join('; ')}</p>` : ''}`;
        toast(`Queued ${result.submitted.length} submission(s).`, 'good');
        lastMine = null;
        await refreshMine();
      } catch (err) {
        // Report in place as well as in the toast: left on "Queueing…" this
        // would read as a hang rather than a rejection.
        box.innerHTML = `<p class="small v-WA">${esc(err.message)}</p>`;
        toast(err.message, 'bad');
      } finally {
        bulk.value = '';
      }
    };
  }
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
        ${s.message ? `<h3>Judge output
          ${state.user && state.user.is_admin && s.verdict !== 'CE'
            ? '<span class="pill">admins only</span>' : ''}</h3>
          <pre class="io">${esc(s.message)}</pre>` : ''}
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

/* ---- problem ranking ---- */

async function viewRanking(slug) {
  const data = await api(`/api/problems/${encodeURIComponent(slug)}/ranking`);
  const rows = data.submissions.map((s) => {
    const me = state.user && state.user.username === s.username;
    return `<tr${me ? ' class="you"' : ''}>
      <td class="num rank">${s.rank}</td>
      <td class="wide">${userLink(s.username, s.user_role)}</td>
      <td>${verdictBadge(s.verdict, s.verdict_name)}</td>
      <td class="num">${s.earned_percent}%</td>
      <td class="num muted">${s.time_ms} ms</td>
      <td class="num muted">${memory(s.memory_kb)}</td>
      <td class="small muted">${esc(s.language)}</td>
      <td class="small muted" title="${esc(absolute(s.created_at))}">
        <a href="#/submission/${s.id}">${esc(relative(s.created_at))}</a></td>
    </tr>`;
  }).join('');

  setView(`
    <div class="page-head">
      <a href="#/problem/${encodeURIComponent(slug)}">← ${esc(data.problem.title)}</a>
      <div class="spacer"></div>
      <span class="muted small">${data.submissions.length} judged</span>
    </div>
    <h1>Ranking</h1>
    <p class="muted small">Most of the problem earned first, then fastest, then
      smallest, then earliest. Submissions still in the queue are not listed.</p>
    ${data.submissions.length
      ? `<div class="table-wrap"><table>
          <thead><tr><th class="num">#</th><th>Who</th><th>Verdict</th>
            <th class="num">Earned</th><th class="num">Time</th>
            <th class="num">Memory</th><th>Language</th><th>When</th></tr></thead>
          <tbody>${rows}</tbody></table></div>`
      : '<div class="empty">Nothing has been judged for this problem yet.</div>'}`);
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
      <td class="mono small">${c.rated
        ? '<span class="pill pill-rated">rated</span>'
        : '<span class="muted">unrated</span>'}</td>
      <td class="countdown mono small" data-starts="${esc(c.starts_at)}" data-ends="${esc(c.ends_at)}" data-state="${esc(c.state)}"></td>
    </tr>`).join('');

  setView(`
    <div class="page-head"><h1>Contests</h1></div>
    <div class="table-wrap"><table>
      <thead><tr><th>Contest</th><th>Status</th><th>Starts</th><th>Ends</th><th>Scoring</th><th>Rating</th><th>Clock</th></tr></thead>
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
        <span class="pill ${c.rated ? 'pill-rated' : ''}">${c.rated ? 'rated' : 'unrated'}</span>
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
    // Only once the contest has actually been rated: a column of dashes
    // during a live contest reads as "you gained nothing", not "not yet".
    const showRating = board.rows.some((r) => r.rating);

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
        ${showRating ? `<td class="num">${r.rating
          ? `<span class="delta ${r.rating.delta > 0 ? 'up' : r.rating.delta < 0 ? 'down' : ''}">${
              r.rating.delta > 0 ? '+' : ''}${r.rating.delta}</span>`
            + `<span class="muted small"> ${r.rating.after}</span>`
          : '<span class="muted small">—</span>'}</td>` : ''}
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
             ${showRating ? '<th class="num">Rating</th>' : ''}
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
                   ['solved', 'Solved', 'num'], ['hardest', 'Hardest', 'num'],
                   // Rating measures placing against other people; score
                   // measures what you have solved. Neither implies the other,
                   // so the table carries both rather than picking one.
                   ['rating', 'Rating', 'num']];
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
      <td class="num">${rankBadge(s.rating_rank, { rating: s.rating_rank ? s.rating : null })}</td>
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

/* ---- submission calendar ---- */

/** Lay the reported days out as GitHub does: one column per week, Sunday at
 * the top, `null` for the padding days before the window opens. Days the API
 * left out had no submissions, so they come back as zeroes. */
function activityWeeks(activity) {
  const byDate = new Map((activity.counts || []).map((d) => [d.date, d]));
  const iso = (d) => d.toISOString().slice(0, 10);
  const cursor = new Date(`${activity.since}T00:00:00Z`);
  cursor.setUTCDate(cursor.getUTCDate() - cursor.getUTCDay());
  const end = new Date(`${activity.until}T00:00:00Z`);

  const weeks = [];
  for (; cursor <= end; cursor.setUTCDate(cursor.getUTCDate() + 1)) {
    if (cursor.getUTCDay() === 0) weeks.push([]);
    const date = iso(cursor);
    weeks[weeks.length - 1].push(date < activity.since
      ? null
      : byDate.get(date) || { date, count: 0, accepted: 0 });
  }
  return weeks;
}

const activityLevel = (n) => (n >= 7 ? 4 : n >= 4 ? 3 : n >= 2 ? 2 : n ? 1 : 0);

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

function activityTip(day) {
  const [y, m, d] = day.date.split('-');
  const when = `${MONTHS[Number(m) - 1]} ${Number(d)}, ${y}`;
  if (!day.count) return `No submissions on ${when}`;
  const accepted = day.accepted ? ` (${day.accepted} accepted)` : '';
  return `${day.count} submission${day.count === 1 ? '' : 's'}${accepted} on ${when}`;
}

/* The squares are 11px inside a scrolling strip, so the bubble cannot live in
 * the grid without being clipped; one fixed element, moved to whichever square
 * is under the pointer, is both cheaper and always visible. */
function wireCalendarTips(cal) {
  const tip = document.createElement('div');
  tip.className = 'cal-tip';
  tip.hidden = true;
  cal.append(tip);
  cal.addEventListener('mouseover', (event) => {
    const day = event.target.closest('.cal-day');
    if (!day) return;
    const box = day.getBoundingClientRect();
    tip.textContent = day.dataset.tip;
    tip.hidden = false;
    // Centred on the square, but never past either edge of the window — the
    // first and last columns sit close enough to clip it otherwise.
    const half = tip.offsetWidth / 2;
    tip.style.left = `${Math.min(
      Math.max(box.left + box.width / 2, half + 6), innerWidth - half - 6)}px`;
    tip.style.top = `${box.top - 6}px`;
  });
  cal.addEventListener('mouseleave', () => { tip.hidden = true; });
}

function activityCalendar(activity) {
  const weeks = activityWeeks(activity);
  let labelled = '';
  const months = weeks.map((week) => {
    const day = week.find((d) => d);
    const month = day ? day.date.slice(0, 7) : '';
    if (!month || month === labelled) return '<span></span>';
    labelled = month;
    return `<span>${MONTHS[Number(month.slice(5)) - 1]}</span>`;
  }).join('');

  const grid = weeks.map((week) => `<div class="cal-week">${week.map((day) => (day
    ? `<div class="cal-day l${activityLevel(day.count)}"
        data-tip="${esc(activityTip(day))}"></div>`
    : '<div></div>')).join('')}</div>`).join('');

  return `
    <div class="row" style="justify-content:space-between;align-items:baseline">
      <h2>Submissions</h2>
      <span class="muted small">${activity.total} in the last year</span>
    </div>
    <div class="card">
      <div class="cal-wrap">
        <div class="cal-months">${months}</div>
        <div class="cal">${grid}</div>
      </div>
    </div>`;
}

/** Every rated contest a competitor has entered, newest first.
 *
 * Shown as a list rather than a chart: with weekly contests the interesting
 * question is "what happened at that one", which a line graph hides. */
function ratingHistory(history) {
  if (!history || !history.length) return '';
  const rows = [...history].reverse().map((h) => `
    <tr>
      <td class="wide"><a href="#/contest/${encodeURIComponent(h.contest_slug)}">${esc(h.contest_title)}</a>
        <span class="muted small" title="${esc(absolute(h.at))}">${esc(relative(h.at))}</span></td>
      <td class="num muted">#${h.place}</td>
      <td class="num"><span class="delta ${h.delta > 0 ? 'up' : h.delta < 0 ? 'down' : ''}">${
        h.delta > 0 ? '+' : ''}${h.delta}</span></td>
      <td class="num">${h.rating_after}</td>
      <td>${rankBadge(h.rank)}</td>
    </tr>`).join('');
  return `
    <div class="row" style="justify-content:space-between;align-items:baseline">
      <h2>Rated contests</h2>
      <span class="muted small">${history.length}</span>
    </div>
    <div class="card"><div class="table-wrap"><table><tbody>${rows}</tbody></table></div></div>`;
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
        <div class="row" style="margin-bottom:14px">${rankBadge(u.rating_rank)}</div>
        <div class="row" style="align-items:flex-end;gap:26px">
          <div><div class="muted small">Score</div><div class="score-big">${u.score}</div></div>
          <div><div class="muted small">Standing</div><div class="score-big">${u.rank ? '#' + u.rank : '—'}</div>
            ${u.rank ? `<div class="muted small">of ${u.ranked_of}</div>` : ''}</div>
          <div><div class="muted small">Rating</div>
            <div class="score-big">${u.rated_contests ? u.rating : '—'}</div>
            <div class="muted small">${u.rated_contests
              ? `${u.rated_contests} rated contest${u.rated_contests === 1 ? '' : 's'}`
              : 'no rated contests'}</div></div>
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

    ${activityCalendar(u.activity)}
    ${ratingHistory(u.rating_history)}

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

  wireCalendarTips($('.cal'));

  if (!u.editable) return;
  const view = $('#bio-view');
  const editor = $('#bio-edit');
  const bioDraft = keepDraft(`bio:${u.username}`, { bio: $('#bio-text') });
  // A restored draft means there was unsaved text; show the editor, not the
  // rendered bio, or the work looks lost even though it is right there.
  if (bioDraft.restored) { view.hidden = true; editor.hidden = false; }
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
      bioDraft.clear();
      toast('Profile updated.', 'good');
    } catch (err) { toast(err.message, 'bad'); }
  };
}

/* ------------------------------------------------------------ admin editor */

/* Everything an admin writes — a post, a problem, a contest — is authored on
 * one page: a card of metadata fields, one Markdown body with a live preview,
 * and Save. Only the field list and the API calls differ between them, so
 * those are data in ADMIN_FORMS rather than three hand-written pages, and
 * creating and editing are the same page with the slug fixed or not. */

/* Toolbar actions over the selection. `wrap` surrounds it, `prefix` starts the
 * line it is on; `key` is the Cmd/Ctrl shortcut that does the same thing. */
const MD_TOOLS = [
  { label: 'B', title: 'Bold  ⌘B', key: 'b', wrap: ['**', '**'] },
  { label: 'I', title: 'Italic  ⌘I', key: 'i', wrap: ['*', '*'] },
  { label: '`', title: 'Code  ⌘E', key: 'e', wrap: ['`', '`'] },
  { label: '$', title: 'Math', wrap: ['$', '$'] },
  { label: 'H', title: 'Heading', prefix: '## ' },
  { label: '•', title: 'Bullet', prefix: '- ' },
  { label: '↗', title: 'Link  ⌘K', key: 'k', wrap: ['[', '](https://)'] },
];

function markdownPane(value) {
  const tab = (mode, label) => `<button type="button" class="md-tab${
    mode === 'write' ? ' on' : ''}" data-mode="${mode}">${label}</button>`;
  const tool = (t, i) => `<button type="button" class="md-tool" data-tool="${i}"
    title="${esc(t.title)}">${esc(t.label)}</button>`;
  return `
    <div class="md" id="md" data-mode="write">
      <div class="md-bar">
        <div class="md-tabs">${tab('write', 'Write')}${tab('preview', 'Preview')}${tab('split', 'Split')}</div>
        <div class="spacer"></div>
        <div class="md-tools">${MD_TOOLS.map(tool).join('')}</div>
      </div>
      <div class="md-panes">
        <textarea class="md-src code" spellcheck="false">${esc(value || '')}</textarea>
        <div class="md-preview statement"></div>
      </div>
    </div>`;
}

/** Live preview, tab switching, toolbar and its shortcuts, over one textarea. */
function bindMarkdownPane(root) {
  const src = $('.md-src', root);
  const preview = $('.md-preview', root);
  const render = () => { preview.innerHTML = markdown(src.value); };

  // setRangeText keeps the browser's own undo stack, which rewriting `value`
  // by hand would throw away on every toolbar press.
  const splice = (text, from, to, caret) => {
    src.setRangeText(text, from, to, 'end');
    src.selectionStart = src.selectionEnd = caret;
    src.focus();
    render();
  };

  const apply = (tool) => {
    const { selectionStart: a, selectionEnd: b, value } = src;
    if (tool.prefix) {
      const line = value.lastIndexOf('\n', a - 1) + 1;
      splice(tool.prefix, line, line, a + tool.prefix.length);
      return;
    }
    const [open, close] = tool.wrap;
    // With nothing selected the caret lands between the delimiters, ready to
    // type; with a selection it lands after what was just wrapped.
    splice(open + value.slice(a, b) + close, a, b,
      a === b ? a + open.length : b + open.length + close.length);
  };

  root.onclick = (event) => {
    const tab = event.target.closest('.md-tab');
    if (tab) {
      root.dataset.mode = tab.dataset.mode;
      $$('.md-tab', root).forEach((t) => t.classList.toggle('on', t === tab));
      return;
    }
    const tool = event.target.closest('.md-tool');
    if (tool) apply(MD_TOOLS[Number(tool.dataset.tool)]);
  };

  src.oninput = render;
  src.onkeydown = (event) => {
    if (event.key === 'Tab') {
      event.preventDefault();
      splice('    ', src.selectionStart, src.selectionEnd, src.selectionStart + 4);
      return;
    }
    if (!event.metaKey && !event.ctrlKey) return;
    const tool = MD_TOOLS.find((t) => t.key === event.key.toLowerCase());
    if (!tool) return;
    event.preventDefault();
    apply(tool);
  };

  render();
  return src;
}

/** One metadata field. `type` picks the control; the rest is layout. */
function adminField(f, value) {
  const id = `f-${f.k}`;
  const wrap = (inner) =>
    `<label style="flex:${f.flex || 1};min-width:${f.w || 150}px">${esc(f.label)}${inner}</label>`;
  switch (f.type) {
    case 'check':
      return `<label class="check"><input type="checkbox" id="${id}"${value ? ' checked' : ''}>
        ${esc(f.label)}</label>`;
    case 'select':
      return wrap(`<select id="${id}">${f.options.map((o) =>
        `<option value="${esc(o)}"${o === value ? ' selected' : ''}>${esc(o)}</option>`).join('')}</select>`);
    case 'chips':
      return `<label style="flex:100%">${esc(f.label)}${typeChips(id, f.all, value || [], true)}</label>`;
    /* Whether anyone but an admin can see this is the one setting worth being
     * wrong about, so it gets a bar of its own above the form rather than a
     * checkbox among the others — two labelled states and, in words, who can
     * see it right now. The checkbox behind it is what carries the value, so
     * reading and drafting the field stay the same as any other. */
    case 'visibility':
      return `
        <div class="vis" data-on="${value ? 1 : 0}">
          <input type="checkbox" id="${id}" hidden${value ? ' checked' : ''}>
          <div class="seg">
            <button type="button" class="seg-btn" data-on="1">${esc(f.on)}</button>
            <button type="button" class="seg-btn" data-on="0">${esc(f.off)}</button>
          </div>
          <span class="vis-note"></span>
        </div>`;
    case 'number':
      return wrap(`<input id="${id}" type="number" value="${esc(value)}"
        min="${f.min}" max="${f.max}"${f.step ? ` step="${f.step}"` : ''}>`);
    case 'time':
      return wrap(`<input id="${id}" type="datetime-local" value="${esc(localField(value))}">`);
    default:
      return wrap(`<input id="${id}" value="${esc(value ?? '')}" placeholder="${esc(f.hint || '')}">`);
  }
}

/** Keep the segmented control, its note and its checkbox saying one thing. */
function bindVisibility(f) {
  const box = $(`#f-${f.k}`);
  const bar = box.closest('.vis');
  const paint = () => {
    bar.dataset.on = box.checked ? 1 : 0;
    $$('.seg-btn', bar).forEach((b) => b.classList.toggle('on', (b.dataset.on === '1') === box.checked));
    $('.vis-note', bar).textContent = box.checked ? f.note.on : f.note.off;
  };
  bar.onclick = (event) => {
    const button = event.target.closest('.seg-btn');
    if (!button) return;
    box.checked = button.dataset.on === '1';
    // The draft listens on the checkbox, which a scripted change does not fire.
    box.dispatchEvent(new Event('input', { bubbles: true }));
    paint();
  };
  paint();
}

function readField(f) {
  if (f.type === 'chips') return chosenTypes(`f-${f.k}`);
  const el = $(`#f-${f.k}`);
  if (f.type === 'check' || f.type === 'visibility') return el.checked;
  if (f.type === 'number') return Number(el.value);
  // An empty datetime is left as-is so the judge can name the problem with it,
  // rather than throwing "Invalid time value" out of the Date constructor.
  if (f.type === 'time') return el.value ? new Date(el.value).toISOString() : '';
  return el.value.trim();
}

const slugify = (s) => s.toLowerCase()
  .replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 64);

/* ---- editor extras: the parts only one kind of thing has ---- */

/* Test data cannot be attached to a problem that does not exist yet, so a new
 * problem's archive is validated when it is picked and uploaded once Save has
 * created something to attach it to. */
function testDataPanel() {
  let pending = null;
  // Receipt for the copy the inspect call already left on the judge.
  let pendingToken = '';

  return {
    html: `
      <div class="card">
        <label>Test data — a zip of <code>1.in</code> / <code>1.out</code> pairs (optional)
          <input type="file" id="p-tests" accept=".zip" style="max-width:340px"></label>
        <p class="small muted" id="p-tests-status" style="margin:0">Checked as soon as you pick it,
          before anything is created. Files whose name contains <code>sample</code> become visible
          samples; the rest stay hidden. You can also add it later from the admin page.</p>
      </div>`,

    bind() {
      $('#p-tests').onchange = async () => {
        const input = $('#p-tests');
        const status = $('#p-tests-status');
        pending = null;
        pendingToken = '';
        status.className = 'small muted';
        status.style.color = '';
        if (!input.files.length) { status.textContent = ''; return; }

        status.textContent = 'Checking archive…';
        const form = new FormData();
        form.append('archive', input.files[0]);
        try {
          const found = await api('/api/admin/testdata/inspect', { method: 'POST', form });
          pending = input.files[0];
          pendingToken = found.token || '';
          status.className = 'small';
          status.style.color = 'var(--ok)';
          status.textContent =
            `✓ ${found.tests} test${found.tests === 1 ? '' : 's'}, ` +
            `${found.samples} sample${found.samples === 1 ? '' : 's'} — first input starts "` +
            `${found.preview.input.trim().slice(0, 40)}"`;
        } catch (err) {
          status.className = 'small error';
          status.textContent = err.message;
        }
      };
    },

    check() {
      if ($('#p-tests').files.length && !pending) {
        throw new Error('That test archive was rejected — fix it or clear the field.');
      }
    },

    async after(slug) {
      if (!pending) {
        toast('Problem created — it needs test data before it can be judged.', 'good');
        return;
      }
      const form = new FormData();
      // Inspection already carried these bytes to the judge; send the receipt
      // instead of the archive. A real test set is tens of megabytes, and
      // uploading it twice was the slowest step in authoring a problem.
      if (pendingToken) form.append('token', pendingToken);
      else form.append('archive', pending);
      const url = `/api/admin/problems/${encodeURIComponent(slug)}/tests/upload`;
      try {
        let result;
        try {
          result = await api(url, { method: 'POST', form });
        } catch (err) {
          // 410: the staged copy expired or was swept. We still hold the file.
          if (err.status !== 410) throw err;
          const retry = new FormData();
          retry.append('archive', pending);
          result = await api(url, { method: 'POST', form: retry });
        }
        toast(`Created with ${result.tests} test case(s).`, 'good');
      } catch (err) {
        // The archive passed inspection, so this is unexpected — but never
        // leave a testless problem behind because the second call failed.
        await api(`/api/admin/problems/${encodeURIComponent(slug)}`, { method: 'DELETE' })
          .catch(() => {});
        throw new Error(`Test data failed to attach, problem not created: ${err.message}`);
      }
    },
  };
}

/* Per-language limits save themselves rather than going out with the page: the
 * rest of the form is an unsaved draft, and re-rendering the route to show the
 * new numbers would throw a half-written statement away. */
async function limitsPanel(slug) {
  const url = `/api/admin/problems/${encodeURIComponent(slug)}/limits`;
  const { limits } = await api(url);
  // Only the rows an author actually touches are sent, so opening the editor
  // and saving does not silently pin every language to its derived value.
  const touched = new Set();

  async function refresh(message) {
    const { limits: fresh } = await api(url);
    $('#e-limits-rows').innerHTML = limitRows(fresh);
    touched.clear();
    bindRows();
    $('#e-limits-status').textContent = message || '';
  }

  function bindRows() {
    $$('.lim').forEach((input) => {
      input.oninput = () => {
        touched.add(input.dataset.lang);
        $('#e-limits-status').textContent = '';
      };
    });
    $$('[data-clear-limit]').forEach((button) => {
      button.onclick = async () => {
        const lang = button.dataset.clearLimit;
        button.disabled = true;
        try {
          await api(url, { method: 'PUT', body: { limits: { [lang]: null } } });
          await refresh(`${lang} back to the derived limit.`);
        } catch (err) {
          toast(err.message, 'bad');
          button.disabled = false;
        }
      };
    });
  }

  return {
    html: `
      <details class="card"${Object.values(limits).some((l) => l.measured) ? ' open' : ''}>
        <summary>Per-language limits</summary>
        <p class="muted small">Set these from a measured run of the intended
          solution in each language — the gap between runtimes depends on the
          problem, not just the language. Clear a row to fall back to the base
          limit above, scaled by that language's multiplier.</p>
        <div class="table-wrap"><table class="limits-table">
          <thead><tr><th>Language</th><th>Time (ms)</th>
            <th>Memory (MiB)</th><th>Source</th><th></th></tr></thead>
          <tbody id="e-limits-rows">${limitRows(limits)}</tbody>
        </table></div>
        <div class="row end">
          <span class="muted small spacer" id="e-limits-status"></span>
          <button class="small primary" id="e-save-limits">Save limits</button>
        </div>
      </details>`,

    bind() {
      bindRows();
      $('#e-save-limits').onclick = async () => {
        const button = $('#e-save-limits');
        const body = {};
        for (const lang of touched) {
          const field = (f) => $(`.lim[data-lang="${CSS.escape(lang)}"][data-f="${f}"]`);
          body[lang] = {
            time_limit_ms: Number(field('time').value),
            memory_limit_mb: Number(field('memory').value),
          };
        }
        if (!Object.keys(body).length) {
          $('#e-limits-status').textContent = 'Nothing changed.';
          return;
        }
        button.disabled = true;
        try {
          await api(url, { method: 'PUT', body: { limits: body } });
          const n = Object.keys(body).length;
          await refresh(`Saved ${n} language${n === 1 ? '' : 's'}.`);
        } catch (err) {
          toast(err.message, 'bad');
        } finally {
          button.disabled = false;
        }
      };
    },
  };
}

/* A contest's problem set is a separate endpoint from the contest itself, so
 * it rides along with Save rather than being its own form. Seeing the current
 * set is the point: otherwise saving an empty box silently empties it. */
function contestProblemsPanel(detail) {
  const warning = {
    running: 'This contest is running. Moving the start time recomputes every'
      + ' penalty on the scoreboard.',
    ended: 'This contest has ended. Editing it rewrites results that entrants'
      + ' have already seen.',
  }[detail.state];

  return {
    html: `
      <div class="card">
        <label>Problem set — slugs in order, comma-separated
          <input id="c-problems" placeholder="slug-a, slug-b, …"
            value="${esc((detail.problems || []).map((p) => p.slug).join(', '))}"></label>
        ${warning ? `<p class="small muted" style="margin:0">${warning}</p>` : ''}
      </div>`,

    async after(slug) {
      const slugs = $('#c-problems').value.split(',').map((x) => x.trim()).filter(Boolean);
      await api(`/api/admin/contests/${encodeURIComponent(slug)}/problems`, {
        method: 'PUT', body: { problems: slugs.map((x) => ({ slug: x })) },
      });
    },
  };
}

/* ---- what each kind of thing is made of ---- */

const ADMIN_FORMS = {
  post: {
    noun: 'post',
    slugHint: 'round-2-results',
    body: { k: 'body', label: 'Body' },
    viewLabel: 'view in stream',
    view: (slug) => `#/post/${encodeURIComponent(slug)}`,
    fields: [
      {
        k: 'published', type: 'visibility', on: 'Published', off: 'Draft',
        note: {
          on: 'Everyone sees this in the stream.',
          off: 'Only admins can see this. It stays out of the stream until you publish it.',
        },
      },
      { k: 'title', label: 'Title', flex: 3, w: 240, hint: 'Round 2 results' },
      { k: 'pinned', label: 'pinned', type: 'check' },
    ],
    blank: () => ({ title: '', body: '', pinned: false, published: true }),
    load: (slug) => api(`/api/posts/${encodeURIComponent(slug)}`),
    save: async (v, slug) => {
      const body = {
        title: v.title || v.slug, body: v.body, pinned: v.pinned, published: v.published,
      };
      if (slug) await api(`/api/admin/posts/${encodeURIComponent(slug)}`, { method: 'PATCH', body });
      else await api('/api/admin/posts', { method: 'POST', body: { ...body, slug: v.slug } });
    },
  },

  problem: {
    noun: 'problem',
    slugHint: 'two-sum',
    body: { k: 'statement', label: 'Statement' },
    viewLabel: 'view as solver',
    view: (slug) => `#/problem/${encodeURIComponent(slug)}`,
    subtitle: (p) => `${p.test_count} test${p.test_count === 1 ? '' : 's'}`,
    fields: [
      {
        k: 'visible', type: 'visibility', on: 'Visible', off: 'Hidden',
        note: {
          on: 'Listed on the problems page for everyone.',
          off: 'Off the problems list and unreadable — except to admins, and to'
            + ' everyone once a contest containing it starts.',
        },
      },
      { k: 'title', label: 'Title', flex: 3, w: 220, hint: 'Two Sum' },
      { k: 'points', label: 'Points', type: 'number', min: 1, max: 10000, w: 110 },
      { k: 'author', label: 'Author', flex: 2, w: 160, hint: '(you)' },
      { k: 'time_limit_ms', label: 'Time (ms)', type: 'number', min: 100, max: 60000, w: 120 },
      { k: 'memory_limit_mb', label: 'Memory (MiB)', type: 'number', min: 16, max: 4096, w: 130 },
      { k: 'checker', label: 'Checker', type: 'select', options: ['token', 'exact', 'float'], w: 120 },
      { k: 'float_eps', label: 'Float epsilon', type: 'number', min: 0, max: 1, step: 'any', w: 130 },
      { k: 'types', label: 'Types', type: 'chips' },
      { k: 'partial', label: 'partial scoring', type: 'check' },
    ],
    blank: () => ({
      title: '', statement: '', points: 100, author: '', time_limit_ms: 1000,
      memory_limit_mb: 256, checker: 'token', float_eps: 0.000001, types: [],
      partial: false, visible: true,
    }),
    // The problem list comes along for the type vocabulary: chips offer what
    // other problems already use, so authors reuse a type instead of coining one.
    async load(slug) {
      return api(`/api/problems/${encodeURIComponent(slug)}`);
    },
    async prepare(form, data) {
      const { problems } = await api('/api/problems');
      const types = form.fields.find((f) => f.k === 'types');
      types.all = [...new Set(problems.flatMap((p) => p.types).concat(data.types || []))].sort();
    },
    extra: (slug) => (slug ? limitsPanel(slug) : testDataPanel()),
    save: async (v, slug) => {
      const body = {
        title: v.title || v.slug, statement: v.statement, points: v.points,
        author: v.author || null, time_limit_ms: v.time_limit_ms,
        memory_limit_mb: v.memory_limit_mb, checker: v.checker, float_eps: v.float_eps,
        types: v.types, partial: v.partial, visible: v.visible,
      };
      if (slug) await api(`/api/admin/problems/${encodeURIComponent(slug)}`, { method: 'PATCH', body });
      else await api('/api/admin/problems', { method: 'POST', body: { ...body, slug: v.slug } });
    },
  },

  contest: {
    noun: 'contest',
    slugHint: 'round-2',
    body: { k: 'description', label: 'Description' },
    viewLabel: 'view contest',
    view: (slug) => `#/contest/${encodeURIComponent(slug)}`,
    subtitle: (c) => STATE_LABEL[c.state].toLowerCase(),
    fields: [
      { k: 'title', label: 'Title', flex: 3, w: 220, hint: 'stroj Open Round 2' },
      { k: 'scoring', label: 'Scoring', type: 'select', options: ['icpc', 'ioi'], w: 110 },
      { k: 'penalty_minutes', label: 'Penalty (min)', type: 'number', min: 0, max: 1440, w: 130 },
      { k: 'freeze_minutes', label: 'Freeze (min before end, 0 = none)', type: 'number', min: 0, max: 1440, w: 220 },
      { k: 'starts_at', label: 'Starts (your local time)', type: 'time', w: 210 },
      { k: 'ends_at', label: 'Ends (your local time)', type: 'time', w: 210 },
      { k: 'rated', label: 'rated — results move competitors\u2019 ratings', type: 'check' },
    ],
    blank: () => {
      const now = new Date(Date.now() + state.clockSkewMs);
      return {
        title: '', description: '', scoring: 'icpc', penalty_minutes: 20, freeze_minutes: 0,
        // Off by default: a contest has to be deliberately declared rated.
        rated: false,
        starts_at: now.toISOString(), ends_at: new Date(now.getTime() + 3 * 3600e3).toISOString(),
      };
    },
    load: (slug) => api(`/api/contests/${encodeURIComponent(slug)}`),
    extra: (slug, data) => (slug ? contestProblemsPanel(data) : null),
    save: async (v, slug) => {
      const body = {
        title: v.title || v.slug, description: v.description, scoring: v.scoring,
        penalty_minutes: v.penalty_minutes, freeze_minutes: v.freeze_minutes,
        starts_at: v.starts_at, ends_at: v.ends_at, rated: v.rated,
      };
      if (slug) await api(`/api/admin/contests/${encodeURIComponent(slug)}`, { method: 'PATCH', body });
      else await api('/api/admin/contests', { method: 'POST', body: { ...body, slug: v.slug } });
    },
  },
};

/* Create and edit share a key space so neither can clobber the other's draft. */
const adminDraftName = (kind, slug) => (slug ? `${kind}:${slug}` : `new-${kind}`);

async function viewAdminEditor(kind, slug) {
  if (!state.user || !state.user.is_admin) {
    setView('<div class="empty">Admins only.</div>');
    return;
  }
  const form = ADMIN_FORMS[kind];
  const creating = !slug;
  const data = creating ? form.blank() : await form.load(slug);
  if (form.prepare) await form.prepare(form, data);
  const extra = form.extra ? await form.extra(slug, data) : null;
  // Visibility leads the page instead of sitting in the form: it is the one
  // setting whose wrong value is invisible until someone reports not seeing it.
  const vis = form.fields.find((f) => f.type === 'visibility');

  setView(`
    <div class="page-head">
      <a href="#/admin">← Admin</a>
      <div class="spacer"></div>
      ${creating ? '' : `<a class="pill" href="${form.view(slug)}">${form.viewLabel}</a>`}
    </div>
    <h1 style="margin-bottom:4px">${creating ? `New ${form.noun}` : `Edit ${esc(data.title)}`}</h1>
    <p class="muted small" style="margin-top:0">${creating
      ? `The slug is this ${form.noun}'s permanent address and cannot be changed later.`
      : `<span class="mono">${esc(slug)}</span>${form.subtitle ? ` · ${esc(form.subtitle(data))}` : ''}`}</p>

    ${vis ? adminField(vis, data[vis.k]) : ''}

    <div class="card"><div class="row">
      ${creating ? adminField({ k: 'slug', label: 'Slug', flex: 2, w: 190, hint: form.slugHint }, '') : ''}
      ${form.fields.filter((f) => f !== vis).map((f) => adminField(f, data[f.k])).join('')}
    </div></div>

    ${extra ? extra.html : ''}
    ${markdownPane(data[form.body.k])}

    <div class="row end" style="margin-top:14px">
      <span class="muted small spacer" id="e-status"></span>
      <button class="primary" id="e-save">${creating ? `Create ${form.noun}` : 'Save changes'}</button>
    </div>`, { wide: true });

  form.fields.filter((f) => f.type === 'chips').forEach((f) => bindTypeChips(`f-${f.k}`));
  if (extra && extra.bind) extra.bind();

  // A forced refresh must not cost an author their half-written statement.
  // Chips are not form controls, so they sit this out.
  const kept = { [form.body.k]: $('.md-src') };
  for (const f of form.fields) if (f.type !== 'chips') kept[f.k] = $(`#f-${f.k}`);
  if (creating) kept.slug = $('#f-slug');
  const draft = keepDraft(adminDraftName(kind, slug), kept);

  // After the draft, so a restored value is what the bar paints itself from.
  if (vis) bindVisibility(vis);
  const src = bindMarkdownPane($('#md'));

  // The slug is the permalink; keep it following the title until someone types
  // one by hand — including across a restored draft that already has one.
  if (creating) {
    const slugField = $('#f-slug');
    if (slugField.value) slugField.dataset.touched = '1';
    slugField.oninput = () => { slugField.dataset.touched = '1'; };
    $('#f-title').oninput = () => {
      if (!slugField.dataset.touched) slugField.value = slugify($('#f-title').value);
    };
  }

  $('#e-save').onclick = async () => {
    const button = $('#e-save');
    const status = $('#e-status');
    button.disabled = true;
    status.textContent = 'Saving…';
    try {
      if (extra && extra.check) extra.check();
      const values = { slug: creating ? $('#f-slug').value.trim() : slug, [form.body.k]: src.value };
      for (const f of form.fields) values[f.k] = readField(f);

      await form.save(values, slug);
      if (extra && extra.after) await extra.after(values.slug);
      draft.clear();
      if (creating) {
        // testDataPanel toasts its own outcome, since only it knows whether
        // the problem came with test data.
        if (!(extra && extra.after)) toast(`${form.noun[0].toUpperCase()}${form.noun.slice(1)} created.`, 'good');
        location.hash = '#/admin';
      } else {
        status.textContent = 'Saved.';
        toast('Saved.', 'good');
      }
    } catch (err) {
      status.textContent = '';
      toast(err.message, 'bad');
    } finally {
      button.disabled = false;
    }
  };
}

/* ------------------------------------------------------------- admin index */

async function viewAdmin() {
  if (!state.user || !state.user.is_admin) {
    setView('<div class="empty">Admins only.</div>');
    return;
  }
  const [{ problems }, { contests }, { users }, { posts }] = await Promise.all([
    api('/api/problems'), api('/api/contests'), api('/api/admin/users'),
    api('/api/posts?limit=100'),
  ]);

  /* Hidden has to be legible scanning straight down the column, rather than by
   * reading each word — it is the state an admin is looking for. */
  const statePill = (on, live, hidden) => (on
    ? `<span class="pill">${live}</span>`
    : `<span class="pill warn">${hidden}</span>`);

  const head = (title, kind) => `
    <div class="row section-head">
      <h2>${title}</h2>
      <div class="spacer"></div>
      <a class="btn small primary" href="#/admin/new/${kind}">+ New ${kind}</a>
    </div>`;

  const postRows = posts.map((p) => `
    <tr>
      <td class="wide"><a href="#/post/${encodeURIComponent(p.slug)}">${esc(p.title)}</a></td>
      <td class="mono small muted">${esc(p.slug)}</td>
      <td>${statePill(p.published, 'published', 'draft')}
        ${p.pinned ? '<span class="pill">pinned</span>' : ''}</td>
      <td class="muted small">${esc(relative(p.created_at))}</td>
      <td>
        <div class="row">
          <a class="btn small" href="#/admin/post/${encodeURIComponent(p.slug)}">Edit</a>
          <button class="small" data-publish="${esc(p.slug)}" data-published="${p.published ? 1 : 0}">${p.published ? 'Hide' : 'Publish'}</button>
          <button class="small" data-pin="${esc(p.slug)}" data-pinned="${p.pinned ? 1 : 0}">${p.pinned ? 'Unpin' : 'Pin'}</button>
          <button class="small danger" data-delete-post="${esc(p.slug)}">Delete</button>
        </div>
      </td>
    </tr>`).join('');

  const problemRows = problems.map((p) => `
    <tr>
      <td class="wide"><a href="#/problem/${encodeURIComponent(p.slug)}">${esc(p.title)}</a></td>
      <td class="mono small muted">${esc(p.slug)}</td>
      <td>${statePill(p.visible, 'visible', 'hidden')}</td>
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
          <a class="btn small" href="#/admin/contest/${encodeURIComponent(c.slug)}">Edit</a>
          <button class="small danger" data-delete-contest="${esc(c.slug)}">Delete</button>
        </div>
      </td>
    </tr>`).join('');

  setView(`
    <div class="page-head"><h1>Admin</h1></div>

    ${head('Posts', 'post')}
    <div class="table-wrap"><table>
      <thead><tr><th>Title</th><th>Slug</th><th>State</th><th>Posted</th><th>Actions</th></tr></thead>
      <tbody>${postRows || '<tr><td colspan="5" class="muted">None yet.</td></tr>'}</tbody></table></div>

    ${head('Problems', 'problem')}
    <div class="table-wrap"><table>
      <thead><tr><th>Title</th><th>Slug</th><th>State</th><th>Actions</th></tr></thead>
      <tbody>${problemRows || '<tr><td colspan="4" class="muted">None yet.</td></tr>'}</tbody></table></div>

    ${head('Contests', 'contest')}
    <div class="table-wrap"><table>
      <thead><tr><th>Title</th><th>Slug</th><th>State</th><th>Actions</th></tr></thead>
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

  $$('[data-publish]').forEach((button) => {
    button.onclick = guard(async () => {
      await api(`/api/admin/posts/${encodeURIComponent(button.dataset.publish)}`, {
        method: 'PATCH', body: { published: button.dataset.published !== '1' },
      });
      route();
    });
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
      case 'problem':
        if (parts[2] === 'ranking') await viewRanking(decodeURIComponent(parts[1]));
        else await viewProblem(decodeURIComponent(parts[1] || ''), params);
        break;
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
      // "#/admin/new/post" creates one, "#/admin/post/x" edits it — the same
      // editor either way, so the route only has to decide which.
      case 'admin':
        if (parts[1] === 'new' && ADMIN_FORMS[parts[2]]) await viewAdminEditor(parts[2]);
        else if (ADMIN_FORMS[parts[1]] && parts[2]) {
          await viewAdminEditor(parts[1], decodeURIComponent(parts[2]));
        } else await viewAdmin();
        break;
      default: location.hash = '#/home';
    }
  } catch (err) {
    if (err.status === 401) setView(requireSignIn('You need to sign in to see this.'));
    else setView(`<div class="empty"><strong>${esc(err.message)}</strong></div>`);
  }
}

/* --------------------------------------------------------- version watch */

/* Three commits matter, not two:
 *
 *   pageCommit   what the HTML in this tab was built from — what you are using
 *   frontend     what the site is serving right now (/version.json)
 *   backend      what the judge is running right now (/api/version)
 *
 * The two halves deploy independently — Vercel on push, the judge on its
 * webhook — so during a rollout they disagree for a while. A mismatched pair
 * cannot be reasoned about: the page may call an endpoint that does not exist
 * yet, or read a field the judge stopped sending. So a mismatch blocks the
 * site outright, while a merely stale tab, sitting on a pair that does agree,
 * only earns a warning. */

const VERSION_POLL_MS = 20000;

const PAGE_COMMIT = (() => {
  const meta = document.querySelector('meta[name="stroj-commit"]');
  const value = meta && meta.content;
  // A checkout served straight from disk never gets stamped.
  return !value || value.startsWith('__') ? null : value;
})();

let blockedByUpdate = false;
/* Set once boot has waited out the whole grace period on a silent judge. From
   then on the "no judge connected" diagnostic is the more useful screen, so
   the watcher must stop covering it every time it polls. */
let bootGaveUp = false;

async function fetchJson(url) {
  try {
    // The whole point is to see past a cached copy of these two files.
    const response = await fetch(url, { cache: 'no-store' });
    return response.ok ? await response.json() : null;
  } catch { return null; }
}

async function deployedCommits() {
  const [frontend, backend] = await Promise.all([
    fetchJson('/version.json'), fetchJson('/api/version'),
  ]);
  const usable = (v) => (v && v.commit && v.commit !== 'unknown' ? v.commit : null);
  return { frontend: usable(frontend), backend: usable(backend) };
}

function showUpdating(detail) {
  blockedByUpdate = true;
  $('#updating-detail').textContent = detail;
  $('#updating').hidden = false;
}

/* Deliberately not dismissible. The warning is about a real hazard — this tab
   may call an endpoint that has changed shape — and that hazard does not go
   away when the notice is closed, so neither should the notice. */
function hideUpdating() {
  blockedByUpdate = false;
  $('#updating').hidden = true;
}

/* Reloading a tab that is merely behind, rather than asking it to.
 *
 * The banner this replaces left people running a page that could call an
 * endpoint which had changed shape. Every editor now writes its draft on each
 * keystroke, so a refresh costs nothing — and the one thing that must not
 * happen is a loop, if a cached page keeps coming back stale. Reload at most
 * once per target commit, and after that leave the tab alone.
 */
const RELOAD_MARK = 'stroj:reloaded-for';

function refreshForUpdate(target) {
  if (sessionStorage.getItem(RELOAD_MARK) === target) return false;
  try { sessionStorage.setItem(RELOAD_MARK, target); } catch { /* private mode */ }
  location.reload();
  return true;
}

/**
 * What the three commits mean, as one decision.
 *
 *   'unknown'      nothing to compare — say nothing, carry on
 *   'backend-down' the site is deployed but the judge is not answering
 *   'updating'     both answer, and they disagree
 *   'stale'        they agree, but this tab predates them
 *   'current'      nothing to do
 *
 * `backend-down` is deliberately separate from `unknown`. Folding them
 * together let a mid-restart judge read as "nothing to compare", so the site
 * loaded and then failed every call — which is the "no judge connected" page
 * appearing during a routine update.
 */
function versionState(page, frontend, backend) {
  // No version.json at all: a self-hosted judge serving its own files, or a
  // dev checkout that was never stamped. There is genuinely nothing to check.
  if (!frontend) return 'unknown';
  // The site is deployed, so version.json answered — a judge that does not is
  // restarting, or missing.
  if (!backend) return 'backend-down';
  if (frontend !== backend) return 'updating';
  if (page && page !== frontend) return 'stale';
  return 'current';
}

/** Poll once and act. Returns the verdict so boot can decide whether to load. */
async function checkVersions() {
  const { frontend, backend } = await deployedCommits();
  const verdict = versionState(PAGE_COMMIT, frontend, backend);

  if (verdict === 'updating') {
    showUpdating(`frontend ${frontend.slice(0, 7)} · backend ${backend.slice(0, 7)}`);
    return verdict;
  }
  if (verdict === 'backend-down') {
    if (!bootGaveUp) showUpdating('waiting for the judge to answer');
    return verdict;
  }
  if (verdict === 'unknown') return verdict;

  // Back in agreement — either from a mismatch, or from a judge that has
  // finally answered. Reload rather than resuming in place: this tab is very
  // likely running the frontend from before the update, and drafts are saved
  // to localStorage on every keystroke, so nothing is lost by doing so.
  if (blockedByUpdate || bootGaveUp) {
    location.reload();
    return verdict;
  }
  if (verdict === 'stale') refreshForUpdate(frontend);
  return verdict;
}

function startVersionWatch() {
  // Deliberately not registered with `every()`, which route() clears on every
  // navigation — this has to keep running for the life of the tab.
  setInterval(() => { checkVersions().catch(() => {}); }, VERSION_POLL_MS);
}

/* ------------------------------------------------------------------- boot */

/* How long to keep waiting for a judge that is not answering before deciding
 * it is not coming back. A container restart is seconds; anything past this is
 * a deployment problem, and the operator needs to see that instead. */
const BOOT_WAIT_MS = 45000;
const BOOT_RETRY_MS = 1500;

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function boot() {
  startVersionWatch();

  // The page must not appear until the judge behind it is actually answering.
  // Rendering first and discovering that afterwards is what put a "no judge
  // connected" screen in front of people during a routine update.
  const deadline = Date.now() + BOOT_WAIT_MS;
  let lastError = null;
  let loaded = false;

  for (;;) {
    const verdict = await checkVersions().catch(() => 'unknown');

    // Both halves answer but disagree: never load, and never time out either.
    // The watcher reloads the tab once they converge.
    if (verdict === 'updating') return;

    if (verdict === 'backend-down') {
      if (Date.now() >= deadline) break;
      await sleep(BOOT_RETRY_MS);
      continue;
    }

    try {
      const [me, langs, config, version, roster] = await Promise.all([
        api('/api/auth/me'), api('/api/languages'), api('/api/config'),
        api('/api/version').catch(() => null),
        api('/api/mentions').catch(() => null),
      ]);
      mentionRoster = (roster && roster.users) || {};
      state.user = me.user;
      state.languages = langs.languages;
      state.defaultLanguage = langs.default;
      state.config = config;
      state.version = version;
      loaded = true;
      break;
    } catch (err) {
      // The judge answered /api/version a moment ago and has gone away since,
      // or one call lost the race with a restart. Same situation — wait.
      lastError = err;
      if (Date.now() >= deadline) break;
      showUpdating('waiting for the judge to answer');
      await sleep(BOOT_RETRY_MS);
    }
  }

  if (!loaded) {
    // Still not answering after the whole grace period. This is no longer a
    // rollout, so show the diagnostic instead of waiting forever — and stop
    // the watcher from painting over it on its next poll.
    bootGaveUp = true;
    hideUpdating();
    const err = lastError || new Error('the judge did not respond');
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
