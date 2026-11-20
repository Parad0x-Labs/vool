# Test receipt: phone private capture, 17 November 2026

The first real transfers from VOOL's private camera on a phone (Solana Seeker, Android 16) to the paired
test VOOL on a Mac. Phone builds: fb72865c (first three files) and 17a59a0e (fourth). Computer build:
a101f4c7. Times are UTC.

## What each column proves

- **Source hash**: the SHA-256 the phone computed from the original bytes while sealing the capture,
  sent in `capture.begin`.
- **Destination hash**: the SHA-256 of the file on the computer's disk, computed again for this receipt
  after the transfer, straight from the stored file.
- **Bytes**: the byte count the phone declared and the size of the stored file. Both agree.
- **Transfer result**: the computer's `capture.saved` receipt. It sits in the hash-chained receipt log
  (each row carries the hash of the row before it), with its sequence number.
- **Deletion confirmation**: whether the computer holds a record that the phone removed its copy.

| # | Kind | Stored as | Bytes | Source hash (phone) | Destination hash (disk) | Transfer result | Deletion confirmation |
|---|------|-----------|-------|---------------------|-------------------------|-----------------|-----------------------|
| 1 | Photo, 3264×2448 | VOOL_20261117_091930_204ad72f.jpg | 1,295,636 | 204ad72fffb65eeb5c13c146e12308de8fc6e702a0330f8f4a55de684e4c3803 | 204ad72fffb65eeb5c13c146e12308de8fc6e702a0330f8f4a55de684e4c3803 | capture.saved, seq 46, 07:19:43 | Not recorded (see below) |
| 2 | Video, 25.7 MB, sent while the phone was locked | VOOL_20261117_092011_e5463975.mp4 | 25,708,750 | e5463975847a7a99d728f2a7d4b903645be400cf5b57b2c3d8490f4ad87e140f | e5463975847a7a99d728f2a7d4b903645be400cf5b57b2c3d8490f4ad87e140f | capture.saved, seq 47, 07:49:34 | Not recorded |
| 3 | Sound | VOOL_20261117_092111_41ad78bb.m4a | 303,605 | 41ad78bb350a456a788c9a1d5a7319a47e1cd9092b58272a71d54d23bd50179b | 41ad78bb350a456a788c9a1d5a7319a47e1cd9092b58272a71d54d23bd50179b | capture.saved, seq 48, 07:49:36 | Not recorded |
| 4 | Photo, 3264×2448 | VOOL_20261117_101808_c74b2249.jpg | 1,314,859 | c74b2249c28505e8d276653af77a1dd5e4064ad948ca9f67478c8c41a3eecb28 | c74b2249c28505e8d276653af77a1dd5e4064ad948ca9f67478c8c41a3eecb28 | capture.saved, seq 49, 08:18:20 | Not recorded |

No partly received files were left on the computer after the four transfers.

## What this does not prove yet, and what closes it

1. **The file was durably stored when the phone heard "done".** Builds up to a101f4c7 confirmed after
   the move into place, without asking the disk to flush. From the next computer build, `capture.finish`
   flushes the file and its folder to disk and reads the stored file back. "done" is answered only when
   the hash read back from disk matches.
2. **The phone's copy is gone.** The phone builds used here delete their copy only after the computer
   answers with the same hash. They did not tell the computer, so the computer holds no record of the
   deletion. That is why the last column says "Not recorded". From the next phone build:
   - each capture's file key is sealed by its own Android Keystore key;
   - on removal, that Keystore key is destroyed first (crypto-erasure), the sealed key in the file is
     overwritten, and then the files are deleted;
   - the phone then sends `capture.removed` (hash, whether the key was destroyed, time), and the computer
     adds a `capture.removed_on_phone` receipt to the same hash-chained log.

   With that, whatever flash memory keeps of a deleted capture can no longer be decrypted, not even by
   VOOL. Files sealed by the older shared key (the four above) are still read and sent, but erasing them
   cannot destroy a per-file key.

The next real-phone run will repeat this table with the deletion column filled from
`capture.removed_on_phone` receipts.
