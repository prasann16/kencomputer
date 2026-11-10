// Voice mode: an answer from Ken with something to look at docks the call to the corner so the chat shows it.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');

const js = readFileSync(join(__dirname, '..', 'web', 'app.js'), 'utf8');
const showable = eval(js.match(/const TALK_SHOWABLE = (\/.*\/);/)[1]);

test('links, drafts and markdown dock the call; a plain spoken answer does not', () => {
  for (const answer of ['Booked. Here it is: https://www.kayak.com/flights/x', 'See www.example.com', 'The [booking](https://x.y)',
    'Here’s the draft:\n\nHi Sam,\n\nFriday works.', '```\ncode\n```']) assert.ok(showable.test(answer), answer);
  for (const answer of ['You have the dentist at 3.', 'Done.', 'Two meetings today.\nBoth after lunch.']) assert.ok(!showable.test(answer), answer);
});
