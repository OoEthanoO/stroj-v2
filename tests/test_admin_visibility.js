'use strict';

const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const src = fs.readFileSync(path.join(__dirname, '..', 'stroj', 'web', 'app.js'), 'utf8');
const helpers = src.slice(src.indexOf('const statePill ='), src.indexOf('function typeMigratePanel('));
const start = src.indexOf('async function viewAdmin()');
const admin = src.slice(start, src.indexOf('\n}', start) + 2);

async function openAdmin() {
  // More than one page matches the search; another row is filtered out.
  const problems = Array.from({ length: 120 }, (_, i) => ({
    slug: `practice-${i}`, title: `Practice ${i}`, visible: false, types: [],
  })).concat([{ slug: 'other', title: 'Other', visible: false, types: [] }]);
  const nodes = {}, requests = [], notices = [];
  let buttons = [], pageRenders = 0, rejectPatch = false;
  const context = vm.createContext({
    state: { user: { is_admin: true } },
    esc: value => String(value),
    api: async (url, options = {}) => {
      requests.push({ url, method: options.method || 'GET', body: options.body });
      if (options.method === 'PATCH') {
        if (rejectPatch) throw new Error('Could not save visibility.');
        const item = problems.find(p => url === `/api/admin/problems/${p.slug}`);
        assert.ok(item, 'a real problem is being updated');
        Object.assign(item, options.body);
        return { ok: true };
      }
      if (url === '/api/problems') return { problems: structuredClone(problems) };
      if (url === '/api/contests') return { contests: [] };
      if (url === '/api/admin/users') return { users: [] };
      if (url === '/api/admin/posts') return { posts: [] };
      throw new Error(`Unexpected request: ${url}`);
    },
    $: selector => nodes[selector] || null,
    $$: selector => selector === '[data-toggle]' ? buttons : [],
    setView: () => {
      pageRenders += 1;
      for (const id of ['posts', 'problems', 'contests', 'users']) {
        nodes[`#${id}-q`] = { value: '' };
        nodes[`#${id}-count`] = {};
        nodes[`#${id}-more`] = {};
        let html = '';
        nodes[`#${id}-rows`] = {
          get innerHTML() { return html; },
          set innerHTML(value) {
            html = value;
            if (id === 'problems') {
              buttons = [...value.matchAll(/data-toggle="([^"]+)" data-visible="([01])"/g)]
                .map(match => ({ dataset: { toggle: match[1], visible: match[2] } }));
            }
          },
        };
      }
      nodes['#x-limits'] = { value: '' };
      nodes['#x-report'] = { value: '' };
    },
    // A visibility change must not navigate away or rebuild the whole view.
    route: () => { throw new Error('Unexpected page navigation.'); },
    toast: message => notices.push(message),
    expressPanel: () => ({ html: '', bind() {} }),
    typeMigratePanel: () => ({ html: '', bind() {} }),
    bindAdminPostRows() {}, bindAdminContestRows() {}, bindAdminUserRows() {},
  });
  await vm.runInContext(`${helpers}\n${admin}\nviewAdmin()`, context);
  nodes['#problems-q'].value = 'practice';
  nodes['#problems-q'].oninput();
  assert.equal(buttons.length, 50);
  nodes['#problems-more'].onclick();
  nodes['#x-limits'].value = 'unsaved limits';
  nodes['#x-report'].value = 'calibration report';
  return {
    nodes, notices, requests,
    get buttons() { return buttons; },
    get pageRenders() { return pageRenders; },
    rejectPatch() { rejectPatch = true; },
  };
}

test('Show and Hide keep the expanded search and express work in place', async () => {
  const page = await openAdmin();
  const rows = page.nodes['#problems-rows'];
  const target = page.buttons[80].dataset.toggle;
  for (const visible of [true, false]) {
    const button = page.buttons.find(b => b.dataset.toggle === target);
    await button.onclick();
    assert.deepEqual(page.notices, []);
    assert.equal(page.pageRenders, 1);
    assert.equal(page.nodes['#problems-rows'], rows);
    assert.equal(page.nodes['#problems-q'].value, 'practice');
    assert.equal(page.buttons.length, 120);
    assert.equal(page.nodes['#problems-more'].hidden, true);
    assert.equal(page.nodes['#problems-count'].textContent, '120 of 121');
    assert.equal(page.nodes['#x-limits'].value, 'unsaved limits');
    assert.equal(page.nodes['#x-report'].value, 'calibration report');
    assert.equal(page.buttons.find(b => b.dataset.toggle === target).dataset.visible,
      visible ? '1' : '0');
    const request = page.requests.filter(r => r.method === 'PATCH').at(-1);
    assert.equal(request.url, `/api/admin/problems/${target}`);
    assert.equal(request.body.visible, visible);
  }
  assert.equal(page.requests.filter(r => r.url === '/api/admin/users').length, 1);
});

test('a failed visibility change leaves the expanded list and button intact', async () => {
  const page = await openAdmin();
  const button = page.buttons[80];
  const rowsBefore = page.nodes['#problems-rows'].innerHTML;
  page.rejectPatch();
  await button.onclick();
  assert.deepEqual(page.notices, ['Could not save visibility.']);
  assert.equal(page.pageRenders, 1);
  assert.equal(page.nodes['#problems-rows'].innerHTML, rowsBefore);
  assert.equal(page.buttons.length, 120);
  assert.equal(button.dataset.visible, '0');
});
