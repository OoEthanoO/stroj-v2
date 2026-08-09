'use strict';

/* `@name` mentions in a bio.
 *
 * Run with `node tests/test_mentions.js`, or through the Python suite. */

const fs = require('fs');
const path = require('path');

const src = fs.readFileSync(
  path.join(__dirname, '..', 'stroj', 'web', 'app.js'), 'utf8');
const grab = (name) => {
  const start = src.indexOf(`function ${name}(`);
  let depth = 0, i = src.indexOf('{', start);
  for (; i < src.length; i++) {
    if (src[i] === '{') depth++;
    else if (src[i] === '}') { depth--; if (!depth) break; }
  }
  return src.slice(start, i + 1);
};
const ESCAPES = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ESCAPES[c]);
const { renderMath } = require(path.join(__dirname, '..', 'stroj', 'web', 'latex.js'));
// Bare declarations would not escape eval's own scope under 'use strict', so
// evaluate each as an expression. Defined in dependency order: the later ones
// close over the earlier ones.
// markdown() falls back to this when no map is passed, which is how every
// surface — posts, statements, previews — picks mentions up for free.
let mentionRoster = {};
const userLink = eval(`(${grab('userLink')})`);
const inlineMarkdown = eval(`(${grab('inlineMarkdown')})`);
const markdown = eval(`(${grab('markdown')})`);

let failures = 0, checks = 0;
function check(name, condition, detail) {
  checks += 1;
  if (!condition) { failures += 1; console.error(`FAIL  ${name}\n      ${detail}`); }
}
const KNOWN = { ann: 'admin', bob: 'user', 'dot.name': 'user' };
mentionRoster = KNOWN;
const render = (text, mentions = KNOWN) => markdown(text, mentions);
const has = (name, text, needle) => {
  const out = render(text);
  check(name, out.includes(needle), `${JSON.stringify(text)}\n      got: ${out}`);
};
const lacks = (name, text, needle) => {
  const out = render(text);
  check(name, !out.includes(needle), `${JSON.stringify(text)}\n      got: ${out}`);
};

// A mention should look exactly like the same name anywhere else on the site,
// admin colouring included — otherwise one person reads as two.
has('admin mention is styled', 'thanks @ann', 'user-link user-admin');
has('admin mention links', 'thanks @ann', 'href="#/user/ann"');
has('plain mention is a link', 'and @bob', 'href="#/user/bob"');
lacks('plain mention is not styled admin', 'and @bob', 'user-admin');
has('a name containing a dot works', 'hi @dot.name', 'href="#/user/dot.name"');

// A name nobody has must stay text. A link to nobody is worse than no link.
lacks('unknown name is not linked', 'who is @nobody', '<a');
has('unknown name survives as text', 'who is @nobody', '@nobody');

// Things that merely look like mentions.
lacks('an email address is untouched', 'mail me@example.com', '<a class="user-link');
lacks('a mention inside code stays literal', 'write `@ann` here', '<a class="user-link');
has('code span still renders', 'write `@ann` here', '<code>@ann</code>');

// Punctuation after a mention belongs to the sentence, not the name.
has('trailing full stop is left outside', 'ask @ann.', '>ann</a>.');
has('trailing comma is left outside', 'ask @ann, please', '>ann</a>,');

// The surrounding markdown must survive, and vice versa.
has('bold still works beside a mention', '**b** and @ann', '<strong>b</strong>');
has('italic still works beside a mention', '@ann and *i*', '<em>i</em>');
has('a mention inside a list item', '- see @ann', '<li>');
has('math still renders beside a mention', '@ann knows $x^2$', '<msup>');

// Every other caller passes nothing and still gets mentions, which is what
// makes a post, a statement and a bio behave the same way.
check('the roster is used when no map is passed',
  markdown('hello @ann').includes('user-link user-admin'), markdown('hello @ann'));
// An explicit map still wins, and an empty one disables linking outright.
check('an explicit empty map disables linking',
  !markdown('hello @ann', {}).includes('<a'), markdown('hello @ann', {}));
// Before the roster loads there is simply nothing to link against.
mentionRoster = {};
check('an empty roster links nothing',
  !markdown('hello @ann').includes('<a'), markdown('hello @ann'));
mentionRoster = KNOWN;

// The rendered bio is written with innerHTML, so a crafted name must not
// escape. Names are validated server-side, but never rely on that alone.
const evil = { '<img src=x>': 'user' };
const out = markdown('hi @<img src=x>', evil);
check('no raw tag can be injected through a mention', !/<img/i.test(out), out);

console.log(`${checks - failures}/${checks} checks passed`);
process.exit(failures ? 1 : 0);
