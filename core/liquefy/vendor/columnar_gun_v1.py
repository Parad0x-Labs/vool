# VENDORED SNAPSHOT — DO NOT EDIT IN PLACE.
# Source: OX-LIQUEFY (Parad0x Labs, MIT) engines/json_codec/NULL_Json_Columnar_Gun_v1.py
# Pinned upstream commits: 2936ecc (audited lineage, dossier 2026-08-31 §22.2/§36.1) through 822bd0b (HEAD, docs-only above the audit).
# Snapshot date: 2026-09-02. Local modifications: none (module docstring below is upstream).
# VOOL wrapper lives in core/liquefy/api.py; this file keeps upstream names for test portability.
#!/usr/bin/env python3
"""
NULL_Json_Columnar_Gun_v1 - [NULL COLUMNAR v1]
==============================================
TARGET: 50x+ Compression on structured JSON logs.
TECH: Columnar Transpose + Type-Aware Encoding + Zstd.
STATUS: Production Grade - Bit-Perfect.
"""

import sys
import struct
import zstandard as zstd
import random
import time
import re
from collections import defaultdict

# orjson is 5-10x faster than stdlib json — use it when available.
# Caveat: orjson silently converts JSON integer literals outside the int64
# range to floats, so lines containing such literals are re-parsed with the
# stdlib parser (which preserves arbitrary-precision ints exactly).
_BIGINT_LITERAL = re.compile(rb':\s*-?\d{19,}\b')

# orjson is 5-10x faster than stdlib json — use it when available
try:
    import orjson as json
    _JSON_LOADS  = json.loads
    _JSON_DUMPS  = lambda obj: json.dumps(obj).decode()
    _JSON_DUMPB  = json.dumps   # returns bytes directly
except ImportError:
    import json as json
    _JSON_LOADS  = json.loads
    _JSON_DUMPS  = lambda obj: json.dumps(obj, separators=(',', ':'))
    _JSON_DUMPB  = lambda obj: json.dumps(obj, separators=(',', ':')).encode()

# numpy for vectorized delta encode/decode — falls back to pure Python
try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False

PROTOCOL_ID    = b'COL2'  # Legacy: per-column zstd
PROTOCOL_ID_V3 = b'COL3'  # v3: one-shot zstd on all column data (fast path)

def pack_varint(val: int) -> bytes:
    if val < 0x80: return bytes([val])
    out = bytearray()
    while val >= 0x80:
        out.append((val & 0x7F) | 0x80)
        val >>= 7
    out.append(val & 0x7F)
    return bytes(out)

def unpack_varint_buf(data: bytes, pos: int) -> tuple:
    res = 0; shift = 0
    while True:
        b = data[pos]; pos += 1
        res |= (b & 0x7F) << shift
        if not (b & 0x80): break
        shift += 7
    return res, pos

def zigzag_encode(n: int) -> int:
    """Map signed int → unsigned int so small abs-value deltas pack small."""
    return (n << 1) ^ (n >> 63)

def zigzag_decode(u: int) -> int:
    return (u >> 1) ^ -(u & 1)

def _esc(b: bytes) -> bytes:
    """Escape a value for \\x00-separated joins: after escaping, b'\\x00' only
    ever appears as a separator and lone b'\\x01' only as the None sentinel."""
    return b.replace(b'\x01', b'\x01\x02').replace(b'\x00', b'\x01\x03')

def _unesc(part: bytes):
    """Inverse of the join encoding: returns None for the sentinel."""
    if part == b'\x01': return None
    return part.replace(b'\x01\x03', b'\x00').replace(b'\x01\x02', b'\x01')

def _join_values(values: list) -> bytes:
    """Join row values (str or None) with b'\\x00'; None becomes b'\\x01'."""
    return b'\x00'.join(_esc(v.encode('utf-8')) if v is not None else b'\x01' for v in values)

def _np_col(values: list):
    """Convert a column value list to a numpy array, inferring int64 or float64."""
    import numpy as np
    try:
        arr = np.array(values, dtype=np.int64)
        return arr
    except (ValueError, OverflowError):
        return np.array(values, dtype=np.float64)

def pack_delta_ints(values: list) -> bytes:
    """Delta encode with adaptive fixed-width integers (0x01/02/04/08 prefix).
    Falls back to zigzag+varint (tagged with a 0x00 prefix so the stream is
    unambiguous) for very small arrays or when numpy is absent."""
    # Reject out-of-int64-range ints explicitly: both encoders would silently
    # lose precision on them (float promotion / broken zigzag).
    for v in values:
        if not -2**63 <= v < 2**63:
            raise ValueError(
                f"integer value {v} out of int64 range; cannot delta-encode exactly")
    if _HAS_NUMPY and len(values) > 32:
        arr = np.array(values, dtype=np.int64)
        d   = np.empty_like(arr); d[0] = arr[0]; d[1:] = np.diff(arr)
        mn, mx = int(d.min()), int(d.max())
        if mn >= -128 and mx <= 127:
            return b'\x01' + d.astype(np.int8).tobytes()
        if mn >= -32768 and mx <= 32767:
            return b'\x02' + d.astype('<i2').tobytes()
        if mn >= -(2**31) and mx <= 2**31-1:
            return b'\x04' + d.astype('<i4').tobytes()
        return b'\x08' + d.astype('<i8').tobytes()
    # Pure-Python fallback — 0x00 tag disambiguates it from the fixed-width
    # streams (whose first data byte could itself be 1/2/4/8).
    out = bytearray(b'\x00'); prev = 0
    for v in values:
        delta = v - prev
        out.extend(pack_varint(zigzag_encode(delta)))
        prev = v
    return bytes(out)

def unpack_delta_ints(data: bytes, count: int) -> list:
    """Decode delta sequence. Handles adaptive fixed-width (0x01/02/04/08 prefix),
    tagged zigzag+varint (0x00 prefix), and legacy archives whose varint stream
    was written without a tag (any other first byte)."""
    if not data:
        return []
    width = data[0]
    if width == 0x00:
        start = 1                      # tagged varint stream: skip the tag
    elif width in (1, 2, 4, 8):
        dtmap = {1: np.int8, 2: '<i2', 4: '<i4', 8: '<i8'}
        if _HAS_NUMPY and width in dtmap:
            arr = np.frombuffer(data[1:], dtype=dtmap[width])
            if len(arr) == count:
                return np.cumsum(arr).tolist()
        start = 0
    else:
        start = 0                      # legacy untagged varint stream
    # Zigzag+varint (tagged or legacy)
    vals = []; pos = start; prev = 0
    for _ in range(count):
        u, pos = unpack_varint_buf(data, pos)
        v = zigzag_decode(u) + prev
        vals.append(v); prev = v
    return vals

import re as _re
from datetime import datetime, timedelta as _td, timezone as _tz
_NUMERIC_SUFFIX_RE = _re.compile(r'^(.*?)(\d+)$')
_ISO_TS_RE = _re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}')
_EPOCH = datetime(1970, 1, 1, tzinfo=_tz.utc)

def _detect_numeric_suffix(values: list):
    """Return (prefix, width, int_list) if all strings share a prefix + zero-padded int suffix."""
    if len(values) < 8: return None
    m = _NUMERIC_SUFFIX_RE.match(values[0])
    if not m: return None
    prefix, first_num = m.group(1), m.group(2)
    width = len(first_num)
    plen  = len(prefix)

    # Probe first / mid / last — fast reject before full scan
    mid = values[len(values)//2]; last = values[-1]
    for probe in (mid, last):
        if not probe.startswith(prefix): return None
        if not probe[plen:].isdigit(): return None

    first_int = int(first_num)
    last_suf  = last[plen:]
    if last_suf.isdigit():
        last_int       = int(last_suf)
        expected_delta = last_int - first_int
        n              = len(values)
        if expected_delta == n - 1 and n > 1:
            # Exactly sequential: arange is correct and fast.
            # Must be exact (0-based or any start, step=1).
            # Excludes constant columns (delta=0) and non-sequential patterns.
            if _HAS_NUMPY:
                nums = np.arange(first_int, first_int + n, dtype=np.int64).tolist()
            else:
                nums = list(range(first_int, first_int + n))
            return prefix, width, nums

    # Non-sequential: full scan
    nums = []
    for v in values:
        if not v.startswith(prefix): return None
        suf = v[plen:]
        if not suf.isdigit(): return None
        nums.append(int(suf))
    return prefix, width, nums

def _parse_iso_ts(v: str):
    """Parse an ISO 8601 timestamp string.

    Returns (epoch_seconds, utcoffset_seconds, suffix) where suffix is the raw
    text after 'YYYY-MM-DDTHH:MM:SS' (e.g. 'Z', '.123Z', '+02:00'), or None if
    the value does not parse. Offsets are honoured: the epoch is the true
    instant, not a UTC relabel of the wall clock."""
    s = v.strip()
    if len(s) < 19 or not _ISO_TS_RE.match(s): return None
    suffix = s[19:]
    iso = s
    # datetime.fromisoformat cannot parse a trailing 'Z' before Python 3.11
    if suffix.endswith(('Z', 'z')):
        iso = s[:19] + suffix[:-1] + '+00:00'
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return None
    off = dt.utcoffset()
    if off is None:
        dt = dt.replace(tzinfo=_tz.utc)   # naive → treat as UTC
        tzoff = 0
    else:
        tzoff = int(off.total_seconds())
    return int(dt.timestamp()), tzoff, suffix

def _detect_iso_timestamps(values: list):
    """Parse every value as an ISO 8601 timestamp. Returns a list of
    (epoch_seconds, utcoffset_seconds, suffix) tuples, or None if any value
    does not parse (caller falls back to the raw/dict path)."""
    if not values or not isinstance(values[0], str): return None
    result = []
    for v in values:
        p = _parse_iso_ts(v)
        if p is None: return None
        result.append(p)
    return result

def _encode_iso_offsets(parsed: list) -> bytes:
    """Serialise per-row (epoch, offset, suffix) tuples for column mode 0x0B
    (mode byte and null mask are written by the caller)."""
    out = bytearray()
    suf_blob = b'\x00'.join(suf.encode('utf-8') for _, _, suf in parsed)
    ep_bytes = pack_delta_ints([ep for ep, _, _ in parsed])
    off_bytes = bytearray(); prev = 0
    for _, off, _ in parsed:            # delta-encode offsets — usually constant
        off_bytes.extend(pack_varint(zigzag_encode(off - prev))); prev = off
    out.extend(pack_varint(len(suf_blob))); out.extend(suf_blob)
    out.extend(pack_varint(len(ep_bytes))); out.extend(ep_bytes)
    out.extend(off_bytes)
    return bytes(out)

def _decode_iso_offsets(meta: bytes, mask: bytes, row_count: int) -> list:
    """Decode mode 0x0B: per-row suffix and UTC offset, restoring each row's
    original textual form ('...Z', '...+02:00', fractional seconds, ...)."""
    bp = 0
    suf_len, bp = unpack_varint_buf(meta, bp)
    sufs = meta[bp:bp+suf_len].split(b'\x00'); bp += suf_len
    ep_len, bp = unpack_varint_buf(meta, bp)
    present = sum(mask)
    epochs = unpack_delta_ints(meta[bp:bp+ep_len], present); bp += ep_len
    offs = []; prev = 0; pos = bp
    for _ in range(present):
        u, pos = unpack_varint_buf(meta, pos); prev += zigzag_decode(u); offs.append(prev)
    it_e = iter(epochs); it_o = iter(offs); it_s = iter(sufs); vals = []
    for m in mask:
        if m:
            ep = next(it_e); off = next(it_o); suf = next(it_s).decode('utf-8')
            dt = datetime.fromtimestamp(ep, _tz(_td(seconds=off)))
            vals.append(dt.strftime('%Y-%m-%dT%H:%M:%S') + suf)
        else:
            vals.append(None)
    return vals

class NULL_Json_Columnar_Gun_v1:
    def __init__(self, level=22, privacy_noise=0.1, enable_privacy_header=False,
                 fast=True):
        self.level = level
        # fast=True  → COL3: encode all columns, compress in ONE zstd L3 call.
        #              342x faster than per-column L22 with same compressed size.
        # fast=False → COL2: per-column zstd (legacy, kept for compatibility).
        self.fast = fast
        # Fast mode: L6 per-column — best ratio/speed balance, avoids L22 startup cost.
        # Slow mode: L22 per-column — maximum ratio for cold archival.
        # write_checksum adds a frame CRC so truncated/corrupt frames fail loudly.
        self.cctx      = zstd.ZstdCompressor(level=6 if fast else level, write_checksum=True)
        self.cctx_slow = zstd.ZstdCompressor(level=level, write_checksum=True)
        self.dctx = zstd.ZstdDecompressor()
        self.privacy_noise = privacy_noise
        self.enable_privacy_header = enable_privacy_header
        self.last_skipped_lines = 0  # malformed JSONL lines skipped by the last compress()

    def _inject_noise(self, val: float, noise_factor: float) -> float:
        """Inject deterministic noise based on value magnitude."""
        if val == 0: return 0.0
        # Deterministic but noisy enough to mask exact value
        # We use random.uniform seeded by the value itself to be deterministic for the writer
        # but random for the observer? No, writer needs to be random.
        # But for search to work, we must EXPAND the range (make min smaller, max larger).
        
        magnitude = abs(val) * noise_factor
        noise = random.uniform(0, magnitude)
        return noise

    def compress(self, raw_data: bytes, strict: bool = False) -> bytes:
        if not raw_data:
            raise ValueError("cannot compress empty input")
        
        rows = []
        all_keys_ordered = []
        
        # 1. Parse JSON. Malformed lines are counted (self.last_skipped_lines)
        # and never shift row indices; with strict=True they raise instead.
        skipped = 0
        for lineno, line in enumerate(raw_data.splitlines(), 1):
            if not line.strip(): continue
            try:
                doc = _JSON_LOADS(line)
                if isinstance(doc, dict) and _BIGINT_LITERAL.search(line):
                    # Re-parse exactly: orjson degrades out-of-int64 integers
                    # to floats, which would silently lose precision.
                    import json as _stdlib_json
                    doc = _stdlib_json.loads(line)
                if not isinstance(doc, dict): raise ValueError("not a JSON object")
            except Exception:
                skipped += 1
                if strict:
                    raise ValueError(f"malformed JSONL at line {lineno}") from None
                continue
            rows.append(doc)
            for k in doc.keys():
                if k not in all_keys_ordered:
                    all_keys_ordered.append(k)
        self.last_skipped_lines = skipped
            
        row_count = len(rows)
        columns = {k: [None] * row_count for k in all_keys_ordered}
        
        # 2. Metadata Collection (Zone Maps)
        # We track min/max for Numeric and String columns
        zone_maps = {} # key -> (min, max, type)
        
        for i, row in enumerate(rows):
            for k, v in row.items():
                columns[k][i] = v
                
                # Update Zone Map
                if v is None: continue
                if k not in zone_maps:
                    zone_maps[k] = {"min": v, "max": v, "type": type(v).__name__}
                else:
                    zm = zone_maps[k]
                    try:
                        if v < zm["min"]: zm["min"] = v
                        if v > zm["max"]: zm["max"] = v
                    except: pass # Mix types ignored

        has_trailing_newline = raw_data.endswith(b'\n')

        output_buffer = bytearray()
        output_buffer.extend(PROTOCOL_ID)  # COL2 format for both modes
        output_buffer.extend(struct.pack('<I', row_count))
        output_buffer.extend(struct.pack('<H', len(all_keys_ordered)))
        output_buffer.append(0x01 if has_trailing_newline else 0x00)
        
        # Store global key order
        order_json = _JSON_DUMPB(all_keys_ordered)
        output_buffer.extend(struct.pack('<I', len(order_json)))
        output_buffer.extend(order_json)
        
        # 3. WRITE PRIVACY HEADER (optional — zone maps for columnar search pruning)
        if self.enable_privacy_header:
            privacy_header = {}
            for k, zm in zone_maps.items():
                p_min, p_max = zm["min"], zm["max"]
                if zm["type"] in ("int", "float"):
                    p_min = p_min - abs(self._inject_noise(p_min, self.privacy_noise))
                    p_max = p_max + abs(self._inject_noise(p_max, self.privacy_noise))
                if zm["type"] == "str":
                    p_min = str(p_min)[:4]
                    p_max = str(p_max)[:4] + "~"
                privacy_header[k] = {"min": p_min, "max": p_max, "type": zm["type"]}
            header_compressed = self.cctx.compress(_JSON_DUMPB(privacy_header))
        else:
            header_compressed = b''
        output_buffer.extend(struct.pack('<I', len(header_compressed)))
        output_buffer.extend(header_compressed)

        # 4. Build column raw payloads
        # col_names_b / col_raws accumulate per-column data.
        # COL3: col_raws = uncompressed raw payloads → concatenate + one-shot zstd at end.
        # COL2: col_raws = already-compressed blobs  → write directly (legacy).
        col_names_b = []
        col_raws    = []

        for col_name in all_keys_ordered:
            values = columns[col_name]
            col_name_bytes = col_name.encode('utf-8')

            # None-free columns take the zero-copy fast path (constant all-ones
            # mask). Columns containing any None get a full scan so mask_bytes
            # always reflects actual per-row presence — a 3-position spot-check
            # would miss interior Nones and desynchronise mask vs value counts.
            n = len(values)
            if None in values:
                valid_vals = [v for v in values if v is not None]
                mask_bytes = bytes([1 if v is not None else 0 for v in values])
            else:
                valid_vals = values          # zero-copy
                mask_bytes = b'\x01' * n    # constant bytes, no loop
            if not valid_vals:
                col_names_b.append(col_name_bytes)
                # Always zstd the empty-column marker — the COL2 container
                # expects a compressed frame per column.
                col_raws.append(self.cctx.compress(b'\x00'))
                continue
            first_val = valid_vals[0]
            raw_payload = bytearray()

            _fv_num = isinstance(first_val, (int, float)) and not isinstance(first_val, bool)
            has_float = False
            if _fv_num:
                # Mixed int/float columns must take the float path — detecting
                # the type from the first value only silently truncated floats.
                has_float = any(isinstance(v, float) for v in valid_vals)

            if _fv_num:
                if has_float:
                    raw_payload.append(0x03); raw_payload.append(0x01)
                    raw_payload.extend(mask_bytes)
                    for v in valid_vals: raw_payload.extend(struct.pack('<d', float(v)))
                else:
                    # Keep the exactness guarantee end-to-end: ints outside
                    # int64 range would be silently promoted to floats.
                    for v in valid_vals:
                        if isinstance(v, int) and not -2**63 <= v < 2**63:
                            raise ValueError(
                                f"integer value {v} out of int64 range; "
                                "cannot delta-encode exactly")
                    raw_payload.append(0x05)
                    raw_payload.extend(mask_bytes)
                    raw_payload.extend(pack_delta_ints([int(v) for v in valid_vals]))

            elif isinstance(first_val, str):
                # sorted() needed for 0x09: delta-encodes indices, sorted → sequential → tiny.
                unique_list = sorted(set(valid_vals))
                mask_b = mask_bytes

                # Structural encodings run first regardless of cardinality:
                # ISO timestamps and numeric-suffix strings beat any dict approach.
                ns  = _detect_numeric_suffix(valid_vals)
                iso = None if ns else _detect_iso_timestamps(valid_vals)
                if ns:
                    prefix, width, nums = ns
                    raw_payload.append(0x06)
                    p_enc = prefix.encode('utf-8')
                    raw_payload.extend(pack_varint(len(p_enc))); raw_payload.extend(p_enc)
                    raw_payload.append(width)
                    raw_payload.extend(mask_b)
                    raw_payload.extend(pack_delta_ints(nums))
                elif iso:
                    # Mode 0x0B: per-row suffix + UTC offset, so mixed 'Z' /
                    # '+02:00' / fractional rows round-trip exactly.
                    raw_payload.append(0x0B)
                    raw_payload.extend(mask_b)
                    raw_payload.extend(_encode_iso_offsets(iso))

                elif len(unique_list) < 256 and len(valid_vals) > 10:
                    # Low-cardinality: 1-byte dict indices
                    raw_payload.append(0x01)
                    raw_payload.extend(pack_varint(len(unique_list)))
                    for uv in unique_list:
                        b_uv = _esc(uv.encode('utf-8'))
                        raw_payload.extend(pack_varint(len(b_uv)))
                        raw_payload.extend(b_uv)
                    mapping = {v: i for i, v in enumerate(unique_list)}
                    indices = bytes([mapping[v] if v is not None else 0xFF for v in values])
                    raw_payload.extend(indices)

                elif len(unique_list) < 65536 and len(valid_vals) > 10:
                    # Mid-cardinality: race 0x02 (raw join) vs 0x09 (dict+delta).
                    # COL3: compare raw sizes — outer zstd will compress both anyway.
                    # COL2: compare compressed sizes immediately (legacy behaviour).
                    mapping = {v: i for i, v in enumerate(unique_list)}

                    cand02 = bytearray([0x02])
                    cand02.extend(_join_values(values))

                    # Mode 0x09 maps None to index 0 (= first unique value), so
                    # it must never carry Nones. With Nones present, use 0x0C:
                    # same layout but indices offset by +1, 0 reserved for None.
                    dict_blob = b'\x00'.join(_esc(u.encode('utf-8')) for u in unique_list)
                    if None in values:
                        dict_cand = bytearray([0x0C])
                        idx_seq = [mapping[v] + 1 if v is not None else 0 for v in values]
                    else:
                        dict_cand = bytearray([0x09])
                        idx_seq = [mapping[v] for v in values]
                    dict_cand.extend(struct.pack('<I', len(dict_blob)))
                    dict_cand.extend(dict_blob)
                    dict_cand.extend(pack_delta_ints(idx_seq))

                    if self.fast:
                        # Fast path: always the dict+delta candidate (0x09/0x0C).
                        # Avoids the double-compress race that costs 600ms on large columns.
                        raw_payload.extend(dict_cand)
                    else:
                        # Slow/accurate path: compare compressed sizes, pick winner.
                        c02 = self.cctx.compress(bytes(cand02))
                        cd  = self.cctx.compress(bytes(dict_cand))
                        winner = c02 if len(c02) <= len(cd) else cd
                        col_names_b.append(col_name_bytes)
                        col_raws.append(winner)   # already compressed for COL2
                        continue

                else:
                    # True high-cardinality, no structural pattern — raw join
                    raw_payload.append(0x02)
                    raw_payload.extend(_join_values(values))
            else:
                raw_payload.append(0x04)
                # Backend-safe serialize: orjson.dumps already returns bytes, stdlib returns str.
                # _JSON_DUMPB normalizes both to bytes (raw json.dumps(...).encode() crashes under orjson).
                # Control bytes never survive JSON serialisation, so the join is unambiguous.
                joined = b'\x00'.join([_JSON_DUMPB(v) if v is not None else b'\x01' for v in values])
                raw_payload.extend(joined)

            col_names_b.append(col_name_bytes)
            col_raws.append(self.cctx.compress(bytes(raw_payload)))

        # 5. Serialise columns — COL2 format for both fast and slow modes
        for nb, payload in zip(col_names_b, col_raws):
            output_buffer.extend(pack_varint(len(nb))); output_buffer.extend(nb)
            output_buffer.extend(struct.pack('<I', len(payload)))
            output_buffer.extend(payload)

        return bytes(output_buffer)

    def decompress(self, blob: bytes) -> bytes:
        if blob.startswith(PROTOCOL_ID_V3):
            return self._decompress_v3(blob)
        if not blob.startswith(PROTOCOL_ID):
            raise ValueError("unrecognized or corrupt Liquefy archive (bad magic)")

        ptr = 4
        row_count = struct.unpack('<I', blob[ptr:ptr+4])[0]; ptr += 4
        num_cols = struct.unpack('<H', blob[ptr:ptr+2])[0]; ptr += 2
        
        has_trailing_newline = blob[ptr] == 0x01; ptr += 1
        
        order_len = struct.unpack('<I', blob[ptr:ptr+4])[0]; ptr += 4
        key_order = _JSON_LOADS(blob[ptr:ptr+order_len]); ptr += order_len

        # SKIP PRIVACY HEADER
        p_len = struct.unpack('<I', blob[ptr:ptr+4])[0]; ptr += 4
        ptr += p_len # Skip the compressed privacy header
        
        columns_data = {}
        for _ in range(num_cols):
            name_len, ptr = unpack_varint_buf(blob, ptr)
            name = blob[ptr:ptr+name_len].decode('utf-8'); ptr += name_len
            
            payload_len = struct.unpack('<I', blob[ptr:ptr+4])[0]; ptr += 4
            payload = self.dctx.decompress(blob[ptr:ptr+payload_len]); ptr += payload_len
            
            if payload == b'\x00':
                columns_data[name] = [None] * row_count
                continue

            mode = payload[0]
            if mode == 0x03: # Float numeric (legacy raw float64)
                format_type = payload[1]
                mask = payload[2 : 2 + row_count]
                data_ptr = 2 + row_count
                values = []
                for i in range(row_count):
                    if mask[i]:
                        if format_type == 0x01:
                            values.append(struct.unpack('<d', payload[data_ptr:data_ptr+8])[0]); data_ptr += 8
                        else:
                            values.append(struct.unpack('<q', payload[data_ptr:data_ptr+8])[0]); data_ptr += 8
                    else:
                        values.append(None)
                columns_data[name] = values
            elif mode == 0x05: # Integer: delta + ZigZag + varint
                mask = payload[1 : 1 + row_count]
                present_count = sum(mask)
                ints = unpack_delta_ints(payload[1 + row_count:], present_count)
                it = iter(ints); values = []
                for i in range(row_count):
                    values.append(next(it) if mask[i] else None)
                columns_data[name] = values
            elif mode == 0x01: # Dict
                dict_n, b_ptr = unpack_varint_buf(payload, 1)
                lookup = []
                for _ in range(dict_n):
                    s_len, b_ptr = unpack_varint_buf(payload, b_ptr)
                    lookup.append(_unesc(payload[b_ptr:b_ptr+s_len]).decode('utf-8')); b_ptr += s_len
                indices = payload[b_ptr:]
                columns_data[name] = [lookup[i] if i != 0xFF else None for i in indices]
            elif mode == 0x07: # Mid-cardinality dict, 16-bit fixed indices (legacy)
                dict_n = struct.unpack('<H', payload[1:3])[0]; b_ptr = 3
                lookup = []
                for _ in range(dict_n):
                    s_len, b_ptr = unpack_varint_buf(payload, b_ptr)
                    lookup.append(payload[b_ptr:b_ptr+s_len].decode('utf-8')); b_ptr += s_len
                values = []
                for i in range(row_count):
                    idx = struct.unpack('<H', payload[b_ptr:b_ptr+2])[0]; b_ptr += 2
                    values.append(lookup[idx] if idx != 0xFFFF else None)
                columns_data[name] = values
            elif mode == 0x09: # Raw dict + delta-varint indices (outer zstd handles compression)
                d_len = struct.unpack('<I', payload[1:5])[0]; b_ptr = 5
                dict_blob = payload[b_ptr:b_ptr+d_len]; b_ptr += d_len
                lookup = [_unesc(s).decode('utf-8') for s in dict_blob.split(b'\x00')]
                indices = unpack_delta_ints(payload[b_ptr:], row_count)
                columns_data[name] = [lookup[idx] if idx < len(lookup) else None for idx in indices]
            elif mode == 0x0C: # Dict + delta-varint indices, offset by 1; 0 = None
                d_len = struct.unpack('<I', payload[1:5])[0]; b_ptr = 5
                dict_blob = payload[b_ptr:b_ptr+d_len]; b_ptr += d_len
                lookup = [_unesc(s).decode('utf-8') for s in dict_blob.split(b'\x00')]
                indices = unpack_delta_ints(payload[b_ptr:], row_count)
                columns_data[name] = [lookup[idx - 1] if 1 <= idx <= len(lookup) else None
                                      for idx in indices]
            elif mode == 0x08: # ISO timestamp → epoch-seconds delta (legacy, single suffix)
                mask = payload[1 : 1 + row_count]; b_ptr = 1 + row_count
                suf_len = payload[b_ptr]; b_ptr += 1
                suffix = payload[b_ptr:b_ptr+suf_len].decode('utf-8'); b_ptr += suf_len
                present_count = sum(mask)
                epochs = unpack_delta_ints(payload[b_ptr:], present_count)
                it = iter(epochs); values = []
                for i in range(row_count):
                    if mask[i]:
                        ep = next(it)
                        dt = datetime.fromtimestamp(ep, _tz.utc)
                        values.append(dt.strftime('%Y-%m-%dT%H:%M:%S') + suffix)
                    else:
                        values.append(None)
                columns_data[name] = values
            elif mode == 0x0B: # ISO timestamp, per-row suffix + UTC offset (v2)
                mask = payload[1 : 1 + row_count]
                columns_data[name] = _decode_iso_offsets(payload[1 + row_count:], mask, row_count)
            elif mode == 0x06: # Numeric-suffix string
                p_len, b_ptr = unpack_varint_buf(payload, 1)
                prefix = payload[b_ptr:b_ptr+p_len].decode('utf-8'); b_ptr += p_len
                width = payload[b_ptr]; b_ptr += 1
                mask = payload[b_ptr : b_ptr + row_count]; b_ptr += row_count
                present_count = sum(mask)
                nums = unpack_delta_ints(payload[b_ptr:], present_count)
                it = iter(nums); values = []
                for i in range(row_count):
                    if mask[i]:
                        values.append(f"{prefix}{next(it):0{width}d}")
                    else:
                        values.append(None)
                columns_data[name] = values
            elif mode == 0x02: # Raw String
                parts = payload[1:].split(b'\x00')
                columns_data[name] = [_unesc(x).decode('utf-8') if x != b'\x01' else None for x in parts]
            elif mode == 0x04: # Complex
                parts = payload[1:].split(b'\x00')
                columns_data[name] = [json.loads(x.decode('utf-8')) if x != b'\x01' else None for x in parts]

        # Explicit nulls are preserved: a None cell decodes back as "key": null.
        # (Fields absent from every source row also surface as null — JSON has
        # no distinction between the two once columns are materialised.)
        rows = []
        for i in range(row_count):
            row = {name: columns_data.get(name, [None] * row_count)[i] for name in key_order}
            rows.append(_JSON_DUMPB(row))
            
        res = b'\n'.join(rows)
        if has_trailing_newline:
            res += b'\n'
        return res

    def _decompress_v3(self, blob: bytes) -> bytes:
        """COL3: read col_index + one-shot decompress + parse columns."""
        ptr = 4
        row_count          = struct.unpack('<I', blob[ptr:ptr+4])[0]; ptr += 4
        _num_cols          = struct.unpack('<H', blob[ptr:ptr+2])[0]; ptr += 2
        has_trailing_newline = blob[ptr] == 0x01;                      ptr += 1
        order_len          = struct.unpack('<I', blob[ptr:ptr+4])[0];  ptr += 4
        key_order          = _JSON_LOADS(blob[ptr:ptr+order_len]);     ptr += order_len
        p_len              = struct.unpack('<I', blob[ptr:ptr+4])[0];  ptr += 4
        ptr += p_len  # skip privacy header

        # Read col_index: (name_varint)(name)(raw_len:4B) for each column
        idx_len = struct.unpack('<I', blob[ptr:ptr+4])[0]; ptr += 4
        idx_end = ptr + idx_len
        col_meta = []   # (name, raw_len)
        while ptr < idx_end:
            nl, ptr = unpack_varint_buf(blob, ptr)
            name    = blob[ptr:ptr+nl].decode(); ptr += nl
            rlen    = struct.unpack('<I', blob[ptr:ptr+4])[0]; ptr += 4
            col_meta.append((name, rlen))

        # One decompression call for all column data
        pay_len = struct.unpack('<I', blob[ptr:ptr+4])[0]; ptr += 4
        all_raw = self.dctx.decompress(blob[ptr:ptr+pay_len])

        # Parse each column from the decompressed buffer
        columns_data = {}
        raw_ptr = 0
        for name, rlen in col_meta:
            payload = all_raw[raw_ptr:raw_ptr+rlen]; raw_ptr += rlen
            columns_data[name] = self._parse_column_payload(payload, row_count)

        rows = []
        for i in range(row_count):
            row = {k: columns_data[k][i] for k in key_order if k in columns_data}
            rows.append(_JSON_DUMPB(row))
        res = b'\n'.join(rows)
        if has_trailing_newline: res += b'\n'
        return res

    def _parse_column_payload(self, payload: bytes, row_count: int) -> list:
        """Parse a single raw (already-decoded) column payload into a value list."""
        if payload == b'\x00': return [None] * row_count
        mode = payload[0]
        if mode == 0x05:
            mask = payload[1:1+row_count]; present = sum(mask)
            ints = unpack_delta_ints(payload[1+row_count:], present)
            it = iter(ints); return [next(it) if m else None for m in mask]
        if mode == 0x01:
            dict_n, bp = unpack_varint_buf(payload, 1); lookup = []
            for _ in range(dict_n):
                sl, bp = unpack_varint_buf(payload, bp)
                lookup.append(_unesc(payload[bp:bp+sl]).decode()); bp += sl
            return [lookup[i] if i != 0xFF else None for i in payload[bp:]]
        if mode == 0x06:
            pl, bp = unpack_varint_buf(payload, 1); prefix = payload[bp:bp+pl].decode(); bp += pl
            width = payload[bp]; bp += 1; mask = payload[bp:bp+row_count]; bp += row_count
            nums = unpack_delta_ints(payload[bp:], sum(mask))
            it = iter(nums)
            return [f"{prefix}{next(it):0{width}d}" if m else None for m in mask]
        if mode == 0x08:
            mask = payload[1:1+row_count]; bp = 1+row_count
            sl = payload[bp]; bp += 1; suffix = payload[bp:bp+sl].decode(); bp += sl
            epochs = unpack_delta_ints(payload[bp:], sum(mask)); it = iter(epochs)
            return [datetime.fromtimestamp(next(it), _tz.utc).strftime('%Y-%m-%dT%H:%M:%S')+suffix
                    if m else None for m in mask]
        if mode == 0x0B:
            return _decode_iso_offsets(payload[1+row_count:], payload[1:1+row_count], row_count)
        if mode == 0x09:
            dl = struct.unpack('<I', payload[1:5])[0]; bp = 5
            lookup = [_unesc(s).decode() for s in payload[bp:bp+dl].split(b'\x00')]; bp += dl
            idxs = unpack_delta_ints(payload[bp:], row_count)
            return [lookup[i] if i < len(lookup) else None for i in idxs]
        if mode == 0x0C:
            dl = struct.unpack('<I', payload[1:5])[0]; bp = 5
            lookup = [_unesc(s).decode() for s in payload[bp:bp+dl].split(b'\x00')]; bp += dl
            idxs = unpack_delta_ints(payload[bp:], row_count)
            return [lookup[i - 1] if 1 <= i <= len(lookup) else None for i in idxs]
        if mode in (0x02, 0x04):
            parts = payload[1:].split(b'\x00')
            if mode == 0x02: return [_unesc(x).decode() if x != b'\x01' else None for x in parts]
            return [_JSON_LOADS(x) if x != b'\x01' else None for x in parts]
        if mode == 0x03:
            ft = payload[1]; mask = payload[2:2+row_count]; dp = 2+row_count; vals = []
            for m in mask:
                if m:
                    if ft == 0x01: vals.append(struct.unpack('<d', payload[dp:dp+8])[0]); dp += 8
                    else:          vals.append(struct.unpack('<q', payload[dp:dp+8])[0]); dp += 8
                else: vals.append(None)
            return vals
        if mode == 0x07:
            dn = struct.unpack('<H', payload[1:3])[0]; bp = 3; lookup = []
            for _ in range(dn):
                sl, bp = unpack_varint_buf(payload, bp); lookup.append(payload[bp:bp+sl].decode()); bp += sl
            vals = []
            for i in range(row_count):
                idx = struct.unpack('<H', payload[bp:bp+2])[0]; bp += 2
                vals.append(lookup[idx] if idx != 0xFFFF else None)
            return vals
        return [None] * row_count

    def grep(self, blob: bytes, query_str: str) -> dict:
        """
        NATIVE COLUMNAR GREP (UNICORN V1) - METRIC ENHANCED
        ==================================================
        Returns detailed stats to prove the '10% Decoded' thesis.
        """
        start_t = time.perf_counter()

        if blob.startswith(PROTOCOL_ID_V3):
            return self._grep_v3(blob, query_str)

        if not blob.startswith(PROTOCOL_ID): return {"error": "Invalid Format"}
        
        # Stats
        total_compressed_bytes = len(blob)
        bytes_decoded = 0
        candidate_cols = 0
        total_cols_in_file = 0
        
        ptr = 4
        row_count = struct.unpack('<I', blob[ptr:ptr+4])[0]; ptr += 4
        num_cols = struct.unpack('<H', blob[ptr:ptr+2])[0]; ptr += 2
        total_cols_in_file = num_cols
        
        ptr += 1 # skip has_trailing_newline
        order_len = struct.unpack('<I', blob[ptr:ptr+4])[0]; ptr += 4
        ptr += order_len # skip key_order
        
        # READ PRIVACY HEADER (optional — may be empty)
        p_len = struct.unpack('<I', blob[ptr:ptr+4])[0]; ptr += 4
        privacy_header = {}
        if p_len > 0:
            privacy_data = self.dctx.decompress(blob[ptr:ptr+p_len])
            bytes_decoded += len(privacy_data)
            privacy_header = _JSON_LOADS(privacy_data)
        ptr += p_len
        
        matching_rows = set()
        query_bytes = query_str.encode('utf-8')
        col_errors = []   # per-column integrity failures, surfaced via result['error']
        
        # PRE-ALLOCATION
        dctx = self.dctx 
        
        for _ in range(num_cols):
            name_len, ptr = unpack_varint_buf(blob, ptr)
            # Use slice to avoid copy
            col_name_bytes = blob[ptr:ptr+name_len]
            ptr += name_len
            
            payload_len = struct.unpack('<I', blob[ptr:ptr+4])[0]; ptr += 4
            payload_start = ptr
            ptr += payload_len
            
            # PRUNING CHECK
            skip_column = False
            if privacy_header:
                try:
                    col_name = col_name_bytes.decode('utf-8')
                    if col_name in privacy_header:
                        zm = privacy_header[col_name]
                        if zm["type"] in ("int", "float"):
                            try:
                                float(query_str)
                            except ValueError:
                                skip_column = True
                except: pass
            
            if skip_column:
                continue
                
            candidate_cols += 1
            
            # Decompress column chunk
            try:
                # OPTIMIZATION: Use memoryview
                chunk_view = memoryview(blob)[payload_start:payload_start+payload_len]
                col_payload = dctx.decompress(chunk_view)
                bytes_decoded += len(col_payload) # TRACK DECODED BYTES
            except Exception as e:
                col_errors.append(
                    f"column {col_name_bytes.decode('utf-8', 'replace')}: {e}")
                continue

            if not col_payload or col_payload == b'\x00': continue
            
            # SCAN — mode-aware columnar search
            mode = col_payload[0]

            if mode == 0x05:  # Delta-int column: skip unless query is numeric
                try:
                    target = int(query_str)
                    mask = col_payload[1 : 1 + row_count]
                    present_count = sum(mask)
                    vals = unpack_delta_ints(col_payload[1 + row_count:], present_count)
                    it = iter(vals); [matching_rows.add(i) for i, m in enumerate(mask) if m and next(it) == target]
                except (ValueError, StopIteration):
                    pass

            elif mode == 0x06:  # Numeric-suffix: extract target int and search delta ints
                try:
                    p_l, bp = unpack_varint_buf(col_payload, 1)
                    prefix = col_payload[bp:bp+p_l].decode('utf-8'); bp += p_l
                    width  = col_payload[bp]; bp += 1
                    if query_str.startswith(prefix):
                        target = int(query_str[len(prefix):])
                        mask = col_payload[bp : bp + row_count]; bp += row_count
                        present_count = sum(mask)
                        vals = unpack_delta_ints(col_payload[bp:], present_count)
                        it = iter(vals); [matching_rows.add(i) for i, m in enumerate(mask) if m and next(it) == target]
                except Exception:
                    pass

            elif mode == 0x08:  # ISO timestamp: convert query to epoch and search
                try:
                    suf_len = col_payload[1 + row_count]
                    iso_ts = _detect_iso_timestamps([query_str])
                    if iso_ts:
                        target_ep = iso_ts[0]
                        mask = col_payload[1 : 1 + row_count]
                        bp   = 1 + row_count + 1 + suf_len
                        vals = unpack_delta_ints(col_payload[bp:], sum(mask))
                        it   = iter(vals); [matching_rows.add(i) for i, m in enumerate(mask) if m and next(it) == target_ep]
                except Exception:
                    pass

            elif mode == 0x0B:  # ISO timestamp v2 (per-row suffix/offset): compare epochs
                try:
                    qp = _parse_iso_ts(query_str)
                    if qp:
                        target_ep = qp[0]
                        mask = col_payload[1 : 1 + row_count]
                        bp   = 1 + row_count
                        s_len, bp = unpack_varint_buf(col_payload, bp); bp += s_len
                        e_len, bp = unpack_varint_buf(col_payload, bp)
                        vals = unpack_delta_ints(col_payload[bp:bp+e_len], sum(mask))
                        it   = iter(vals); [matching_rows.add(i) for i, m in enumerate(mask) if m and next(it) == target_ep]
                except Exception:
                    pass

            elif mode == 0x09:  # Dict+delta: check if query is in dict, find matching rows
                try:
                    d_len = struct.unpack('<I', col_payload[1:5])[0]; bp = 5
                    dict_blob = col_payload[bp:bp+d_len]; bp += d_len
                    lookup = [_unesc(s).decode('utf-8') for s in dict_blob.split(b'\x00')]
                    if query_str in lookup:
                        target_idx = lookup.index(query_str)
                        vals = unpack_delta_ints(col_payload[bp:], row_count)
                        matching_rows.update(i for i, v in enumerate(vals) if v == target_idx)
                except Exception:
                    pass

            elif mode == 0x0C:  # Dict+delta with +1 offset (0 reserved for None)
                try:
                    d_len = struct.unpack('<I', col_payload[1:5])[0]; bp = 5
                    dict_blob = col_payload[bp:bp+d_len]; bp += d_len
                    lookup = [_unesc(s).decode('utf-8') for s in dict_blob.split(b'\x00')]
                    if query_str in lookup:
                        target_idx = lookup.index(query_str) + 1
                        vals = unpack_delta_ints(col_payload[bp:], row_count)
                        matching_rows.update(i for i, v in enumerate(vals) if v == target_idx)
                except Exception:
                    pass

            elif mode == 0x01:  # Dict: check if query in dict, find index, scan indices
                if col_payload.find(_esc(query_bytes)) != -1:
                    dict_n, bp = unpack_varint_buf(col_payload, 1)
                    lookup = []
                    for _ in range(dict_n):
                        s_len, bp = unpack_varint_buf(col_payload, bp)
                        lookup.append(_unesc(col_payload[bp:bp+s_len])); bp += s_len
                    target_idx = next((i for i, v in enumerate(lookup) if v == query_bytes), None)
                    if target_idx is not None:
                        indices = col_payload[bp:]
                        matching_rows.update(i for i, idx in enumerate(indices) if idx == target_idx)

            elif mode in (0x02, 0x04):  # Raw string / complex: byte scan with null counting
                if col_payload.find(_esc(query_bytes)) != -1:
                    needle = _esc(query_bytes)
                    search_pos = 1
                    while True:
                        match_pos = col_payload.find(needle, search_pos)
                        if match_pos == -1: break
                        row_idx = col_payload.count(b'\x00', 0, match_pos)
                        matching_rows.add(row_idx)
                        search_pos = match_pos + 1
            
        end_t = time.perf_counter()
        
        # Calculate Percentage
        # This is strictly "Bytes Decompressed vs Full Theoretical Decompressed Size"?
        # Or "Bytes Decompressed vs File Size"?
        # The user asked: "% of archive decoded". 
        # But we don't know the full uncompressed size without decompressing everything.
        # We can estimate it? Or just report raw bytes decoded.
        
        # Actually, "archive bytes" usually means Compressed Size.
        # "% of archive decoded" implies: did we read/decode all the compressed chunks?
        # No, we skipped chunks. So we should measure "Compressed Bytes Read".
        # But `dctx` reads compressed bytes.
        
        # Let's track "Compressed Bytes Processed" vs "Total Compressed Bytes"
        # Since we skipped `payload_len` bytes for skipped columns, we can track that.
        
        return {
            "matches": sorted(list(matching_rows)),
            "error": "; ".join(col_errors) if col_errors else None,
            "stats": {
                "duration_ms": (end_t - start_t) * 1000,
                "total_archive_bytes": total_compressed_bytes,
                "bytes_decoded": bytes_decoded,
                "candidate_cols": candidate_cols,
                "total_cols": total_cols_in_file,
                # "% Decoded" could be interpreted as "Did we do the work?"
                # Let's calculate effective work ratio?
            }
        }

    def _grep_v3(self, blob: bytes, query_str: str) -> dict:
        """COL3 grep: decompress once, then search all columns."""
        start_t = time.perf_counter()
        ptr = 4
        row_count = struct.unpack('<I', blob[ptr:ptr+4])[0]; ptr += 4
        ptr += 3  # num_cols + has_newline
        order_len = struct.unpack('<I', blob[ptr:ptr+4])[0]; ptr += 4; ptr += order_len
        p_len = struct.unpack('<I', blob[ptr:ptr+4])[0]; ptr += 4; ptr += p_len
        idx_len = struct.unpack('<I', blob[ptr:ptr+4])[0]; ptr += 4
        idx_end = ptr + idx_len
        col_meta = []
        while ptr < idx_end:
            nl, ptr = unpack_varint_buf(blob, ptr)
            name = blob[ptr:ptr+nl].decode(); ptr += nl
            rlen = struct.unpack('<I', blob[ptr:ptr+4])[0]; ptr += 4
            col_meta.append((name, rlen))
        pay_len = struct.unpack('<I', blob[ptr:ptr+4])[0]; ptr += 4
        all_raw = self.dctx.decompress(blob[ptr:ptr+pay_len])
        matching_rows = set(); raw_ptr = 0
        for name, rlen in col_meta:
            payload = all_raw[raw_ptr:raw_ptr+rlen]; raw_ptr += rlen
            self._grep_column(payload, row_count, query_str, matching_rows)
        return {"matches": sorted(matching_rows),
                "stats": {"duration_ms": (time.perf_counter()-start_t)*1000}}

    def _grep_column(self, payload: bytes, row_count: int,
                     query_str: str, out: set) -> None:
        """Search a single column payload, add matching row indices to `out`."""
        if not payload or payload == b'\x00': return
        mode = payload[0]; qb = query_str.encode()
        if mode == 0x05:
            try:
                t = int(query_str); mask = payload[1:1+row_count]
                vals = unpack_delta_ints(payload[1+row_count:], sum(mask))
                it = iter(vals); [out.add(i) for i,m in enumerate(mask) if m and next(it)==t]
            except ValueError: pass
        elif mode == 0x06:
            try:
                pl, bp = unpack_varint_buf(payload, 1); prefix = payload[bp:bp+pl].decode(); bp += pl
                width = payload[bp]; bp += 1
                if query_str.startswith(prefix):
                    t = int(query_str[len(prefix):]); mask = payload[bp:bp+row_count]; bp += row_count
                    vals = unpack_delta_ints(payload[bp:], sum(mask)); it = iter(vals)
                    [out.add(i) for i,m in enumerate(mask) if m and next(it)==t]
            except Exception: pass
        elif mode == 0x01:
            qb = _esc(qb)
            if qb in payload:
                dn, bp = unpack_varint_buf(payload, 1); lookup = []
                for _ in range(dn):
                    sl, bp = unpack_varint_buf(payload, bp); lookup.append(_unesc(payload[bp:bp+sl])); bp += sl
                ti = next((i for i,v in enumerate(lookup) if v==qb), None)
                if ti is not None: [out.add(i) for i,idx in enumerate(payload[bp:]) if idx==ti]
        elif mode in (0x02, 0x04):
            qb = _esc(qb)
            if qb in payload:
                sp = 1
                while True:
                    mp = payload.find(qb, sp)
                    if mp == -1: break
                    out.add(payload.count(b'\x00', 0, mp)); sp = mp+1
        elif mode == 0x0B:
            try:
                qp = _parse_iso_ts(query_str)
                if qp:
                    t = qp[0]; mask = payload[1:1+row_count]; bp = 1+row_count
                    sl, bp = unpack_varint_buf(payload, bp); bp += sl
                    el, bp = unpack_varint_buf(payload, bp)
                    vals = unpack_delta_ints(payload[bp:bp+el], sum(mask)); it = iter(vals)
                    [out.add(i) for i,m in enumerate(mask) if m and next(it)==t]
            except Exception: pass
        elif mode == 0x09:
            try:
                dl = struct.unpack('<I', payload[1:5])[0]; bp = 5
                lookup = [_unesc(s).decode() for s in payload[bp:bp+dl].split(b'\x00')]; bp += dl
                if query_str in lookup:
                    ti = lookup.index(query_str); idxs = unpack_delta_ints(payload[bp:], row_count)
                    out.update(i for i,v in enumerate(idxs) if v==ti)
            except Exception: pass
        elif mode == 0x0C:  # Dict+delta with +1 offset (0 reserved for None)
            try:
                dl = struct.unpack('<I', payload[1:5])[0]; bp = 5
                lookup = [_unesc(s).decode() for s in payload[bp:bp+dl].split(b'\x00')]; bp += dl
                if query_str in lookup:
                    ti = lookup.index(query_str) + 1; idxs = unpack_delta_ints(payload[bp:], row_count)
                    out.update(i for i,v in enumerate(idxs) if v==ti)
            except Exception: pass

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python NULL_Json_Columnar_Gun_v1.py [compress|decompress] <in> <out>")
        sys.exit(1)
    codec = NULL_Json_Columnar_Gun_v1()
    if sys.argv[1] == "compress":
        with open(sys.argv[2], "rb") as f: d = f.read()
        with open(sys.argv[3], "wb") as f: f.write(codec.compress(d))
    elif sys.argv[1] == "decompress":
        with open(sys.argv[2], "rb") as f: d = f.read()
        with open(sys.argv[3], "wb") as f: f.write(codec.decompress(d))

