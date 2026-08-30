'use strict';

/* The type migration panel: which types it offers, how it counts them, and
 * that it stays out of the way when there is nothing to merge.
 *
 * Run with `node tests/test_type_migrate.js`, or through the Python suite. */

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
const typeMigratePanel = eval(`(${extract('typeMigratePanel')})`);

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

const noop = (fn) => fn;
const panel = (problems) => typeMigratePanel(problems, noop);

// The two spellings this exists for. Both are offered, each counted, and the
// count is what tells an admin which way round to merge them.
const split = panel([
  { types: ['dp', 'arrays'] },
  { types: ['dp'] },
  { types: ['dynamic programming'] },
]).html;
contains('the common spelling is offered with its count', split, '>dp (2)</option>');
contains('the rare one is offered too', split, '>dynamic programming (1)</option>');
contains('a type on one problem counts once', split, '>arrays (1)</option>');

// Types are listed alphabetically: the select is scanned for a name, and the
// order problems happen to be written in is no help finding one.
const ordered = panel([{ types: ['zebra', 'arrays', 'math'] }]).html;
check('types are sorted', ordered.indexOf('>arrays') < ordered.indexOf('>math'), true);
check('and sorted all the way down', ordered.indexOf('>math') < ordered.indexOf('>zebra'), true);

// Every control the binder reaches for has to be in the markup, or the panel
// renders and does nothing.
for (const hook of ['id="type-from"', 'id="type-to"', 'id="type-migrate"']) {
  contains(`the panel carries ${hook}`, split, hook);
}
// The target is free text so a type can be renamed to one nothing uses yet,
// with the existing names offered as completions rather than as the only ones.
contains('the target is an input, not a select', split, '<input id="type-to"');
contains('existing types are offered as completions', split, 'list="type-names"');
contains('and the completion list is there to be found', split, '<datalist id="type-names">');
contains('the target is capped at what a type may hold', split, 'maxlength="32"');

// An archive with no types has nothing to merge; the panel would be a control
// that can only fail.
const empty = panel([{ types: [] }, { types: [] }]);
check('an untyped archive renders no panel', empty.html, '');
check('and binding it is safe anyway', typeof empty.bind, 'function');
empty.bind();

// `/api/problems` seals types on a problem in a running contest, which reaches
// the page as no key at all rather than an empty list.
const sealed = panel([{ slug: 'sealed' }, { types: ['dp'] }]).html;
contains('a problem with no types at all is skipped, not fatal', sealed, '>dp (1)</option>');

// Types are typed in by an admin, and they land in an option's text and in
// two value attributes.
const nasty = panel([{ types: ['"><script>x</script>'] }]).html;
absent('types are escaped in the option text', nasty, '<script>');
absent('and cannot break out of the value attribute', nasty, 'value=""><');

console.log(`${checks - failures}/${checks} checks passed`);
process.exit(failures ? 1 : 0);
