import {APP_API, apiLevel, fit, newerAppKnown} from '../../src/protocol/compat';
import {isVoolPairingCode, parsePairingUri} from '../../src/protocol/pairing';
import {errorText, fitText} from '../../src/i18n/index';

const base = 'vool-pair://192.0.2.5:55031?fp=' + 'a'.repeat(64) + '&pid=pair_0123abcd&code=ABCDEFGH&exp=' + (Math.floor(Date.now() / 1000) + 600);

it('a computer from before the check works with this app, and its pairing code still reads', () => {
  expect(fit(undefined)).toBe('ok');
  expect(fit({})).toBe('ok');
  expect(parsePairingUri(base)?.fit).toBe('ok');
});

it('a computer that needs a newer app says so, from its code and from its answers', () => {
  expect(parsePairingUri(base + '&api=9&minapp=' + (APP_API + 1))?.fit).toBe('update_app');
  expect(fit({companion_api: 9, min_app_api: APP_API + 1})).toBe('update_app');
  expect(fitText('update_app')).toBe('Update the VOOL app on this phone, then try again.');
  expect(parsePairingUri(base + '&api=' + APP_API + '&minapp=1')?.fit).toBe('ok');
});

it('odd numbers count as the oldest level, never as a newer one', () => {
  for (const value of [undefined, null, 0, -1, 1.5, 'x', '99999999', {}, 1e9]) expect(apiLevel(value)).toBe(1);
  expect(apiLevel('3')).toBe(3);
});

it('a newer app is suggested only when the computer knows one', () => {
  expect(newerAppKnown({newest_app_api: APP_API})).toBe(false);
  expect(newerAppKnown({newest_app_api: APP_API + 1})).toBe(true);
});

it('a VOOL code this app cannot read is recognised, so the scanner says to update instead of going quiet', () => {
  const future = 'vool-pair://v3/' + 'x'.repeat(40);
  expect(parsePairingUri(future)).toBeNull();
  expect(isVoolPairingCode(future)).toBe(true);
  expect(isVoolPairingCode('https://example.invalid')).toBe(false);
});

it('the computer refusing an old app, or not knowing a request, reads as plain update advice', () => {
  expect(errorText({code: 'app_update_required', message: 'x'})).toBe('Update the VOOL app on this phone, then try again.');
  expect(errorText({code: 'action_not_offered', message: "the mobile companion does not offer 'x'"}))
    .toBe('VOOL on your computer is older and can’t do this yet. Update VOOL on your computer.');
});
