'use strict';

/* The submission calendar's grid layout.
 *
 * Run with `node tests/test_activity_calendar.js`, or through the Python
 * suite. */

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
const activityWeeks = eval(`(${grab('function activityWeeks(')})`);
const activityLevel = eval(src.slice(
  src.indexOf('const activityLevel'), src.indexOf('const MONTHS'))
  .replace('const activityLevel =', ''));
eval(src.slice(src.indexOf('const MONTHS'), src.indexOf('function activityTip'))
  .replace('const MONTHS', 'globalThis.MONTHS'));
const activityTip = eval(`(${grab('function activityTip(')})`);

let failures = 0, checks = 0;
function check(name, got, want) {
  checks += 1;
  if (got !== want) { failures += 1; console.error(`FAIL  ${name}: ${got} != ${want}`); }
}

// 2024-01-07 is a Sunday, 2024-01-20 a Saturday: two whole weeks.
const twoWeeks = activityWeeks({
  since: '2024-01-07', until: '2024-01-20',
  counts: [{ date: '2024-01-09', count: 3, accepted: 1 }],
});
check('whole weeks are not padded', twoWeeks.length, 2);
check('every column holds seven days', twoWeeks.every((w) => w.length === 7), true);
check('a reported day keeps its count', twoWeeks[0][2].count, 3);
check('a reported day keeps its accepted', twoWeeks[0][2].accepted, 1);
check('an unreported day is a zero, not a hole', twoWeeks[1][0].count, 0);
check('the last cell is the last day', twoWeeks[1][6].date, '2024-01-20');

// A window opening mid-week is padded back to the Sunday, so the row a day
// sits on is its real weekday rather than an offset from the start.
const midWeek = activityWeeks({ since: '2024-01-10', until: '2024-01-13', counts: [] });
check('the leading partial week exists', midWeek.length, 1);
check('days before the window are null', midWeek[0].slice(0, 3).join(','), ',,');
check('the window starts on its weekday', midWeek[0][3].date, '2024-01-10');
check('the padded column is still seven tall', midWeek[0].length, 7);

// A year's window is 53 columns at most, and the counts map is optional.
const year = activityWeeks({ since: '2023-08-10', until: '2024-08-08' });
check('a year fits in 53 columns', year.length <= 53, true);
check('a missing counts list is empty, not fatal', year[1][0].count, 0);

check('no submissions is level 0', activityLevel(0), 0);
check('one submission is level 1', activityLevel(1), 1);
check('two submissions is level 2', activityLevel(2), 2);
check('four submissions is level 3', activityLevel(4), 3);
check('seven submissions tops out', activityLevel(7), 4);
check('the scale is capped', activityLevel(500), 4);

// The hover bubble leads with the number, which is the whole point of it.
check('one submission reads singular',
  activityTip({ date: '2024-03-05', count: 1, accepted: 0 }),
  '1 submission on Mar 5, 2024');
check('several submissions read plural, with the accepted split out',
  activityTip({ date: '2024-03-05', count: 4, accepted: 2 }),
  '4 submissions (2 accepted) on Mar 5, 2024');
check('an empty day says so rather than showing a zero',
  activityTip({ date: '2024-12-25', count: 0, accepted: 0 }),
  'No submissions on Dec 25, 2024');

console.log(`${checks - failures}/${checks} checks passed`);
process.exit(failures ? 1 : 0);
