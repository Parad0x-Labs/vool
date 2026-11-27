import {phoneModelName} from '../../src/protocol/deviceName.ts';

describe('the name a phone pairs under', () => {
  it('is the maker and model an Android phone reports, without saying the maker twice', () => {
    expect(phoneModelName('android', {Manufacturer: 'samsung', Model: 'SM-S918B'})).toBe('Samsung SM-S918B');
    expect(phoneModelName('android', {Manufacturer: 'Google', Model: 'Pixel 8'})).toBe('Google Pixel 8');
    expect(phoneModelName('android', {Manufacturer: 'OnePlus', Model: 'OnePlus 12'})).toBe('OnePlus 12');
    expect(phoneModelName('android', {Brand: 'solanamobile', Model: 'Seeker'})).toBe('Solanamobile Seeker');
  });
  it('is never empty and never carries control characters', () => {
    expect(phoneModelName('android', {})).toBe('Android phone');
    expect(phoneModelName('android', undefined)).toBe('Android phone');
    expect(phoneModelName('android', {Model: 'X1\u0000\n'})).toBe('X1');
    expect(phoneModelName('android', {Model: 'M'.repeat(90)}).length).toBe(64);
  });
  it('names an iPhone or iPad, which iOS does not tell an app more about', () => {
    expect(phoneModelName('ios', {}, false)).toBe('iPhone');
    expect(phoneModelName('ios', {}, true)).toBe('iPad');
  });
});
