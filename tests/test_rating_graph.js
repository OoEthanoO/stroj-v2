'use strict';

/* The rating history graph: when it draws, and what it draws.
 *
 * Run with `node tests/test_rating_graph.js`, or through the Python suite. */

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
const ESCAPES = eval(`(${lineFor('const ESCAPES =').split('=').slice(1).join('=').replace(/;$/, '')})`);
const esc = eval(`(${lineFor('const esc =').split('=').slice(1).join('=').replace(/;$/, '')})`);
// The page's own time helpers, so the axis labels are the real ones. These are
// arrow consts rather than declarations, so they come off their line.
const arrow = (decl) => eval(
  `(${lineFor(decl).split('=').slice(1).join('=').replace(/;$/, '')})`);
const parseTime = arrow('const parseTime =');
const absolute = arrow('const absolute =');
const ratingGraph = eval(`(${extract('ratingGraph')})`);

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

const day = (n) => new Date(Date.UTC(2026, 8, n)).toISOString();
const entry = (n, rating, delta, place = 1) => ({
  contest_slug: `weekly-${n}`, contest_title: `Weekly ${n}`, place,
  rating_before: rating - delta, rating_after: rating, delta,
  at: day(n), rank: null,
});

const LADDER = [
  { name: 'Adept 3', tier: 'Adept', division: 3, index: 8, from: 920, to: 944 },
  { name: 'Specialist 1', tier: 'Specialist', division: 1, index: 9, from: 945, to: 969 },
  { name: 'Specialist 2', tier: 'Specialist', division: 2, index: 10, from: 970, to: 994 },
  { name: 'Specialist 3', tier: 'Specialist', division: 3, index: 11, from: 995, to: 1019 },
  { name: 'Expert 1', tier: 'Expert', division: 1, index: 12, from: 1020, to: 1044 },
];

// A history is two or more contests. One is a dot, not a trend.
check('nothing to draw with no history', ratingGraph([], LADDER), '');
check('nothing to draw with one contest', ratingGraph([entry(1, 1030, 30)], LADDER), '');
check('null history is handled', ratingGraph(null, LADDER), '');

const two = [entry(1, 1030, 30), entry(8, 1010, -20)];
const svg = ratingGraph(two, LADDER);
contains('two contests draw a graph', svg, '<svg');
contains('the line is a path', svg, 'class="trend"');
check('one dot per contest', (svg.match(/class="dot"/g) || []).length, 2);

// The caption is the summary someone reads before studying the shape.
contains('shows the current rating', svg, 'now 1010');
contains('shows the peak, not just the last', svg, 'peak 1030');
contains('shows how many contests', svg, '2 contests');

// Tier bands are the reason a rating number means anything.
contains('bands are drawn', svg, 'class="band tier-specialist"');
contains('bands are named', svg, 'Specialist 3');
absent('bands outside the range are skipped', svg, 'tier-adept');
// Specialist 2 (970-994) clips the bottom of this range by two rating points.
// It is drawn, but a label in a 7px stripe would collide with its neighbour.
contains('a sliver of a band is still drawn', svg, 'tier-specialist');
absent('a sliver is not labelled', svg, '>Specialist 2<');

// Hovering a point should say what happened, not just the number.
contains('a rise is signed', svg, '+30');
contains('a fall carries its contest', svg, 'Weekly 8');
contains('a fall is signed', svg, '-20');

// Time on the x axis, so a gap looks like a gap.
const spaced = [entry(1, 1000, 0), entry(2, 1010, 10), entry(28, 1020, 10)];
const spacedSvg = ratingGraph(spaced, LADDER);
const xs = [...spacedSvg.matchAll(/<circle class="dot" cx="([\d.]+)"/g)].map((m) => Number(m[1]));
checks += 1;
if (!(xs[1] - xs[0] < (xs[2] - xs[1]) / 3)) {
  failures += 1;
  console.error(`FAIL  a month's gap is wider than a week's: ${JSON.stringify(xs)}`);
}

// A flat history must not divide by zero.
const flat = ratingGraph([entry(1, 1000, 0), entry(8, 1000, 0)], LADDER);
contains('a flat history still draws', flat, 'class="trend"');
absent('no NaN leaks into the markup', flat, 'NaN');

// Contest titles are user-supplied through the admin editor.
const nasty = [{ ...entry(1, 1030, 30), contest_title: '<script>x</script>' },
               entry(8, 1010, -20)];
absent('titles are escaped', ratingGraph(nasty, LADDER), '<script>');

// A ladder that failed to load should cost the line, not the page.
contains('draws without a ladder', ratingGraph(two, []), 'class="trend"');
contains('survives a missing ladder', ratingGraph(two, undefined), 'class="trend"');

console.log(`${checks - failures}/${checks} checks passed`);
process.exit(failures ? 1 : 0);
