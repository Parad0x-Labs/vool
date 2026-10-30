import {HoldRecording, type RecordingTake} from '../../src/security/holdRecording';
const deferred = () => {let resolve!: (v: boolean) => void;const promise = new Promise<boolean>(r => {resolve=r;});return {promise,resolve};};
function setup(allow=async()=>true, prepare=async()=>{}) {
 const transcript=jest.fn(async(_bytes:Uint8Array)=>"draft words"), failed=jest.fn(), changed=jest.fn(), draft=jest.fn();
 const take:RecordingTake={prepare:jest.fn(prepare),start:jest.fn(async()=>{}),finish:jest.fn(async()=>new Uint8Array([1,2])),discard:jest.fn(async()=>{})};
 return {take,transcript,failed,changed,draft,recording:new HoldRecording(allow,()=>take,transcript,draft,changed,failed)};
}
test('release during permission cannot open the microphone later',async()=>{const d=deferred();const s=setup(()=>d.promise);const pending=s.recording.begin();await s.recording.end();d.resolve(true);await pending;expect(s.take.prepare).not.toHaveBeenCalled();expect(s.transcript).not.toHaveBeenCalled();});
test('release during preparation discards without starting or uploading',async()=>{const d=deferred();const s=setup(async()=>true,async()=>{await d.promise;});const p=s.recording.begin();await Promise.resolve();await s.recording.end();d.resolve(true);await p;expect(s.take.start).not.toHaveBeenCalled();expect(s.take.discard).toHaveBeenCalledTimes(1);expect(s.transcript).not.toHaveBeenCalled();});
test('background discards an active take and sends nothing',async()=>{const s=setup();await s.recording.begin();await s.recording.end(true);expect(s.take.discard).toHaveBeenCalledTimes(1);expect(s.transcript).not.toHaveBeenCalled();});
test('one held take produces one draft and no repeated upload on release',async()=>{const s=setup();await s.recording.begin();await s.recording.end();await s.recording.end();expect(s.transcript).toHaveBeenCalledTimes(1);expect(s.take.finish).toHaveBeenCalledTimes(1);});
test('PC permission refusal never prepares a recorder',async()=>{const s=setup(async()=>false);await s.recording.begin();expect(s.take.prepare).not.toHaveBeenCalled();});

test('a late transcript after background never enters the composer',async()=>{const d=deferred();const s=setup();s.transcript.mockImplementation(async()=>{await d.promise;return 'late private words';});await s.recording.begin();const ended=s.recording.end();await Promise.resolve();await s.recording.end(true);d.resolve(true);await ended;expect(s.draft).not.toHaveBeenCalled();});
