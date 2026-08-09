'use strict';

/* The frontend/backend version state machine.
 *
 * Run with `node tests/test_version_watch.js`, or through the Python suite. */

const fs = require('fs');
const path = require('path');

const src = fs.readFileSync(
  path.join(__dirname, '..', 'stroj', 'web', 'app.js'), 'utf8');
const start = src.indexOf('function versionState(');
let depth = 0, i = src.indexOf('{', start);
for (; i < src.length; i++) {
  if (src[i] === '{') depth++;
  else if (src[i] === '}') { depth--; if (!depth) break; }
}
// A bare declaration would not escape eval's own scope under 'use strict'.
const versionState = eval(`(${src.slice(start, i + 1)})`);

let failures = 0, checks = 0;
function check(name, got, want) {
  checks += 1;
  if (got !== want) { failures += 1; console.error(`FAIL  ${name}: ${got} != ${want}`); }
}

const A = 'aaaaaaaaaaaaaaaa';
const B = 'bbbbbbbbbbbbbbbb';

// The halves disagree: mid-rollout, in either direction. The page must not be
// usable, because it may call an endpoint the other half does not have.
check('backend ahead', versionState(A, A, B), 'updating');
check('frontend ahead', versionState(B, B, A), 'updating');
check('tab predates both, still a mismatch', versionState(A, B, A), 'updating');
// A mismatch outranks staleness — blocking is the stronger statement.
check('mismatch beats stale', versionState('cccccccccccccccc', A, B), 'updating');

// The halves agree.
check('everything current', versionState(A, A, A), 'current');
check('tab is behind an agreeing pair', versionState(A, B, B), 'stale');

// Nothing measurable: a dev checkout, or a judge serving its own files with no
// version.json. Silence beats a warning we cannot justify.
check('no frontend version', versionState(A, null, A), 'unknown');
check('no backend version', versionState(A, A, null), 'unknown');
check('neither', versionState(A, null, null), 'unknown');
check('unstamped page, halves agree', versionState(null, A, A), 'current');
check('unstamped page, halves disagree', versionState(null, A, B), 'updating');

console.log(`${checks - failures}/${checks} checks passed`);
process.exit(failures ? 1 : 0);
