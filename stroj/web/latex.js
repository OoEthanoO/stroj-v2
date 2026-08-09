'use strict';

/* ------------------------------------------------------------------ LaTeX

   Problem statements are mathematical, so the statement renderer speaks a
   working subset of LaTeX and emits MathML, which every current browser lays
   out natively.

   MathML rather than a library: the rest of this frontend has no dependencies
   and no build step, and a statement has to be readable on school wifi, on a
   phone, and on the first paint. Shipping KaTeX would mean vendoring ~600 KB
   of script and fonts into the repo; loading it from a CDN would mean a
   statement that renders as raw source until a third party answers.

   The subset is what contest statements actually use: scripts, fractions,
   roots, big operators with limits, the Greek and relation symbols, sized
   delimiters, text runs, accents, cases and matrices. Anything unrecognised
   degrades to its own literal source rather than throwing, because a
   statement that reads oddly is recoverable and one that renders as an empty
   box is not. */

const MATH_NS = 'http://www.w3.org/1998/Math/MathML';

const MATH_GREEK = {
  alpha: 'α', beta: 'β', gamma: 'γ', delta: 'δ', epsilon: 'ϵ', varepsilon: 'ε',
  zeta: 'ζ', eta: 'η', theta: 'θ', vartheta: 'ϑ', iota: 'ι', kappa: 'κ',
  lambda: 'λ', mu: 'μ', nu: 'ν', xi: 'ξ', omicron: 'ο', pi: 'π', varpi: 'ϖ',
  rho: 'ρ', varrho: 'ϱ', sigma: 'σ', varsigma: 'ς', tau: 'τ', upsilon: 'υ',
  phi: 'ϕ', varphi: 'φ', chi: 'χ', psi: 'ψ', omega: 'ω',
  Gamma: 'Γ', Delta: 'Δ', Theta: 'Θ', Lambda: 'Λ', Xi: 'Ξ', Pi: 'Π',
  Sigma: 'Σ', Upsilon: 'Υ', Phi: 'Φ', Psi: 'Ψ', Omega: 'Ω',
};

/** Commands that stand for one symbol, rendered as an operator. */
const MATH_SYMBOLS = {
  le: '≤', leq: '≤', ge: '≥', geq: '≥', ne: '≠', neq: '≠',
  ll: '≪', gg: '≫', leqslant: '⩽', geqslant: '⩾',
  times: '×', cdot: '⋅', div: '÷', ast: '∗', star: '⋆', bullet: '∙',
  pm: '±', mp: '∓', oplus: '⊕', ominus: '⊖', otimes: '⊗', odot: '⊙',
  approx: '≈', equiv: '≡', sim: '∼', simeq: '≃', cong: '≅', propto: '∝',
  in: '∈', notin: '∉', ni: '∋', subset: '⊂', subseteq: '⊆', subsetneq: '⊊',
  supset: '⊃', supseteq: '⊇', cup: '∪', cap: '∩', setminus: '∖',
  emptyset: '∅', varnothing: '∅', complement: '∁',
  forall: '∀', exists: '∃', nexists: '∄', neg: '¬', lnot: '¬',
  land: '∧', wedge: '∧', lor: '∨', vee: '∨',
  to: '→', rightarrow: '→', leftarrow: '←', gets: '←',
  leftrightarrow: '↔', longrightarrow: '⟶', longleftarrow: '⟵',
  Rightarrow: '⇒', implies: '⟹', Leftarrow: '⇐', impliedby: '⟸',
  Leftrightarrow: '⇔', iff: '⟺', mapsto: '↦', hookrightarrow: '↪',
  uparrow: '↑', downarrow: '↓',
  infty: '∞', partial: '∂', nabla: '∇', angle: '∠', perp: '⊥',
  parallel: '∥', mid: '∣', nmid: '∤', therefore: '∴', because: '∵',
  dots: '…', ldots: '…', cdots: '⋯', vdots: '⋮', ddots: '⋱',
  // Also reachable without \left/\right, which is how statements usually
  // write a floor or a ceiling.
  lfloor: '⌊', rfloor: '⌋', lceil: '⌈', rceil: '⌉',
  langle: '⟨', rangle: '⟩', vert: '|', Vert: '‖',
  lbrace: '{', rbrace: '}', lbrack: '[', rbrack: ']', backslash: '\\',
  prime: '′', circ: '∘', degree: '°', surd: '√', checkmark: '✓',
  aleph: 'ℵ', hbar: 'ℏ', ell: 'ℓ', Re: 'ℜ', Im: 'ℑ', wp: '℘',
  triangle: '△', square: '□', diamond: '⋄', bowtie: '⋈',
};

/** Operators that take limits above and below when set as display math. */
const MATH_BIG = {
  sum: '∑', prod: '∏', coprod: '∐', int: '∫', iint: '∬', iiint: '∭',
  oint: '∮', bigcup: '⋃', bigcap: '⋂', bigsqcup: '⨆', bigoplus: '⨁',
  bigotimes: '⨂', bigvee: '⋁', bigwedge: '⋀', bigodot: '⨀',
};

/** Named functions, set upright so `\log n` does not read as l·o·g·n. */
const MATH_FUNCTIONS = new Set([
  'log', 'ln', 'lg', 'exp', 'sin', 'cos', 'tan', 'cot', 'sec', 'csc',
  'arcsin', 'arccos', 'arctan', 'sinh', 'cosh', 'tanh', 'coth',
  'det', 'dim', 'ker', 'hom', 'arg', 'deg', 'Pr',
]);

/** Functions that also take limits underneath in display math. */
const MATH_LIMIT_FUNCTIONS = new Set([
  'lim', 'limsup', 'liminf', 'max', 'min', 'sup', 'inf', 'gcd', 'lcm',
  'argmax', 'argmin',
]);

const MATH_VARIANTS = {
  mathrm: 'normal', mathbf: 'bold', mathit: 'italic', mathsf: 'sans-serif',
  mathtt: 'monospace', mathbb: 'double-struck', mathcal: 'script',
  mathfrak: 'fraktur', boldsymbol: 'bold-italic', bm: 'bold-italic',
};

const MATH_ACCENTS = {
  hat: '^', widehat: '^', tilde: '~', widetilde: '~', bar: '¯', overline: '¯',
  vec: '→', dot: '˙', ddot: '¨', check: 'ˇ', acute: '´', grave: '`', breve: '˘',
};

const MATH_SPACING = {
  ',': '0.1667em', ':': '0.2222em', ';': '0.2778em', '!': '-0.1667em',
  ' ': '0.25em', quad: '1em', qquad: '2em', enspace: '0.5em', thinspace: '0.1667em',
};

/** Delimiters usable after \left and \right. */
const MATH_DELIMITERS = {
  '(': '(', ')': ')', '[': '[', ']': ']', '|': '|', '/': '/',
  '\\{': '{', '\\}': '}', '\\|': '‖', '\\langle': '⟨', '\\rangle': '⟩',
  '\\lfloor': '⌊', '\\rfloor': '⌋', '\\lceil': '⌈', '\\rceil': '⌉',
  '\\vert': '|', '\\Vert': '‖', '\\backslash': '\\', '.': '',
};

/** Environments that lay out as a table, with the fences they carry. */
const MATH_ENVIRONMENTS = {
  cases: ['{', ''], matrix: ['', ''], pmatrix: ['(', ')'],
  bmatrix: ['[', ']'], Bmatrix: ['{', '}'], vmatrix: ['|', '|'],
  Vmatrix: ['‖', '‖'], aligned: ['', ''], gathered: ['', ''],
  smallmatrix: ['', ''],
};

/* The statement text reaches this module already HTML-escaped, so `a < b`
   arrives as `a &lt; b`. Undo exactly the five entities the escaper produces —
   one pass, so a literal `&amp;lt;` in a statement cannot decode twice. */
const MATH_ENTITIES = {
  '&amp;': '&', '&lt;': '<', '&gt;': '>', '&quot;': '"', '&#39;': "'",
};
const mathUnescape = (s) =>
  s.replace(/&(?:amp|lt|gt|quot|#39);/g, (m) => MATH_ENTITIES[m]);

const mathEscape = (s) =>
  String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

/* `\text{ even}` carries a deliberate leading space that HTML whitespace
   collapsing would eat, running the word into whatever precedes it. Pin the
   outer spaces open with non-breaking ones. */
const mathText = (s) =>
  mathEscape(String(s ?? '')).replace(/^ +| +$/g, (run) => ' '.repeat(run.length));

/** Commands whose argument is literal text rather than math. */
const MATH_TEXT_COMMANDS = new Set([
  'text', 'textrm', 'textbf', 'textit', 'texttt', 'textsf', 'mbox',
  'operatorname', 'begin', 'end',
]);

function mathTokenize(src) {
  const tokens = [];
  let i = 0;
  while (i < src.length) {
    const c = src[i];
    if (/\s/.test(c)) { i += 1; continue; }

    if (c === '\\') {
      const word = src.slice(i + 1).match(/^[A-Za-z]+/);
      if (!word) {
        // A backslash escape: \{ \} \$ \% \& \_ and friends.
        tokens.push({ t: 'esc', v: src[i + 1] ?? '' });
        i += 2;
        continue;
      }
      const name = word[0];
      i += 1 + name.length;
      if (MATH_TEXT_COMMANDS.has(name)) {
        // Take the braced argument verbatim, so spaces inside \text survive
        // a tokenizer that otherwise drops them.
        const rest = src.slice(i);
        const braced = rest.match(/^\s*\{([^{}]*)\}/);
        if (braced) {
          tokens.push({ t: 'cmd', v: name, raw: braced[1] });
          i += braced[0].length;
          continue;
        }
      }
      tokens.push({ t: 'cmd', v: name });
      continue;
    }

    if (c === '{' || c === '}' || c === '^' || c === '_' || c === '&') {
      tokens.push({ t: c });
      i += 1;
      continue;
    }
    const num = src.slice(i).match(/^\d+(?:[.,]\d+)*/);
    if (num) { tokens.push({ t: 'num', v: num[0] }); i += num[0].length; continue; }
    if (/[A-Za-z]/.test(c)) { tokens.push({ t: 'ident', v: c }); i += 1; continue; }
    tokens.push({ t: 'op', v: c });
    i += 1;
  }
  return tokens;
}

function mathParse(tokens, display) {
  let pos = 0;
  const peek = () => tokens[pos];
  const at = (t, v) => {
    const tok = peek();
    return !!tok && tok.t === t && (v === undefined || tok.v === v);
  };

  const wrap = (items) =>
    items.length === 1 ? items[0] : `<mrow>${items.join('')}</mrow>`;

  /** One argument: a braced group, or the single atom that follows. */
  function argument() {
    if (at('{')) return atom();
    const one = atom();
    return one === null ? '<mrow></mrow>' : one;
  }

  function row(stop) {
    const items = [];
    while (pos < tokens.length && !stop()) {
      const next = scripted();
      if (next === null) break;
      items.push(next);
    }
    return items;
  }

  /** An atom plus any sub/superscripts bound to it. */
  function scripted() {
    const base = atom();
    if (base === null) return null;
    let sub = null;
    let sup = null;
    while (at('_') || at('^')) {
      const kind = peek().t;
      pos += 1;
      const value = argument();
      if (kind === '_') sub = value; else sup = value;
    }
    if (sub === null && sup === null) return base;

    // Limits sit under and over a big operator in display math, but beside it
    // inline, or a line of prose would grow to three times its height.
    const stacked = display && /data-limits="1"/.test(base);
    const tag = (a, b) => (stacked ? a : b);
    if (sub !== null && sup !== null) {
      const t = tag('munderover', 'msubsup');
      return `<${t}>${base}${sub}${sup}</${t}>`;
    }
    if (sub !== null) {
      const t = tag('munder', 'msub');
      return `<${t}>${base}${sub}</${t}>`;
    }
    const t = tag('mover', 'msup');
    return `<${t}>${base}${sup}</${t}>`;
  }

  function delimiter() {
    const tok = peek();
    if (!tok) return '';
    pos += 1;
    if (tok.t === 'cmd') return MATH_DELIMITERS['\\' + tok.v] ?? '';
    if (tok.t === 'esc') return MATH_DELIMITERS['\\' + tok.v] ?? tok.v;
    return MATH_DELIMITERS[tok.v] ?? tok.v ?? '';
  }

  function environment(name) {
    const [open, close] = MATH_ENVIRONMENTS[name] ?? ['', ''];
    const rows = [];
    let cells = [[]];
    while (pos < tokens.length && !at('cmd', 'end')) {
      if (at('&')) { pos += 1; cells.push([]); continue; }
      if (at('esc', '\\')) { pos += 1; rows.push(cells); cells = [[]]; continue; }
      const next = scripted();
      if (next === null) break;
      cells[cells.length - 1].push(next);
    }
    if (at('cmd', 'end')) pos += 1;
    rows.push(cells);
    const body = rows
      .map((r) => `<mtr>${r.map((c) => `<mtd>${wrap(c)}</mtd>`).join('')}</mtr>`)
      .join('');
    const align = name === 'cases' ? ' columnalign="left"' : '';
    const table = `<mtable${align}>${body}</mtable>`;
    if (!open && !close) return `<mrow>${table}</mrow>`;
    // Browsers stretch delimiters against most things but not against an
    // <mtable>, which leaves a two-row `cases` wearing a one-line brace.
    // Sizing it by the row count gets the same result everywhere.
    const grown = Math.min(1 + (rows.length - 1) * 1.15, 4).toFixed(2);
    const fence = (ch) =>
      `<mo stretchy="true" style="font-size:${grown}em">${mathEscape(ch)}</mo>`;
    return `<mrow>${open ? fence(open) : ''}` +
      `${table}${close ? fence(close) : ''}</mrow>`;
  }

  function command(tok) {
    const name = tok.v;

    if (MATH_GREEK[name]) return `<mi>${MATH_GREEK[name]}</mi>`;
    if (MATH_SYMBOLS[name]) return `<mo>${mathEscape(MATH_SYMBOLS[name])}</mo>`;
    if (MATH_BIG[name]) {
      return `<mo data-limits="1" largeop="true" movablelimits="true">` +
        `${mathEscape(MATH_BIG[name])}</mo>`;
    }
    if (MATH_FUNCTIONS.has(name)) {
      // As <mi> these sit flush against their argument and `O(n \log n)` reads
      // as "O(nlogn)". An <mo> with explicit spacing is what separates them.
      return `<mo lspace="0.167em" rspace="0.167em" mathvariant="normal">${name}</mo>`;
    }
    if (MATH_LIMIT_FUNCTIONS.has(name)) {
      return `<mo data-limits="1" movablelimits="true" mathvariant="normal">${name}</mo>`;
    }
    if (MATH_SPACING[name] !== undefined) {
      return `<mspace width="${MATH_SPACING[name]}"></mspace>`;
    }

    switch (name) {
      case 'frac': case 'dfrac': case 'tfrac': case 'cfrac': {
        const num = argument();
        const den = argument();
        return `<mfrac>${num}${den}</mfrac>`;
      }
      case 'binom': case 'dbinom': case 'tbinom': {
        const top = argument();
        const bottom = argument();
        return `<mrow><mo stretchy="true">(</mo>` +
          `<mfrac linethickness="0">${top}${bottom}</mfrac>` +
          `<mo stretchy="true">)</mo></mrow>`;
      }
      case 'sqrt': {
        // \sqrt[3]{x}: the index is an optional bracketed argument.
        if (at('op', '[')) {
          pos += 1;
          const index = row(() => at('op', ']'));
          if (at('op', ']')) pos += 1;
          return `<mroot>${argument()}${wrap(index)}</mroot>`;
        }
        return `<msqrt>${argument()}</msqrt>`;
      }
      case 'text': case 'textrm': case 'mbox':
        return `<mtext>${mathText(tok.raw ?? '')}</mtext>`;
      case 'textbf':
        return `<mtext mathvariant="bold">${mathText(tok.raw ?? '')}</mtext>`;
      case 'textit':
        return `<mtext mathvariant="italic">${mathText(tok.raw ?? '')}</mtext>`;
      case 'texttt':
        return `<mtext mathvariant="monospace">${mathText(tok.raw ?? '')}</mtext>`;
      case 'textsf':
        return `<mtext mathvariant="sans-serif">${mathText(tok.raw ?? '')}</mtext>`;
      case 'operatorname':
        return `<mi mathvariant="normal">${mathEscape(tok.raw ?? '')}</mi>`;
      case 'begin':
        return environment(tok.raw ?? '');
      case 'end':
        return '';
      case 'left': {
        const open = delimiter();
        const inner = row(() => at('cmd', 'right'));
        if (at('cmd', 'right')) pos += 1;
        const close = delimiter();
        return `<mrow>${open ? `<mo stretchy="true">${mathEscape(open)}</mo>` : ''}` +
          `${inner.join('')}` +
          `${close ? `<mo stretchy="true">${mathEscape(close)}</mo>` : ''}</mrow>`;
      }
      case 'right':
        return '';
      case 'bmod':
        return '<mo lspace="0.222em" rspace="0.222em">mod</mo>';
      case 'pmod':
        return `<mrow><mspace width="0.444em"></mspace><mo>(</mo>` +
          `<mi mathvariant="normal">mod</mi><mspace width="0.333em"></mspace>` +
          `${argument()}<mo>)</mo></mrow>`;
      case 'overbrace': case 'underbrace': {
        const inner = argument();
        const brace = name === 'overbrace' ? '⏞' : '⏟';
        const tag = name === 'overbrace' ? 'mover' : 'munder';
        return `<${tag}>${inner}<mo stretchy="true">${brace}</mo></${tag}>`;
      }
      case 'substack':
        return argument();
      case 'limits': case 'nolimits': case 'displaystyle':
      case 'textstyle': case 'scriptstyle': case 'nonumber':
        return '';
    }

    if (MATH_VARIANTS[name]) {
      // mathvariant applies per token, so push it onto every leaf inside.
      const inner = argument();
      return `<mstyle mathvariant="${MATH_VARIANTS[name]}">${inner}</mstyle>`;
    }
    if (MATH_ACCENTS[name]) {
      const wide = name === 'overline' || name === 'bar' || name === 'widehat'
        || name === 'widetilde' || name === 'vec';
      return `<mover accent="true">${argument()}` +
        `<mo${wide ? ' stretchy="true"' : ''}>${mathEscape(MATH_ACCENTS[name])}</mo></mover>`;
    }
    if (name === 'underline') {
      return `<munder accent="true">${argument()}<mo stretchy="true">_</mo></munder>`;
    }

    // Unknown command: show its source rather than swallowing it, so a typo
    // is visible to whoever wrote the statement.
    return `<mi mathvariant="normal">${mathEscape('\\' + name)}</mi>`;
  }

  function atom() {
    const tok = peek();
    if (!tok) return null;

    if (tok.t === '}') return null;
    if (tok.t === '{') {
      pos += 1;
      const items = row(() => at('}'));
      if (at('}')) pos += 1;
      return `<mrow>${items.join('')}</mrow>`;
    }
    // `_` and `^` bind to a preceding atom, so they never start one. `&` only
    // means "next cell" inside an environment, which intercepts it before we
    // get here; loose in a formula it is just an ampersand.
    if (tok.t === '_' || tok.t === '^') return null;
    if (tok.t === '&') { pos += 1; return '<mo>&amp;</mo>'; }

    pos += 1;
    if (tok.t === 'num') return `<mn>${tok.v}</mn>`;
    if (tok.t === 'ident') return `<mi>${tok.v}</mi>`;
    if (tok.t === 'esc') {
      if (tok.v === '\\') return '<mspace linebreak="newline"></mspace>';
      return `<mo>${mathEscape(tok.v)}</mo>`;
    }
    if (tok.t === 'cmd') return command(tok);
    // Plain operator or punctuation.
    if (tok.v === "'") return '<mo>′</mo>';
    return `<mo>${mathEscape(tok.v)}</mo>`;
  }

  const items = row(() => false);
  return items.join('');
}

/**
 * Render one span of LaTeX as MathML.
 *
 * `src` arrives HTML-escaped, the way the statement renderer holds it.
 * Never throws: a malformed span falls back to its own source in a way that
 * still reads, because half a rendered statement is worse than none.
 */
function renderMath(src, display = false) {
  const source = mathUnescape(String(src ?? ''));
  let body;
  try {
    body = mathParse(mathTokenize(source), display);
  } catch (err) {
    body = `<mtext>${mathEscape(source)}</mtext>`;
  }
  if (!body) body = '<mtext></mtext>';
  const attrs = `xmlns="${MATH_NS}" display="${display ? 'block' : 'inline'}"`;
  // The source stays on the element so it can be copied back out, and so a
  // statement that renders wrongly can be diagnosed from the page itself.
  return `<math ${attrs} class="${display ? 'math-display' : 'math-inline'}">${body}</math>`;
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { renderMath, mathTokenize, mathParse };
}
