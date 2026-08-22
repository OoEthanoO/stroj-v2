'use strict';

/* The "set for" pills on a problem page: which contests they name, and the
 * one they never do.
 *
 * Run with `node tests/test_problem_origin.js`, or through the Python suite. */

const fs = require('fs');
const path = require('path');

const src = fs.readFileSync(
  path.join(__dirname, '..', 'stroj', 'web', 'app.js'), 'utf8');
const grab = (name) => {
  const start = src.indexOf(name);
  let depth = 0, i = src.indexOf('{', start);
  for (; i < src.length; i++) {
    if (src[i] === '{') depth++;
    else if (src[i] === '}') { depth--; if (!depth) break; }
  }
  return src.slice(start, i + 1);
};
// A bare declaration would not escape eval's own scope under 'use strict'.
eval(src.slice(src.indexOf('const ESCAPES'), src.indexOf('class ApiError'))
  .replace('const ESCAPES', 'globalThis.ESCAPES')
  .replace('const esc', 'globalThis.esc'));
const originPills = eval(`(${grab('function originPills(')})`);

let failures = 0, checks = 0;
function check(name, got, want) {
  checks += 1;
  if (got !== want) {
    failures += 1;
    console.error(`FAIL  ${name}: got ${JSON.stringify(got)}, want ${JSON.stringify(want)}`);
  }
}
const has = (name, html, needle) => check(name, html.includes(needle), true);

const march = { slug: 'march', title: 'March Round', label: 'C' };
const june = { slug: 'june', title: 'June Round', label: 'A' };

// Read from the archive: the round and the letter both have to be there.
const outside = originPills({ contests: [march] }, null);
has('names the contest', outside, 'March Round');
has('names the label', outside, '<b>C</b>');
has('links to the contest', outside, 'href="#/contest/march"');
has('reads as one fact on hover', outside, 'title="Set as problem C of March Round"');

// Read through that contest's own page: it is already the frame around you.
check('says nothing inside its own contest', originPills({ contests: [march] }, 'march'), '');

// Reading one contest does not silence the others a problem was set for.
const both = originPills({ contests: [march, june] }, 'june');
has('the other round survives', both, 'March Round');
check('and only the other one', both.includes('June Round'), false);

// Order is the server's — oldest round first — and must not be reshuffled.
const ordered = originPills({ contests: [march, june] }, null);
check('oldest first', ordered.indexOf('March Round') < ordered.indexOf('June Round'), true);

// A problem that was never set for anything, and an older server that does
// not send the field at all.
check('never set for a contest', originPills({ contests: [] }, null), '');
check('field absent entirely', originPills({}, null), '');

// Contest titles are member-visible text an admin typed; they go through esc.
const nasty = originPills(
  { contests: [{ slug: 'x"y', title: '<script>x</script>', label: 'A&B' }] }, null);
check('title is escaped', nasty.includes('<script>'), false);
has('label is escaped', nasty, 'A&amp;B');
has('slug is url-encoded', nasty, 'href="#/contest/x%22y"');

console.log(`${checks - failures}/${checks} checks passed`);
process.exit(failures ? 1 : 0);
