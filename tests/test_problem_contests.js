'use strict';

/* The contest pills on a problem page: which rounds they name, the one they
 * never do, and how a round that has not started is told apart from history.
 *
 * Run with `node tests/test_problem_contests.js`, or through the Python
 * suite. */

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
const contestPills = eval(`(${grab('function contestPills(')})`);

let failures = 0, checks = 0;
function check(name, got, want) {
  checks += 1;
  if (got !== want) {
    failures += 1;
    console.error(`FAIL  ${name}: got ${JSON.stringify(got)}, want ${JSON.stringify(want)}`);
  }
}
const has = (name, html, needle) => check(name, html.includes(needle), true);

const march = { slug: 'march', title: 'March Round', label: 'C', state: 'ended' };
const june = { slug: 'june', title: 'June Round', label: 'A', state: 'ended' };
const sept = { slug: 'sept', title: 'September Round', label: 'D', state: 'before' };

// Read from the archive: the round and the letter both have to be there.
const outside = contestPills({ contests: [march] }, null);
has('names the contest', outside, 'March Round');
has('names the label', outside, '<b>C</b>');
has('links to the contest', outside, 'href="#/contest/march"');
has('reads as one fact on hover', outside, 'title="Set as problem C of March Round"');
check('a finished round is not marked upcoming', outside.includes('upcoming'), false);

// Read through that contest's own page: it is already the frame around you.
check('says nothing inside its own contest', contestPills({ contests: [march] }, 'march'), '');

// Reading one contest does not silence the others a problem was set for.
const both = contestPills({ contests: [march, june] }, 'june');
has('the other round survives', both, 'March Round');
check('and only the other one', both.includes('June Round'), false);

// Order is the server's — oldest round first — and must not be reshuffled.
const ordered = contestPills({ contests: [march, june] }, null);
check('oldest first', ordered.indexOf('March Round') < ordered.indexOf('June Round'), true);

// A round still to come. Unmarked beside a finished one it would read as
// history, so the marker is in words and not only in the tint.
const soon = contestPills({ contests: [sept] }, null);
has('names the coming round', soon, 'September Round');
has('names its label', soon, '<b>D</b>');
has('says so in words', soon, '<i>upcoming</i>');
has('and says so on hover', soon,
  'title="Set as problem D of September Round, which has not started"');
has('tinted apart as well', soon, 'class="pill pill-origin pill-upcoming"');

// Mixed history and schedule: only the coming one carries the marker. Split
// on the closing tag rather than on the title — the class attribute sits
// before the round's name, so slicing at the name reads the wrong pill.
const pills = contestPills({ contests: [march, sept] }, null)
  .split('</a>').filter((p) => p.includes('<a '));
check('one pill per round', pills.length, 2);
check('the finished round is left plain', pills[0].includes('pill-upcoming'), false);
check('the coming round is marked', pills[1].includes('pill-upcoming'), true);
check('and only it carries the word', pills[0].includes('upcoming'), false);
has('the marked one is the right one', pills[1], 'September Round');

// A problem that was never set for anything, and an older server that does
// not send the field at all.
check('never set for a contest', contestPills({ contests: [] }, null), '');
check('field absent entirely', contestPills({}, null), '');

// Contest titles are member-visible text an admin typed; they go through esc.
const nasty = contestPills(
  { contests: [{ slug: 'x"y', title: '<script>x</script>', label: 'A&B',
                 state: 'before' }] }, null);
check('title is escaped', nasty.includes('<script>'), false);
has('label is escaped', nasty, 'A&amp;B');
has('slug is url-encoded', nasty, 'href="#/contest/x%22y"');
check('the tooltip is escaped too', nasty.includes('title="Set as problem A&B'), false);

console.log(`${checks - failures}/${checks} checks passed`);
process.exit(failures ? 1 : 0);
