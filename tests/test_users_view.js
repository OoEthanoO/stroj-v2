'use strict';

const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const src = fs.readFileSync(path.join(__dirname, '..', 'stroj', 'web', 'app.js'), 'utf8');
const start = src.indexOf('async function viewUsers()');
const end = src.indexOf('\n}', start) + 2;

async function renderUsers(users) {
  const nodes = Object.fromEntries(
    ['user-search', 'user-rows', 'user-count', 'user-nomatch'].map(id => [id, { value: '' }]));
  const headers = ['rank', 'username', 'score', 'solved', 'hardest', 'rating'].map(key => ({
    dataset: { key }, classList: { toggle() {} }, arrow: {},
  }));
  let html;
  const context = vm.createContext({
    api: async url => {
      assert.equal(url, '/api/users');
      return { decay: 0.95, users };
    },
    state: { user: null },
    userLink: name => `<a>${name}</a>`,
    pointsPill: points => String(points),
    rankBadge: () => 'Unrated',
    setView: value => { html = value; },
    $: (selector, parent) => parent ? parent.arrow : nodes[selector.slice(1)],
    $$: () => headers,
  });
  await vm.runInContext(`${src.slice(start, end)}\nviewUsers()`, context);
  return {
    html, nodes, headers,
    names: () => [...nodes['user-rows'].innerHTML.matchAll(/<a>(.*?)<\/a>/g)].map(m => m[1]),
  };
}

const account = (username, rank = null, score = 0) => ({
  username, rank, score, role: 'user', solved: rank === null ? 0 : 1,
  hardest: score, rating: 1000, rating_rank: null,
});

test('new users appear after ranked users, with working search and rank sorting', async () => {
  const view = await renderUsers([
    account('new-user'), account('second', 2, 50), account('first', 1, 100),
  ]);
  assert.deepEqual(view.names(), ['first', 'second', 'new-user']);
  assert.equal(view.nodes['user-count'].textContent, '3 users');
  assert.match(view.nodes['user-rows'].innerHTML, /class="rank">—<\/td>/);

  view.headers[0].onclick();
  assert.deepEqual(view.names(), ['second', 'first', 'new-user']);
  view.nodes['user-search'].value = ' NEW-USER ';
  view.nodes['user-search'].oninput();
  assert.deepEqual(view.names(), ['new-user']);
  assert.equal(view.nodes['user-count'].textContent, '1 of 3');
  assert.equal(view.nodes['user-nomatch'].hidden, true);

  view.nodes['user-search'].value = 'missing';
  view.nodes['user-search'].oninput();
  assert.deepEqual(view.names(), []);
  assert.equal(view.nodes['user-count'].textContent, '0 of 3');
  assert.equal(view.nodes['user-nomatch'].hidden, false);
});

test('accounts remain visible when nobody has scored yet', async () => {
  const view = await renderUsers([account('zoe'), account('alice')]);
  assert.deepEqual(view.names(), ['alice', 'zoe']);
  assert.equal(view.nodes['user-count'].textContent, '2 users');
  assert.match(view.html, /<table>/);
  assert.doesNotMatch(view.html, /Nobody has solved|No users yet/);
});
