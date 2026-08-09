'use strict';

/* Banding for the DMOJ orientation table.
 *
 * Run with `node tests/test_dmoj_table.js`, or through the Python suite. */

const fs = require('fs');
const path = require('path');

const src = fs.readFileSync(
  path.join(__dirname, '..', 'stroj', 'web', 'app.js'), 'utf8');
const grab = (name) => {
  const start = src.indexOf(`${name}`);
  let depth = 0, i = src.indexOf('{', start);
  for (; i < src.length; i++) {
    if (src[i] === '{') depth++;
    else if (src[i] === '}') { depth--; if (!depth) break; }
  }
  return src.slice(start, i + 1);
};
// A bare declaration would not escape eval's own scope under 'use strict'.
const bandsSrc = src.slice(src.indexOf('const DMOJ_BANDS'), src.indexOf('/** Which band'));
eval(bandsSrc.replace('const DMOJ_BANDS', 'globalThis.DMOJ_BANDS'));
const dmojBand = eval(`(${grab('function dmojBand(')})`);

let failures = 0, checks = 0;
function check(name, got, want) {
  checks += 1;
  if (got !== want) { failures += 1; console.error(`FAIL  ${name}: ${got} != ${want}`); }
}

const label = (points) => {
  const band = dmojBand(points);
  return band ? band.dmoj : null;
};

// The three problems that exist today should land where a DMOJ solver expects.
check('a-plus-b (10)', label(10), '1 – 3');
check('subarray-sum-k (200)', label(200), '10 – 15');
check('fair-split (250)', label(250), '10 – 15');

// Boundaries: every band must be closed, with no value falling between two.
check('bottom of the scale', label(1), '1 – 3');
check('top of band 1', label(50), '1 – 3');
check('bottom of band 2', label(51), '5 – 7');
check('top of band 2', label(150), '5 – 7');
check('bottom of band 3', label(151), '10 – 15');
check('top of band 3', label(300), '10 – 15');
check('bottom of band 4', label(301), '17 – 25');
check('top of band 4', label(600), '17 – 25');
check('open-ended top band', label(601), '30 +');
check('far above the top band', label(10000), '30 +');

// The point column is validated to 1..10000, but the table must not throw on
// anything outside that, or a bad row would take the whole page down.
check('zero', label(0), null);
check('negative', label(-5), null);
check('not a number', label(NaN), null);
check('undefined', label(undefined), null);

// No gaps anywhere across the validated range.
let gaps = 0;
for (let p = 1; p <= 10000; p++) if (!dmojBand(p)) gaps++;
check('every valid point value has a band', gaps, 0);

console.log(`${checks - failures}/${checks} checks passed`);
process.exit(failures ? 1 : 0);
