# Test receipt: phone private capture, 17 November 2026

The first real transfers from VOOL's private camera on a phone (Solana Seeker, Android 16) to the paired
test VOOL on a Mac. Phone builds: fb72865c (first three files), 17a59a0e (fourth) and 4e702e59 (fifth).
Computer builds: a101f4c7 (first four) and 4e702e59 (fifth). Times are UTC.

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
| 5 | Photo, 9000×12000 (108 MP pixel count, full-sensor mode), made smaller on the phone (HEIC) | VOOL_20261117_111104_29872aed.heic | 2,383,332 | 29872aed801c96571c22b3475b5e1f0e2dcde7be898e975047bc9c06fad5f268 | 29872aed801c96571c22b3475b5e1f0e2dcde7be898e975047bc9c06fad5f268 | capture.saved, seq 50, 09:11:28.874, after flush and read-back | capture.removed_on_phone, seq 51, 09:11:29.075, key destroyed: yes |
| 6 | Photo, 9000×12000, compression on (HEIC) | VOOL_20261117_111632_3947bce0.heic | 1,085,605 | 3947bce0e817d12d62a02510a111af5d0a5c18478e4b71f92a3b3ae80beb5555 | 3947bce0e817d12d62a02510a111af5d0a5c18478e4b71f92a3b3ae80beb5555 | capture.saved, seq 52, 09:16:42.961 | capture.removed_on_phone, seq 53, key destroyed: yes |
| 7 | Photo, 9000×12000, compression off (JPEG), same scene as 6 | VOOL_20261117_111653_3f6f9527.jpg | 10,772,733 | 3f6f95278eb41e33065575fe9c42ecf20620dba9ba0abab2fd9ca438ed348146 | 3f6f95278eb41e33065575fe9c42ecf20620dba9ba0abab2fd9ca438ed348146 | capture.saved, seq 54, 09:17:53.702 | capture.removed_on_phone, seq 55, key destroyed: yes |

No partly received files were left on the computer after the seven transfers. The receipt log held 55 rows
after transfer 7, and every row carries the hash of the row before it, without a break.

For transfer 5, the phone's removal time (09:11:28.562 by the phone's clock) is a little earlier than the
computer's save time (09:11:28.874 by the Mac's clock). The two clocks are not synchronised to the
millisecond. The order is fixed by the log itself: `capture.saved` is seq 50 and `capture.removed_on_phone`
is seq 51, and the phone only removes a capture after the computer has answered "done" with the same hash.

## What transfer 5 closes

Transfers 1 to 4 left two gaps. Transfer 5 (phone and computer both on 4e702e59) closes both.

1. **The file was durably stored when the phone heard "done".** Builds up to a101f4c7 confirmed after
   the move into place, without asking the disk to flush. From 4e702e59, `capture.finish` flushes the
   file and its folder to disk and reads the stored file back. "done" is answered only when the hash read
   back from disk matches. Transfer 5 was confirmed this way.
2. **The phone's copy is gone.** The older phone builds deleted their copy only after the computer
   answered with the same hash, but they did not tell the computer. The computer holds no record of
   those deletions, so rows 1 to 4 say "Not recorded". From 4e702e59:
   - each capture's file key is sealed by its own Android Keystore key;
   - on removal, that Keystore key is destroyed first (crypto-erasure), the sealed key in the file is
     overwritten, and then the files are deleted;
   - the phone then sends `capture.removed` (hash, whether the key was destroyed, time), and the computer
     adds a `capture.removed_on_phone` receipt to the same hash-chained log.

   Row 5 carries that receipt with "key destroyed: yes". Whatever flash memory keeps of that capture can
   no longer be decrypted, not even by VOOL. Files sealed by the older shared key (rows 1 to 4) are still
   read and sent, but erasing them cannot destroy a per-file key.

## What this still does not prove

- The deletion receipt is the phone's own statement. The computer cannot inspect the phone's storage; it
  records what the phone reports, in a log it cannot rewrite without breaking the chain.
- **108 MP is the pixel count, not the detail.** 9000×12000 is the full readout of the main sensor, not a
  software upscale: shrinking the photo to 12 MP and back loses 1.12 grey levels (RMS), against 0.56 to
  0.66 for 48 MP and 27 MP, so it holds detail a 12 MP image cannot. Above about 27 MP the difference
  falls to the compression noise, so the real detail is roughly 20 to 27 MP worth. At 100% the photo looks
  soft, which is normal for this kind of high-pixel-count phone sensor.
- **Compression, rows 6 and 7.** The same scene was shot twice, 20 seconds apart, with compression on and
  off. The compressed photo is 10.1% of the original's size (1,085,605 against 10,772,733 bytes), at the
  same 9000×12000. Matched crops of the same keys at 100% show the same lettering and edges with no
  blocking. Sharpness (variance of the Laplacian at 3000 px wide) is 49.8 compressed against 46.5
  original. The shots are not pixel-identical, so this is a visual and statistical comparison, not a
  pixel-difference score. Before keeping a compressed photo, the phone compares it with its own original
  (brightness PSNR at a reduced size) and keeps the original if it scores below 30 dB.
- One photo is one run. Video and sound on 4e702e59 follow the same path but were not re-run here.
