'use strict';

const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const src = fs.readFileSync(path.join(__dirname, '..', 'stroj', 'web', 'app.js'), 'utf8');
function extract(name) {
  let start = src.indexOf(`function ${name}(`);
  if (src.slice(start - 6, start) === 'async ') start -= 6;
  return src.slice(start, src.indexOf('\n}', start) + 2);
}

function page(api) {
  let html = '', list;
  const polls = [];
  const context = vm.createContext({
    URLSearchParams, api, state: { user: null }, STATE_LABEL: { ended: 'Ended' },
    absolute: value => value, relative: () => 'just now', memory: value => value,
    updateCountdown() {}, $$: () => [],
    $: selector => selector === '#sub-list' ? list : null,
    every: (ms, fn) => polls.push(fn),
    setView: value => {
      if (list) list.isConnected = false;
      html = value;
      list = { innerHTML: '', isConnected: true };
    },
  });
  vm.runInContext([
    src.slice(src.indexOf('const ESCAPES ='), src.indexOf('class ApiError')),
    src.match(/^const verdictBadge =.*$/m)[0],
    ...['userLink', 'scopeTabs', 'viewSubmissions', 'viewScoreboard'].map(extract),
  ].join('\n'), context);
  return {
    context, polls, html: () => html, list: () => list.innerHTML,
    submissions: params => context.viewSubmissions(new URLSearchParams(params)),
  };
}

function linkedParams(html, label) {
  const href = html.match(new RegExp(`href="([^"]+)"[^>]*>${label}`))[1];
  return new URLSearchParams(href.replaceAll('&amp;', '&').split('?')[1]);
}

for (const scoring of ['icpc', 'ioi']) {
  test(`${scoring} cells link to the correct contestant, problem and contest`, async () => {
    const problems = ['A', 'B', 'C', 'D', 'E'].map(label => ({
      label, slug: `problem-${label}`, title: `Problem ${label}`, solved_by: 0,
    }));
    const cell = { attempts: 1, pending: false, frozen: 0, solved: false, score: 0 };
    const board = {
      scoring, state: 'ended', frozen: false, penalty_minutes: 20, problems,
      contest: { title: 'Weekly', starts_at: '', ends_at: '' },
      rows: [{ username: 'a+b', user_id: 1, rank: 1, solved: 1, total_score: 100, penalty: 20,
        cells: {
          A: { ...cell, solved: true, attempts: 3, score: 100, minutes: 10 },
          B: cell, C: { ...cell, pending: true, attempts: 0 },
          D: { ...cell, frozen: 2, attempts: 0 },
        } }],
    };
    const view = page(async () => board);
    await view.context.viewScoreboard('weekly+practice');
    const links = [...view.html().matchAll(/class="score-cell-link" href="([^"]+)"/g)];
    assert.equal(links.length, 5, 'empty and unresolved cells are also accessible');
    links.forEach((match, i) => {
      const params = new URLSearchParams(match[1].replaceAll('&amp;', '&').split('?')[1]);
      assert.equal(params.get('contest'), 'weekly+practice');
      assert.equal(params.get('username'), 'a+b');
      assert.equal(params.get('problem'), problems[i].slug);
    });
    assert.match(view.html(), /\+2 hidden/);
    assert.match(view.html(), scoring === 'icpc' ? />\+2<span/ : />100<span/);
  });
}

const submission = id => ({
  id, username: 'member', user_role: 'user', problem_slug: 'p', problem_title: 'P',
  contest_slug: 'weekly', language: 'cpp', verdict: 'AC', score: 1, max_score: 1,
  time_ms: 1, memory_kb: 1, created_at: '2026-10-07T00:00:00Z',
});

test('pagination reaches every attempt while retaining all three filters', async () => {
  const rows = Array.from({ length: 65 }, (_, i) => submission(100 - i));
  const view = page(async url => {
    const query = new URLSearchParams(url.split('?')[1]);
    assert.equal(query.get('username'), 'member');
    assert.equal(query.get('problem'), 'p');
    assert.equal(query.get('contest'), 'weekly');
    return { submissions: rows.filter(s => !query.has('before') || s.id < Number(query.get('before')))
      .slice(0, Number(query.get('limit'))) };
  });
  await view.submissions({ username: 'member', problem: 'p', contest: 'weekly' });
  const first = [...view.list().matchAll(/href="#\/submission\/(\d+)"/g)].map(m => Number(m[1]));
  assert.equal(first.length, 60);
  const older = linkedParams(view.list(), 'Older submissions');
  assert.equal(older.get('before'), '41');
  await view.submissions(older);
  const second = [...view.list().matchAll(/href="#\/submission\/(\d+)"/g)].map(m => Number(m[1]));
  assert.deepEqual([...first, ...second], rows.map(s => s.id));
  assert.doesNotMatch(view.list(), /Older submissions/);
  const newest = linkedParams(view.list(), 'Newest submissions');
  assert.equal(newest.has('before'), false);
  await view.submissions(newest);
  assert.match(view.list(), /#100<\/a>/);
  assert.match(view.html(), /Back to scoreboard/);
});

test('a late response cannot replace another user or page of submissions', async () => {
  let release;
  const deferred = new Promise(resolve => { release = resolve; });
  const view = page(async url => url.includes('username=old')
    ? deferred : { submissions: [submission(2)] });
  const old = view.submissions({ username: 'old' });
  await view.submissions({ username: 'new' });
  release({ submissions: [submission(1)] });
  await old;
  assert.match(view.list(), /#2<\/a>/);
  assert.doesNotMatch(view.list(), /#1<\/a>/);
  assert.equal(view.polls.length, 1, 'the detached page must not start polling');
});
