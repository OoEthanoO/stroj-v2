'use strict';

/* Placement of the "running" row in a subtask-grouped submission table.
 *
 * Run with `node tests/test_submission_view.js`, or through the Python suite. */

const fs = require('fs');
const path = require('path');

const src = fs.readFileSync(
  path.join(__dirname, '..', 'stroj', 'web', 'app.js'), 'utf8');
const start = src.indexOf('function runningSection(');
let depth = 0, i = src.indexOf('{', start);
for (; i < src.length; i++) {
  if (src[i] === '{') depth++;
  else if (src[i] === '}') { depth--; if (!depth) break; }
}
// A bare declaration would not escape eval's own scope under 'use strict',
// so evaluate it as an expression and keep the value.
const runningSection = eval(`(${src.slice(start, i + 1)})`);

let failures = 0, checks = 0;
function check(name, got, want) {
  checks += 1;
  if (got !== want) {
    failures += 1;
    console.error(`FAIL  ${name}: got ${got}, want ${want}`);
  }
}

// fair-split's shape: 2 samples, then subtasks of 18, 15 and 17 tests.
const groups = [{ idx: 1, tests: 18 }, { idx: 2, tests: 15 }, { idx: 3, tests: 17 }];
const total = 52;
const at = (done) => runningSection(done, total, groups);

// The bug: whatever had finished, the running row rendered under the last
// subtask, so subtask 3 always looked like it had a test in flight.
check('nothing run yet -> samples', at(0), 0);
check('one sample done -> still samples', at(1), 0);
check('samples done -> subtask 1', at(2), 1);
check('mid subtask 1', at(10), 1);
check('last test of subtask 1', at(19), 1);
check('subtask 1 done -> subtask 2', at(20), 2);
check('mid subtask 2', at(30), 2);
check('subtask 2 done -> subtask 3', at(35), 3);
check('mid subtask 3', at(50), 3);
check('last test of all -> subtask 3', at(51), 3);
check('everything run -> nowhere', at(52), null);

// A problem whose subtasks cover every test, with no samples at all.
const noSamples = [{ idx: 1, tests: 3 }, { idx: 2, tests: 4 }];
check('no samples, first test', runningSection(0, 7, noSamples), 1);
check('no samples, crossing into subtask 2', runningSection(3, 7, noSamples), 2);
check('no samples, finished', runningSection(7, 7, noSamples), null);

// Defensive: `total` can lag the group counts if test data was just replaced.
check('inconsistent total does not hang', runningSection(0, 0, noSamples), 1);
check('done past the end', runningSection(99, 7, noSamples), null);

console.log(`${checks - failures}/${checks} checks passed`);
process.exit(failures ? 1 : 0);
