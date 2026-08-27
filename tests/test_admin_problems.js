'use strict';

/* The admin problems table: what a row shows, and that it can be redrawn on
 * its own after express creation adds one.
 *
 * Run with `node tests/test_admin_problems.js`, or through the Python suite. */

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
const lineFor = (decl) => {
  const at = src.indexOf(decl);
  return src.slice(at, src.indexOf('\n', at));
};
const arrow = (decl) => eval(
  `(${lineFor(decl).split('=').slice(1).join('=').replace(/;$/, '')})`);

const ESCAPES = eval(`(${lineFor('const ESCAPES =').split('=').slice(1).join('=').replace(/;$/, '')})`);
const esc = arrow('const esc =');
// statePill spans three lines, so take it up to the semicolon that ends the
// declaration rather than the first one inside it.
function constBlock(decl) {
  const at = src.indexOf(decl);
  if (at < 0) throw new Error(`${decl} not found`);
  let depth = 0;
  for (let i = at; i < src.length; i++) {
    if (src[i] === '(') depth++;
    else if (src[i] === ')') depth--;
    else if (src[i] === ';' && depth === 0) return src.slice(at, i);
  }
  throw new Error(`${decl} has no end`);
}
const statePill = eval(
  `(${constBlock('const statePill =').split('=').slice(1).join('=')})`);
const adminProblemRows = eval(`(${extract('adminProblemRows')})`);
const NO_PROBLEMS_ROW = arrow('const NO_PROBLEMS_ROW =');

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

const problem = (slug, title, visible) => ({ slug, title, visible });

// An express problem is created hidden, so the row it adds must read hidden —
// that pill is the whole reason an admin scans this column.
const hidden = adminProblemRows([problem('rope-off', 'Rope Off', false)]);
contains('a new express problem reads hidden', hidden, 'pill warn">hidden<');
absent('and does not read visible', hidden, '>visible<');
contains('the toggle offers to show it', hidden, 'data-visible="0"');
contains('the toggle is labelled Show', hidden, '>Show</button>');

const shown = adminProblemRows([problem('a-plus-b', 'A + B', true)]);
contains('a published problem reads visible', shown, 'pill">visible<');
contains('the toggle offers to hide it', shown, 'data-visible="1"');

// Every control the row binder looks for has to be present, or a redrawn table
// comes back inert.
for (const hook of ['data-upload=', 'data-toggle=', 'data-rejudge=', 'data-delete=']) {
  contains(`the row carries ${hook}`, hidden, hook);
}

// Redrawing is one row per problem, newest first — the reverse of the order
// `/api/problems` serves, which is the order the problems were written.
const served = [problem('b', 'B', true), problem('a', 'A', false)];
const many = adminProblemRows(served);
check('one row per problem', (many.match(/<tr>/g) || []).length, 2);
check('the newest problem is first', many.indexOf('>A<') < many.indexOf('>B<'), true);
// The same array is read elsewhere on the admin page, so the reversal has to
// be on a copy.
check('the list it was handed is left alone', served[0].slug, 'b');

check('an empty list renders nothing', adminProblemRows([]), '');
contains('the empty-table row spans the columns', NO_PROBLEMS_ROW, 'colspan="4"');

// Titles and slugs are author-supplied and land in both text and attributes.
const nasty = adminProblemRows([problem('"><script>x</script>', '<img onerror=1>', false)]);
absent('titles are escaped', nasty, '<img');
absent('slugs are escaped in attributes', nasty, '<script>');

// The link has to survive a slug that would otherwise break the hash route.
const spaced = adminProblemRows([problem('a b', 'A B', true)]);
contains('slugs are percent-encoded in links', spaced, '#/problem/a%20b');

console.log(`${checks - failures}/${checks} checks passed`);
process.exit(failures ? 1 : 0);
