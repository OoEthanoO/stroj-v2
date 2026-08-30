'use strict';

/* The admin page's lists: searching them, counting them, and drawing a page at
 * a time so that a site with a term of problems behind it opens like a new one.
 *
 * Run with `node tests/test_admin_sections.js`, or through the Python suite. */

const fs = require('fs');
const path = require('path');

const src = fs.readFileSync(
  path.join(__dirname, '..', 'stroj', 'web', 'app.js'), 'utf8');

function extract(name) {
  const start = src.indexOf(`function ${name}(`);
  if (start < 0) throw new Error(`${name} not found`);
  let depth = 0, i = src.indexOf('{', src.indexOf(')', start));
  for (; i < src.length; i++) {
    if (src[i] === '{') depth++;
    else if (src[i] === '}') { depth--; if (!depth) break; }
  }
  return src.slice(start, i + 1);
}
// Trimmed, because a checkout with CRLF endings leaves a carriage return
// where the declaration's semicolon is stripped off below.
const lineFor = (decl) => {
  const at = src.indexOf(decl);
  return src.slice(at, src.indexOf('\n', at)).trim();
};
const arrow = (decl) => eval(
  `(${lineFor(decl).split('=').slice(1).join('=').replace(/;$/, '')})`);

const ESCAPES = eval(`(${lineFor('const ESCAPES =').split('=').slice(1).join('=').replace(/;$/, '')})`);
const esc = arrow('const esc =');
const ADMIN_PAGE = arrow('const ADMIN_PAGE =');

/* Enough of the DOM for the section to draw into: it reads `$('#id-rows')` and
 * friends and writes innerHTML, textContent and hidden. `$` is resolved out of
 * this scope when the function is eval'd, so the stub simply shadows the real
 * one. */
let dom = {};
const $ = (sel) => dom[sel] || null;
const mount = (id, withSearch = true) => {
  dom = {
    [`#${id}-rows`]: { innerHTML: '' },
    [`#${id}-count`]: { textContent: '' },
    [`#${id}-more`]: { textContent: '', hidden: false, onclick: null },
  };
  if (withSearch) dom[`#${id}-q`] = { value: '', oninput: null };
};
// Data rows only. The "nothing here" placeholder is a row too, and it is drawn
// alone, so a spanning cell means none of the list was drawn.
const rowsDrawn = (id) => {
  const html = dom[`#${id}-rows`].innerHTML;
  return html.includes('colspan=') ? 0 : (html.match(/<tr>/g) || []).length;
};
const type = (id, text) => { dom[`#${id}-q`].value = text; dom[`#${id}-q`].oninput(); };

const adminSection = eval(`(${extract('adminSection')})`);

let failures = 0, checks = 0;
function check(name, got, want) {
  checks += 1;
  if (got !== want) {
    failures += 1;
    console.error(`FAIL  ${name}: got ${JSON.stringify(got)}, want ${JSON.stringify(want)}`);
  }
}
function contains(name, haystack, needle) {
  checks += 1;
  if (!String(haystack).includes(needle)) {
    failures += 1;
    console.error(`FAIL  ${name}: missing ${needle}`);
  }
}
function absent(name, haystack, needle) {
  checks += 1;
  if (String(haystack).includes(needle)) {
    failures += 1;
    console.error(`FAIL  ${name}: should not contain ${needle}`);
  }
}

const thing = (n) => ({ title: `Thing ${n}`, slug: `thing-${n}` });
const named = (item, q) => item.title.toLowerCase().includes(q)
  || item.slug.toLowerCase().includes(q);
const spec = (items, extra) => Object.assign({
  id: 'things', title: 'Things', kind: 'thing', items, match: named,
  columns: ['Title', 'Slug', 'Actions'], empty: 'None yet.',
  row: (t) => `<tr><td>${esc(t.title)}</td></tr>`,
}, extra);

// ---------------------------------------------------------------- the markup

const section = adminSection(spec([thing(1)]));
contains('the rows land in a tbody the section owns', section.html, '<tbody id="things-rows">');
contains('there is a count to fill in', section.html, 'id="things-count"');
contains('and a search box', section.html, 'id="things-q"');
contains('the search box reads like the rest of the site', section.html, 'class="search"');
contains('the header names the list', section.html, '<h2>Things</h2>');
contains('a section that can create offers it', section.html, 'href="#/admin/new/thing"');

// A list with nothing in it has nothing to search; the users page hides its
// box the same way.
absent('an empty list has no search box',
  adminSection(spec([])).html, 'id="things-q"');
// Users cannot be created from here, so that section passes no kind.
absent('a section with no kind offers no create button',
  adminSection(spec([thing(1)], { kind: undefined })).html, '+ New');

// ------------------------------------------------------------------ paging

const many = Array.from({ length: ADMIN_PAGE * 2 + 7 }, (_, i) => thing(i + 1));
mount('things');
const big = adminSection(spec(many));
big.bind();
check('a long list draws one page', rowsDrawn('things'), ADMIN_PAGE);
check('the count is the whole list', dom['#things-count'].textContent, String(many.length));
check('the rest are offered', dom['#things-more'].hidden, false);
check('and it says how many are left', dom['#things-more'].textContent,
  `Show the other ${many.length - ADMIN_PAGE}`);

dom['#things-more'].onclick();
check('asking for the rest draws them all', rowsDrawn('things'), many.length);
check('and there is nothing left to offer', dom['#things-more'].hidden, true);

mount('things');
const small = adminSection(spec([thing(1), thing(2)]));
small.bind();
check('a short list draws whole', rowsDrawn('things'), 2);
check('with no button to press', dom['#things-more'].hidden, true);

// ---------------------------------------------------------------- searching

mount('things');
const searchable = adminSection(spec(many));
searchable.bind();
type('things', 'thing-7');
// thing-7, thing-70..thing-79 exist in a list of 107.
check('a search narrows the rows', rowsDrawn('things'), 11);
check('and the count says what it narrowed from',
  dom['#things-count'].textContent, `11 of ${many.length}`);

type('things', 'nothing like this');
check('a search that matches nothing draws no rows', rowsDrawn('things'), 0);
contains('and says so', dom['#things-rows'].innerHTML, 'Nothing matches that.');
absent('rather than claiming the list is empty', dom['#things-rows'].innerHTML, 'None yet.');

// A search wide enough to still overflow one page is paged too, and asking for
// the rest must not reach past what matched.
type('things', 'thing');
check('a wide search is paged as well', rowsDrawn('things'), ADMIN_PAGE);
dom['#things-more'].onclick();
check('and shows every match, not every row', rowsDrawn('things'), many.length);

// Having asked to see all of one search, the next search starts at the top
// again — the page you expanded was a page of the old results.
type('things', 'thing');
check('a new search re-pages', rowsDrawn('things'), ADMIN_PAGE);

mount('things');
const none = adminSection(spec([]));
none.bind();
contains('an empty list says it is empty', dom['#things-rows'].innerHTML, 'None yet.');
contains('and the empty row spans every column', dom['#things-rows'].innerHTML, 'colspan="3"');

// ------------------------------------------------------- rebinding and reload

mount('things');
let bound = 0;
const rebinding = adminSection(spec(many, { bindRows: () => { bound += 1; } }));
rebinding.bind();
check('rows are bound when first drawn', bound, 1);
type('things', 'thing-1');
// Redrawing replaces the row elements, so the handlers on them have to go back
// or a searched-for row comes back inert.
check('and bound again after a search redraws them', bound, 2);
dom['#things-more'].onclick();
check('and again after showing the rest', bound, 3);

mount('things');
const reloading = adminSection(spec([thing(1)]));
reloading.bind();
reloading.reload([thing(1), thing(2), thing(3)]);
check('a reload draws the fresh list', rowsDrawn('things'), 3);
check('and counts it', dom['#things-count'].textContent, '3');

// Express creation reloads the problems table while a search is typed in it;
// the search is the admin's, not the page's, so it survives.
mount('things');
const kept = adminSection(spec(many));
kept.bind();
type('things', 'thing-42');
kept.reload(many.concat([thing(999)]));
check('a reload keeps what was typed', rowsDrawn('things'), 1);

// ---------------------------------------------------------------- escaping

const nasty = adminSection(spec([thing(1)], { title: '<img onerror=1>' }));
absent('the title is escaped in the header', nasty.html, '<img');

console.log(`${checks - failures}/${checks} checks passed`);
process.exit(failures ? 1 : 0);
