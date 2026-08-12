'use strict';

/* Ordering the problem list by a column.
 *
 * Run with `node tests/test_problem_sort.js`, or through the Python suite. */

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

const PROBLEM_TEXT_COLUMNS = arrow('const PROBLEM_TEXT_COLUMNS =');
const sortProblems = eval(`(${extract('sortProblems')})`);
const firstDirection = arrow('const firstDirection =');

let failures = 0, checks = 0;
function check(name, got, want) {
  checks += 1;
  const a = JSON.stringify(got), b = JSON.stringify(want);
  if (a !== b) {
    failures += 1;
    console.error(`FAIL  ${name}:\n  got  ${a}\n  want ${b}`);
  }
}

const problem = (title, points, author, time, memory, checker) => ({
  slug: title.toLowerCase().replace(/ /g, '-'), title, points, author,
  time_limit_ms: time, memory_limit_mb: memory, checker, types: [],
});

// Deliberately served in neither alphabetical nor numeric order.
const LIST = [
  problem('Turnout', 120, 'yan', 1000, 64, 'token'),
  problem('A + B', 10, 'dean', 2000, 256, 'exact'),
  problem('Climb', 350, null, 1500, 128, 'token'),
  problem('Patrol', 120, 'Ada', 1000, 64, 'float'),
];
const names = (sort) => sortProblems(LIST, sort).map((p) => p.title);

// No key means the list as the judge served it, which is a real state and the
// one a third click returns to.
check('no key keeps the served order', names({ key: null, dir: 1 }),
  ['Turnout', 'A + B', 'Climb', 'Patrol']);

check('title ascending', names({ key: 'title', dir: 1 }),
  ['A + B', 'Climb', 'Patrol', 'Turnout']);
check('title descending', names({ key: 'title', dir: -1 }),
  ['Turnout', 'Patrol', 'Climb', 'A + B']);

check('points descending', names({ key: 'points', dir: -1 }),
  ['Climb', 'Patrol', 'Turnout', 'A + B']);
check('points ascending', names({ key: 'points', dir: 1 }),
  ['A + B', 'Patrol', 'Turnout', 'Climb']);

// Patrol and Turnout are both on 120; the title decides, in both directions,
// so the pair never shuffles between renders.
check('equal points break on title, ascending', names({ key: 'points', dir: 1 }).slice(1, 3),
  ['Patrol', 'Turnout']);
check('equal points break on title, descending', names({ key: 'points', dir: -1 }).slice(1, 3),
  ['Patrol', 'Turnout']);

check('time', names({ key: 'time_limit_ms', dir: 1 }).slice(0, 2), ['Patrol', 'Turnout']);
check('memory descending', names({ key: 'memory_limit_mb', dir: -1 })[0], 'A + B');
check('checker ascending', names({ key: 'checker', dir: 1 })[0], 'A + B');

// Author sorting is case-insensitive, or 'Ada' would sort apart from 'dean'
// purely because of its capital letter.
check('author ascending puts Ada before dean',
  names({ key: 'author', dir: 1 }).indexOf('Patrol')
    < names({ key: 'author', dir: 1 }).indexOf('A + B'), true);

// An unattributed problem must not poison the comparison.
check('a missing author sorts as blank, not as undefined',
  names({ key: 'author', dir: 1 })[0], 'Climb');
check('a missing author still sorts the rest',
  names({ key: 'author', dir: 1 }).length, 4);

// Sorting must not disturb the caller's array.
const before = LIST.map((p) => p.title);
sortProblems(LIST, { key: 'points', dir: -1 });
check('the original list is untouched', LIST.map((p) => p.title), before);

// Empty and single-item lists are the states a filter leaves behind.
check('an empty list sorts to empty', sortProblems([], { key: 'points', dir: 1 }), []);
check('one problem sorts to itself',
  sortProblems([LIST[0]], { key: 'points', dir: -1 }).map((p) => p.title), ['Turnout']);

// A sealed contest problem reports no points; it must not sort as NaN.
const sealed = [{ ...problem('Sealed', null, 'yan', 1000, 64, 'token') }, LIST[1]];
check('null points sort as zero, not NaN',
  sortProblems(sealed, { key: 'points', dir: 1 }).map((p) => p.title), ['Sealed', 'A + B']);

// Which way a column runs on its first click.
check('numbers open largest first', firstDirection('points'), -1);
check('time opens largest first', firstDirection('time_limit_ms'), -1);
check('names open A to Z', firstDirection('title'), 1);
check('authors open A to Z', firstDirection('author'), 1);
check('checkers open A to Z', firstDirection('checker'), 1);

check('the text columns are the ones that read as names',
  [...PROBLEM_TEXT_COLUMNS].sort(), ['author', 'checker', 'title']);

console.log(`${checks - failures}/${checks} checks passed`);
process.exit(failures ? 1 : 0);
