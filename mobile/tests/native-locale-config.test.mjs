import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
const {getResolvedLocalesAsync}=require('@expo/config-plugins/build/ios/Locales.js');
const config=JSON.parse(fs.readFileSync(new URL('../app.json',import.meta.url))).expo;
test('SDK52 resolves all seventeen native permission catalogs',async()=>{
 const values=await getResolvedLocalesAsync(process.cwd(),config.locales);
 assert.equal(Object.keys(values).length,17);
 assert.deepEqual(Object.keys(values).sort(),[...config.ios.infoPlist.CFBundleLocalizations].sort());
 const fields=['NSCameraUsageDescription','NSFaceIDUsageDescription','NSLocationWhenInUseUsageDescription','NSMicrophoneUsageDescription','NSPhotoLibraryUsageDescription'];
 for(const [lang,copy] of Object.entries(values)){
  assert.deepEqual(Object.keys(copy).sort(),fields);
  for(const value of Object.values(copy))assert.ok(typeof value==='string'&&value.trim().length>3,lang);
  assert.ok(copy.NSFaceIDUsageDescription.includes('VOOL'));
  if(lang!=='en')assert.notEqual(copy.NSMicrophoneUsageDescription,values.en.NSMicrophoneUsageDescription,lang);
 }
});
