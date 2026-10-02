'use strict';

/* What a rated contest's page says about its access code, and what the admin
 * editor's code panel says — to a member outside, a member in, a visitor, and
 * the admin running the room.
 *
 * Run with `node tests/test_access_code_view.js`, or through the Python
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
globalThis.absolute = (iso) => `at ${iso}`;
globalThis.userLink = eval(`(${grab('function userLink(')})`);
const accessCard = eval(`(${grab('function accessCard(')})`);
const accessPanelBody = eval(`(${grab('function accessPanelBody(')})`);

let failures = 0, checks = 0;
function check(name, got, want) {
  checks += 1;
  if (got !== want) {
    failures += 1;
    console.error(`FAIL  ${name}: got ${JSON.stringify(got)}, want ${JSON.stringify(want)}`);
  }
}
const has = (name, html, needle) => check(name, html.includes(needle), true);
const lacks = (name, html, needle) => check(name, html.includes(needle), false);

const member = { username: 'ann', is_admin: false };
const admin = { username: 'boss', is_admin: true };
const live = (access) => ({ slug: 'final', rated: true, state: 'running', access });
const outside = { needs_code: true, entered: false, code_ready: true };

/* ---- the contest page ---- */

const form = accessCard(live(outside), member);
has('a member outside is asked for the code', form, 'id="enter-form"');
has('and told where it comes from', form, 'contest room');
lacks('without being told it is unopened', form, 'not been opened');

has('a room not opened yet says so',
  accessCard(live({ ...outside, code_ready: false }), member), 'It has not been opened yet.');

const visitor = accessCard(live(outside), null);
lacks('a visitor gets no form to submit', visitor, '<form');
has('and is told to sign in', visitor, 'Sign in');

const inside = accessCard(live({ needs_code: false, entered: true, code_ready: true }), member);
lacks('a member in is not asked again', inside, '<form');
has('and is told they are in', inside, 'You have entered');

const boss = accessCard(live({ needs_code: false, entered: false, code_ready: false }), admin);
lacks('an admin is never asked', boss, '<form');
has('but is pointed at the code', boss, 'href="#/admin/contest/final"');

check('an unrated contest says nothing',
  accessCard({ slug: 'p', rated: false, state: 'running', access: {} }, member), '');
check('a finished rated contest says nothing',
  accessCard({ ...live(outside), state: 'ended' }, member), '');
has('a rated contest not started yet already takes the code',
  accessCard({ ...live(outside), state: 'before' }, member), 'id="enter-form"');

/* ---- the admin panel ---- */

const base = { code: null, rated: true, state: 'running', entrants: [] };

const unopened = accessPanelBody(base);
has('a rated contest with no code is a warning', unopened, 'Nobody can enter this contest yet');
has('styled as one', unopened, 'access-warn');
has('offers to generate', unopened, '>Generate code<');
lacks('with nothing to copy', unopened, 'access-copy');

const opened = accessPanelBody({ ...base, code: 'K7Q-XM4',
  entrants: [{ username: 'ann', role: 'user', entered_at: '2026-10-02T18:00:00.000Z' },
             { username: 'bob', role: 'user', entered_at: '2026-10-02T18:01:00.000Z' }] });
has('the code is shown', opened, '>K7Q-XM4<');
has('and can be replaced', opened, '>New code<');
has('and copied', opened, 'id="access-copy"');
has('entrants are counted', opened, '2 members have entered');
has('and named', opened, 'href="#/user/ann"');
lacks('the warning is gone', opened, 'access-warn');

has('one entrant reads as one', accessPanelBody({ ...base, code: 'AAA-BBB',
  entrants: [{ username: 'ann', role: 'user', entered_at: 'x' }] }), '1 member has entered');
has('nobody in yet keeps the list out of the way',
  accessPanelBody({ ...base, code: 'AAA-BBB' }), '<details class="small" hidden>');

has('an unrated contest explains a code does nothing',
  accessPanelBody({ ...base, rated: false }), 'Only rated contests ask for a code');
lacks('and is not a warning', accessPanelBody({ ...base, rated: false }), 'access-warn');
lacks('nor is a finished one', accessPanelBody({ ...base, state: 'ended' }), 'access-warn');

// Usernames are member-chosen text; they go through esc on the way out.
const nasty = accessPanelBody({ ...base, code: 'AAA-BBB',
  entrants: [{ username: '<img src=x>', role: 'user', entered_at: 'x' }] });
lacks('an entrant name is escaped', nasty, '<img');

console.log(`${checks - failures}/${checks} checks passed`);
process.exit(failures ? 1 : 0);
