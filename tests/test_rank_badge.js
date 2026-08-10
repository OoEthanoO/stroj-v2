'use strict';

/* The rank chip: what it shows, and what it refuses to show.
 *
 * Run with `node tests/test_rank_badge.js`, or through the Python suite. */

const fs = require('fs');
const path = require('path');

const src = fs.readFileSync(
  path.join(__dirname, '..', 'stroj', 'web', 'app.js'), 'utf8');
const start = src.indexOf('function rankBadge(');
// Anchor past the parameter list: the signature destructures, so the first
// brace after `function` belongs to the parameters, not the body.
const bodyAt = src.indexOf('{', src.indexOf(')', start));
let depth = 0, i = bodyAt;
for (; i < src.length; i++) {
  if (src[i] === '{') depth++;
  else if (src[i] === '}') { depth--; if (!depth) break; }
}
// The page's own escaper and its table, so the badge is tested against the
// real one rather than a stand-in that might be more forgiving.
const lineFor = (decl) => {
  const at = src.indexOf(decl);
  return src.slice(at, src.indexOf('\n', at));
};
const ESCAPES = eval(`(${lineFor('const ESCAPES =').split('=').slice(1).join('=').replace(/;$/, '')})`);
const esc = eval(`(${lineFor('const esc =').split('=').slice(1).join('=').replace(/;$/, '')})`);
const rankBadge = eval(`(${src.slice(start, i + 1)})`);

let failures = 0, checks = 0;
function check(name, got, want) {
  checks += 1;
  if (got !== want) {
    failures += 1;
    console.error(`FAIL  ${name}:\n  got  ${got}\n  want ${want}`);
  }
}
function contains(name, haystack, needle) {
  checks += 1;
  if (!String(haystack).includes(needle)) {
    failures += 1;
    console.error(`FAIL  ${name}: ${haystack} does not contain ${needle}`);
  }
}
function absent(name, haystack, needle) {
  checks += 1;
  if (String(haystack).includes(needle)) {
    failures += 1;
    failures && console.error(`FAIL  ${name}: ${haystack} should not contain ${needle}`);
  }
}

const rank = (tier, division, index) => ({ tier, division, index, name:
  division === null ? tier : `${tier} ${division}`, of: 25 });

// Every tier gets its own class, so the colour is driven by data not by a
// hand-maintained switch somewhere else.
for (const tier of ['Iron', 'Bronze', 'Silver', 'Gold', 'Platinum',
                    'Diamond', 'Ascendant', 'Immortal']) {
  const html = rankBadge(rank(tier, 2, 4));
  contains(`${tier} carries its class`, html, `rank-${tier.toLowerCase()}`);
  contains(`${tier} shows its division`, html, `${tier} 2`);
}

const radiant = rankBadge(rank('Radiant', null, 24));
contains('radiant has its own class', radiant, 'rank-radiant');
contains('radiant names itself', radiant, '>Radiant<');
absent('radiant shows no division number', radiant, 'Radiant 1');

// The case the whole design turns on: no contests, no rank.
const none = rankBadge(null);
contains('unranked says so', none, 'Unranked');
contains('unranked has its own class', none, 'rank-unranked');
absent('unranked claims no tier', none, 'rank-iron');

// A rating rides along only when asked for, and never for the unranked.
contains('rating shown when given', rankBadge(rank('Gold', 1, 9), { rating: 1204 }), '1204');
absent('no rating when omitted', rankBadge(rank('Gold', 1, 9)), 'rank-rating');
absent('no rating for unranked', rankBadge(null, { rating: 1000 }), '1000');

// A tier name is data from the server, so it goes through the escaper.
const nasty = rankBadge(rank('<script>', 1, 0));
absent('tier names are escaped', nasty, '<script>');

console.log(`${checks - failures}/${checks} checks passed`);
process.exit(failures ? 1 : 0);
