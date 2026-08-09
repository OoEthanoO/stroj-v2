'use strict';

/* Tests for the LaTeX → MathML renderer.
 *
 * Run directly with `node tests/test_latex.js`, or through the Python suite,
 * which shells out to node so that `pytest` still runs everything. */

const path = require('path');
const { renderMath } = require(path.join(__dirname, '..', 'stroj', 'web', 'latex.js'));

let failures = 0;
let checks = 0;

function check(name, condition, detail) {
  checks += 1;
  if (!condition) {
    failures += 1;
    console.error(`FAIL  ${name}`);
    if (detail) console.error(`      ${detail}`);
  }
}

const has = (name, src, needle, display = false) => {
  const out = renderMath(src, display);
  check(name, out.includes(needle), `${JSON.stringify(src)}\n      got: ${out}`);
};
const lacks = (name, src, needle, display = false) => {
  const out = renderMath(src, display);
  check(name, !out.includes(needle), `${JSON.stringify(src)}\n      got: ${out}`);
};

/* -------------------------------------------------------------- structure */

has('digits become mn', '42', '<mn>42</mn>');
has('letters become mi', 'x', '<mi>x</mi>');
has('superscript', 'x^2', '<msup><mi>x</mi><mn>2</mn></msup>');
has('subscript', 'a_i', '<msub><mi>a</mi><mi>i</mi></msub>');
has('braced script keeps both digits', '10^{15}', '<mn>15</mn>');
lacks('braced script is not truncated', '10^{15}', '<mn>1</mn><mn>5</mn>');
has('fraction', '\\frac{a}{b}', '<mfrac>');
has('square root', '\\sqrt{2}', '<msqrt>');
has('nth root', '\\sqrt[3]{x}', '<mroot>');
has('binomial', '\\binom{n}{k}', 'linethickness="0"');

/* ----------------------------------------------------------------- limits */

// A sum's bounds belong under and over it on a display line, but beside it
// inline, or a paragraph grows to three times its line height.
has('display sum stacks its limits', '\\sum_{i=1}^{n}', '<munderover>', true);
has('inline sum keeps limits beside', '\\sum_{i=1}^{n}', '<msubsup>', false);
has('display max stacks', '\\max_{i}', '<munder>', true);
has('inline max does not stack', '\\max_{i}', '<msub>', false);

/* ---------------------------------------------------------------- symbols */

has('relation', 'a \\le b', '≤');
has('greek lowercase', '\\alpha', 'α');
has('greek uppercase', '\\Omega', 'Ω');
has('set membership', 'x \\in S', '∈');
has('floor without left/right', '\\lfloor x \\rfloor', '⌊');
has('sized delimiters', '\\left( x \\right)', 'stretchy="true"');
has('blackboard bold', '\\mathbb{Z}', 'double-struck');
has('accent', '\\overline{x}', '<mover');

/* Functions need visible gaps or `O(n \log n)` reads as "O(nlogn)". */
has('function is upright', '\\log n', 'mathvariant="normal"');
has('function has spacing', '\\log n', 'lspace');

/* ------------------------------------------------------------ text blocks */

has('text renders as mtext', '\\text{even}', '<mtext>even</mtext>');
// HTML collapses a leading ordinary space, running the word into what precedes.
has('text keeps its leading space', 'n \\text{ even}', '\u00a0even');
has('operatorname is upright', '\\operatorname{lcm}', 'lcm');

/* ----------------------------------------------------------- environments */

const cases = renderMath(
  'f(x) = \\begin{cases} 1 & x > 0 \\\\ 0 & x \\le 0 \\end{cases}', true);
check('cases builds a table', cases.includes('<mtable'), cases);
check('cases has two rows', (cases.match(/<mtr>/g) || []).length === 2, cases);
check('cases has a brace', cases.includes('{'), cases);
// Browsers do not stretch a delimiter against an <mtable>, so the brace is
// sized by row count instead; without that a two-row cases wears a one-line brace.
check('cases brace is grown', /font-size:[\d.]+em/.test(cases), cases);

const matrix = renderMath('\\begin{pmatrix} a & b \\\\ c & d \\end{pmatrix}', true);
check('matrix has two rows', (matrix.match(/<mtr>/g) || []).length === 2, matrix);
check('matrix has four cells', (matrix.match(/<mtd>/g) || []).length === 4, matrix);

/* ----------------------------------------------------------------- spacing */

// The spacing commands are punctuation after a backslash, so they reach the
// parser as escapes rather than named commands. Emitted literally, `\!` shows
// a factorial and changes what the formula says.
has('thin space is a space', '200\\,000', '<mspace');
lacks('thin space is not a comma', '200\\,000', '<mo>,</mo>');
has('negative thin space is a space', 'a\\!b', '<mspace');
lacks('negative thin space is not a factorial', 'a\\!b', '<mo>!</mo>');
has('backslash-space is a space', 'a\\ b', '<mspace');
has('quad still works', 'a \\quad b', 'width="1em"');
// A real factorial and a real percent must survive all of that.
has('factorial is preserved', 'n!', '<mo>!</mo>');
has('percent is preserved', '50\\%', '<mo>%</mo>');

/* ------------------------------------------------------------------ signs */

// A binary minus is spaced on both sides, so `[1, 2, -3]` reads as a
// subtraction unless the sign is recognised as unary from what precedes it.
has('sign after a comma is unary', '[1, 2, -3]', 'form="prefix"');
has('sign at the start is unary', '-10^9', 'form="prefix"');
has('sign after a relation is unary', 'x = -y', 'form="prefix"');
lacks('sign between operands stays binary', 'a - b', 'form="prefix"');
lacks('sign after a closing paren stays binary', '(a+b) - c', 'form="prefix"');
lacks('sign after a number stays binary', 'n - 1', 'form="prefix"');
// An ellipsis is an ordinary atom, not an operator: `\cdots + a_j` adds.
lacks('sign after an ellipsis stays binary', 'a_i + \\cdots + a_j', 'form="prefix"');
lacks('sign after infinity stays binary', '\\infty - 1', 'form="prefix"');
lacks('sign after a closing floor stays binary', '\\lfloor x \\rfloor - 1', 'form="prefix"');
has('sign after a big operator is unary', '\\sum -a_i', 'form="prefix"');
// Math sets a minus sign, not a hyphen.
has('minus is a real minus sign', 'a - b', '\u2212');
lacks('minus is not an ascii hyphen', 'a - b', '<mo>-</mo>');

/* ------------------------------------------------- escaping and injection */

// Statement text reaches the renderer already HTML-escaped, and the result is
// written with innerHTML, so both directions have to be right.
has('escaped less-than survives as an operator', 'a &lt; b', '&lt;');
lacks('less-than is not left raw', 'a &lt; b', '<mo><</mo>');
has('escaped greater-than survives', 'a &gt; b', '&gt;');

const injected = renderMath('&lt;img src=x onerror=alert(1)&gt;');
check('no raw tag escapes the renderer', !/<img/i.test(injected), injected);
const injectedText = renderMath('\\text{&lt;script&gt;alert(1)&lt;/script&gt;}');
check('no raw tag escapes \\text', !/<script/i.test(injectedText), injectedText);

/* ------------------------------------------------------------- robustness */

// A statement that renders oddly can be fixed; one that throws takes the whole
// page down, so nothing here may raise.
const rough = [
  '', '   ', '\\frac{1}{', '{{{{', '}}}}', '\\left(', '\\right)', 'a & b',
  '\\begin{cases}', '\\end{cases}', '^', '_', 'x^', '\\sqrt[', '\\unknown{x}',
  '\\\\', '$', '\\text{', '&', '\\begin{nope} x \\end{nope}',
];
for (const src of rough) {
  let ok = true;
  let out = '';
  try { out = renderMath(src, true); } catch (err) { ok = false; out = String(err); }
  check(`survives ${JSON.stringify(src)}`, ok && out.startsWith('<math'), out);
}

check('unknown command shows its source',
  renderMath('\\unknown').includes('\\unknown'), renderMath('\\unknown'));
check('loose ampersand is kept',
  renderMath('a & b').includes('<mi>b</mi>'), renderMath('a & b'));

/* ------------------------------------------------------------------ shape */

for (const [src, display] of [['x', false], ['\\sum_{i=1}^{n} a_i', true]]) {
  const out = renderMath(src, display);
  check(`well-formed wrapper for ${JSON.stringify(src)}`,
    out.startsWith('<math ') && out.endsWith('</math>'), out);
  check(`balanced angle brackets for ${JSON.stringify(src)}`,
    (out.match(/</g) || []).length === (out.match(/>/g) || []).length, out);
}
check('display attribute is set', renderMath('x', true).includes('display="block"'));
check('inline attribute is set', renderMath('x', false).includes('display="inline"'));

console.log(`${checks - failures}/${checks} checks passed`);
process.exit(failures ? 1 : 0);
