'use strict';

/* The sign-in dialog's buttons.
 *
 * Pressing Enter in a field submits the form through its *default button* —
 * the first submit button in tree order. When Cancel was a submit button
 * sitting in front of Sign in, Enter cancelled instead of signing in, and the
 * dialog closed taking the typed credentials with it. Nothing about that is
 * visible in the rendered page, so it is pinned here.
 *
 * Run with `node tests/test_auth_dialog.js`, or through the Python suite. */

const fs = require('fs');
const path = require('path');

const web = path.join(__dirname, '..', 'stroj', 'web');
const html = fs.readFileSync(path.join(web, 'index.html'), 'utf8');
const js = fs.readFileSync(path.join(web, 'app.js'), 'utf8');

const form = html.slice(html.indexOf('<form method="dialog" id="auth-form"'),
                        html.indexOf('</form>', html.indexOf('id="auth-form"')));

let failures = 0, checks = 0;
function check(name, got, want) {
  checks += 1;
  if (got !== want) {
    failures += 1;
    console.error(`FAIL  ${name}: got ${JSON.stringify(got)}, want ${JSON.stringify(want)}`);
  }
}

// Every <button> in a form is type="submit" unless it says otherwise, so the
// absence of an explicit type is what makes a button a submit button.
const buttons = [...form.matchAll(/<button\b([^>]*)>/g)].map((m) => m[1]);
const isSubmit = (attrs) => !/type\s*=\s*"(button|reset)"/.test(attrs);

check('the dialog has exactly two buttons', buttons.length, 2);
check('exactly one of them submits', buttons.filter(isSubmit).length, 1);

const submitting = buttons.find(isSubmit);
check('the submitting one is Sign in', /id\s*=\s*"auth-submit"/.test(submitting), true);

const cancel = buttons.find((b) => /id\s*=\s*"auth-cancel"/.test(b));
check('Cancel exists', Boolean(cancel), true);
check('Cancel does not submit', isSubmit(cancel), false);

// Order still matters even with one submit button: adding a second submitting
// control in front of Sign in would bring the bug straight back.
const first = form.indexOf('<button');
const submitAt = form.indexOf('id="auth-submit"');
const anySubmitBefore = [...form.slice(first, submitAt).matchAll(/<button\b([^>]*)>/g)]
  .some((m) => isSubmit(m[1]));
check('nothing that submits sits in front of Sign in', anySubmitBefore, false);

// Cancel is a plain button now, so something has to close the dialog for it.
check('Cancel is wired to close the dialog',
  /\$\('#auth-cancel'\)\.onclick\s*=\s*\(\)\s*=>\s*dialog\.close\(\)/.test(js), true);

// The form is method="dialog": a submit that reaches the browser closes the
// dialog and discards what was typed, so no path may skip preventDefault.
const handler = js.slice(js.indexOf('form.onsubmit = async (event) => {'),
                         js.indexOf('dialog.showModal();'));
const bodyBeforeAwait = handler.slice(0, handler.indexOf('await'));
check('the submit handler prevents the default before anything else',
  bodyBeforeAwait.includes('event.preventDefault();'), true);
check('no early return can skip it',
  /return;[\s\S]*?event\.preventDefault\(\)/.test(bodyBeforeAwait), false);

console.log(`${checks - failures}/${checks} checks passed`);
process.exit(failures ? 1 : 0);
