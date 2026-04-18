#!/usr/bin/env python3
"""Virtual MP4 Proxy v3 - full moof parsing for correct sample tables.

Scans all moof boxes to get per-sample sizes/durations from trun atoms,
then builds a correct progressive MP4 moov with real stts/stsz/stco/stss.
"""
import sys, os, json, struct, threading, time, socket, subprocess
from concurrent.futures import ThreadPoolExecutor
import requests as req_lib
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

DEFAULT_TV_IP = '192.168.1.103'
PORT = 9234

# Import shared TV discovery
sys.path.insert(0, str(Path(__file__).parent))
from tv_discovery import resolve_tv_ip, get_tv_client
CDN_H = {
    'Referer': 'https://www.bilibili.com/',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}

def make_box(t, d):
    s = 8 + len(d)
    if s > 0xFFFFFFFF:
        return struct.pack('>I', 1) + t.encode() + struct.pack('>Q', s + 8) + d
    return struct.pack('>I', s) + t.encode() + d

def make_full_box(t, ver, flags, d):
    return make_box(t, struct.pack('>I', (ver<<24)|flags) + d)

def parse_boxes(data, off=0, end=None):
    if end is None: end = len(data)
    r = []
    p = off
    while p < end - 8:
        s = struct.unpack('>I', data[p:p+4])[0]
        t = data[p+4:p+8].decode('ascii', errors='replace')
        if s <= 0: break
        r.append((t, p, s))
        p += s
    return r

# ──────────────────────────────────────────────────────────────────────
# CDN mirror pool — rotates through multiple B站 CDN hosts, each capped at
# a per-connection rate limit by Bilibili. By rewriting the host portion
# of the URL we can access mirrors that the playurl API never returns for
# overseas users, and by parallelising large Range requests across mirrors
# we saturate multi-100 Mbps even when a single connection is throttled.
# ──────────────────────────────────────────────────────────────────────

# Hardcoded fallback mirror hosts discovered via probe (2026-04). These
# do not appear in the playurl API response for US clients but work when
# the URL path is reused. Ordered by typical measured throughput; the
# MirrorPool probes and re-ranks at init so order here is just a starting
# hint. Unreachable ones drop out automatically.
_EXTRA_MIRROR_HOSTS = [
    'cn-hk-eq-01-02.bilivideo.com',     # HK edge, typically fastest from US west
    'upos-sz-mirrorali.bilivideo.com',  # Aliyun 国内
    'upos-sz-mirrorhw.bilivideo.com',   # Huawei Cloud
    'upos-sz-mirror08c.bilivideo.com',  # unknown provider, often fast
    'upos-sz-mirrorbd.bilivideo.com',   # Baidu Cloud
    'upos-sz-mirrorcos.bilivideo.com',  # Tencent Cloud 国内
    'upos-sz-mirroraliov.bilivideo.com',# Aliyun 海外
]


class MirrorPool:
    """Manages a list of equivalent CDN URLs for the same media stream.

    All mirrors share the same URL path; only the host differs. On init
    we probe each host with a small parallel Range request and keep the
    ones that respond, sorted by throughput. Fetches use the fastest
    available; if a mirror fails mid-stream we rotate to the next.

    Large reads (> 1.5 MB) are automatically sharded across up to 4
    connections in parallel, each to a different mirror when possible,
    to bypass per-connection throttling.
    """
    PROBE_BYTES = 2 * 1024 * 1024  # 2 MB probe
    PROBE_TIMEOUT = 5.0
    # B站 CDN throttles each TCP connection after ~3-5 MB sustained
    # transfer, so we want chunks *smaller* than that (not bigger) and
    # fresh TCP connections for each. Together with high fan-out this
    # saturates the pipe instead of tripping the throttle.
    PARALLEL_THRESHOLD = 800_000   # start parallelising above ~800 KB
    PARALLEL_MAX_CHUNKS = 8        # up to 8 concurrent connections
    PARALLEL_TARGET_CHUNK = 1_500_000  # ~1.5 MB per chunk — below throttle
    PARALLEL_MIN_CHUNK = 256 * 1024

    def __init__(self, primary_url: str, backup_urls: list[str] | None = None,
                 label: str = 'stream'):
        self._label = label
        # Collect all candidate hosts: primary + backups + hardcoded extras
        seen_hosts = set()
        candidates = []  # list[(host, full_url)]
        for u in [primary_url] + list(backup_urls or []):
            if not u: continue
            h = u.split('/')[2]
            if h not in seen_hosts:
                seen_hosts.add(h); candidates.append((h, u))
        # Build the shared path from primary
        path = '/' + '/'.join(primary_url.split('/')[3:])
        for h in _EXTRA_MIRROR_HOSTS:
            if h not in seen_hosts:
                seen_hosts.add(h); candidates.append((h, f'https://{h}{path}'))

        self._candidates = candidates
        self._mirrors: list[tuple[str, str, float]] = []  # (host, url, mbps)
        self._probed = False
        self._lock = threading.Lock()

    def probe(self):
        """Measure each candidate in parallel and keep reachable ones sorted by speed."""
        if self._probed:
            return
        def _probe_one(item):
            host, url = item
            try:
                t0 = time.time()
                rr = req_lib.get(url, headers={**CDN_H,
                    'Range': f'bytes=2097152-{2097152 + self.PROBE_BYTES - 1}'},
                    stream=True, timeout=self.PROBE_TIMEOUT)
                if rr.status_code not in (200, 206):
                    return (host, url, 0.0)
                n = 0
                for chunk in rr.iter_content(65536):
                    n += len(chunk)
                    if time.time() - t0 > self.PROBE_TIMEOUT:
                        break
                dt = time.time() - t0
                mbps = (n * 8 / dt / 1e6) if dt > 0 else 0
                return (host, url, mbps)
            except Exception:
                return (host, url, 0.0)

        with ThreadPoolExecutor(max_workers=min(len(self._candidates), 12)) as ex:
            results = list(ex.map(_probe_one, self._candidates))

        alive = sorted([r for r in results if r[2] > 0], key=lambda r: -r[2])
        self._mirrors = alive
        self._probed = True
        print(f'  [{self._label}] Mirror pool ({len(alive)}/{len(self._candidates)} alive):',
              flush=True)
        for host, _, mbps in alive[:6]:
            print(f'    ★ {host[:50]:50} {mbps:6.1f} Mbps', flush=True)
        if not alive:
            # Fall back: at least keep the primary so fetches don't crash
            self._mirrors = [(c[0], c[1], 0.0) for c in self._candidates[:1]]

    @property
    def urls(self) -> list[str]:
        return [m[1] for m in self._mirrors]

    @property
    def primary_url(self) -> str:
        return self._mirrors[0][1] if self._mirrors else ''

    def fetch(self, start: int, end: int, retries: int = 3) -> bytes:
        """Fetch a Range [start, end] inclusive. Auto-parallelises large chunks.

        Uses a work-stealing queue pattern: chunks are dumped into a shared
        queue and worker threads (one per top-tier mirror) pull the next
        available chunk. Fast mirrors naturally service more chunks; slow
        or stalled mirrors only service what they can. A single slow mirror
        cannot hold up the whole request — at worst it finishes its last
        chunk after the others and we wait on that tail.

        We cap the mirror fan-out to the top N by measured speed; adding
        slow mirrors past this just wastes thread slots.
        """
        if not self._probed:
            self.probe()
        length = end - start + 1

        # Small request: single connection, fastest mirror
        if length < self.PARALLEL_THRESHOLD or len(self._mirrors) < 2:
            return self._fetch_single(start, end, retries)

        # Pick workers: top N mirrors, where N = PARALLEL_MAX_CHUNKS
        active_mirrors = [m[1] for m in self._mirrors[:self.PARALLEL_MAX_CHUNKS]]
        n_workers = len(active_mirrors)

        # Chunk sizing: each chunk must stay below the per-connection
        # throttle ceiling (~3 MB), so we derive chunk count from the
        # target chunk size, not from worker count. More chunks than
        # workers is critical — the work-stealing queue feeds each
        # worker multiple small chunks so throttle never kicks in.
        ideal_chunks = max(n_workers,
                           (length + self.PARALLEL_TARGET_CHUNK - 1)
                           // self.PARALLEL_TARGET_CHUNK)
        chunk_size = (length + ideal_chunks - 1) // ideal_chunks
        # If chunk_size drifts above the throttle ceiling, add more chunks
        while chunk_size > self.PARALLEL_TARGET_CHUNK * 1.5:
            ideal_chunks += 1
            chunk_size = (length + ideal_chunks - 1) // ideal_chunks
        if chunk_size < self.PARALLEL_MIN_CHUNK:
            ideal_chunks = max(1, length // self.PARALLEL_MIN_CHUNK)
            chunk_size = (length + ideal_chunks - 1) // ideal_chunks

        ranges = []
        for i in range(ideal_chunks):
            cs = start + i * chunk_size
            ce = min(end, cs + chunk_size - 1)
            if cs > end: break
            ranges.append((cs, ce))

        results: list[bytes | None] = [None] * len(ranges)

        # Work-stealing queue: atomic counter into the ranges list
        import queue
        q = queue.Queue()
        for i in range(len(ranges)):
            q.put(i)

        def _worker(my_url):
            while True:
                try:
                    idx = q.get_nowait()
                except queue.Empty:
                    return
                s, e = ranges[idx]
                for attempt in range(retries):
                    try:
                        r = req_lib.get(my_url, headers={**CDN_H,
                                                          'Range': f'bytes={s}-{e}',
                                                          'Connection': 'close'},
                                        timeout=30)
                        if r.status_code in (200, 206):
                            results[idx] = r.content
                            break
                    except Exception:
                        pass
                    if attempt < retries - 1:
                        time.sleep(0.2 * (attempt + 1))
                else:
                    # All attempts failed on this mirror — fall back serial
                    try:
                        results[idx] = self._fetch_single(s, e, retries=2)
                    except Exception:
                        results[idx] = b''

        with ThreadPoolExecutor(max_workers=n_workers) as ex:
            futs = [ex.submit(_worker, active_mirrors[i]) for i in range(n_workers)]
            for f in futs:
                f.result()

        return b''.join(r or b'' for r in results)

    def _fetch_single(self, start: int, end: int, retries: int = 3) -> bytes:
        """Serial fetch with mirror rotation on failure."""
        urls = self.urls or [self._candidates[0][1]]
        last_exc = None
        for attempt in range(retries):
            url = urls[attempt % len(urls)]
            try:
                r = req_lib.get(url, headers={**CDN_H, 'Range': f'bytes={start}-{end}'}, timeout=30)
                if r.status_code in (200, 206):
                    return r.content
                last_exc = RuntimeError(f'HTTP {r.status_code} from {url.split("/")[2]}')
            except Exception as e:
                last_exc = e
            if attempt < retries - 1:
                time.sleep(0.3 * (attempt + 1))
        raise last_exc or RuntimeError('all mirrors failed')


def cdn_fetch(url_or_pool, start, end, retries=3):
    """Back-compat wrapper: accepts either a URL string or a MirrorPool."""
    if isinstance(url_or_pool, MirrorPool):
        return url_or_pool.fetch(start, end, retries=retries)
    # Legacy string path
    for attempt in range(retries):
        try:
            return req_lib.get(url_or_pool, headers={**CDN_H, 'Range': f'bytes={start}-{end}'}, timeout=30).content
        except Exception:
            if attempt < retries - 1:
                time.sleep(0.5 * (attempt + 1))
            else:
                raise

def parse_sidx(data, off, size):
    d = data[off:off+size]; v = d[8]
    if v == 0:
        ts = struct.unpack('>I', d[16:20])[0]; _, rc = struct.unpack('>HH', d[28:32]); rs = 32
    else:
        ts = struct.unpack('>I', d[16:20])[0]; _, rc = struct.unpack('>HH', d[36:40]); rs = 40
    refs = []
    for i in range(rc):
        o = rs+i*12
        ref_sz = struct.unpack('>I', d[o:o+4])[0] & 0x7FFFFFFF
        dur = struct.unpack('>I', d[o+4:o+8])[0]
        sap = (struct.unpack('>I', d[o+8:o+12])[0] >> 31) & 1
        refs.append((ref_sz, dur, sap))
    return ts, refs

def extract_stsd(moov_data):
    for bt,bo,bs in parse_boxes(moov_data, 8):
        if bt=='trak':
            for bt2,bo2,bs2 in parse_boxes(moov_data, bo+8, bo+bs):
                if bt2=='mdia':
                    for bt3,bo3,bs3 in parse_boxes(moov_data, bo2+8, bo2+bs2):
                        if bt3=='minf':
                            for bt4,bo4,bs4 in parse_boxes(moov_data, bo3+8, bo3+bs3):
                                if bt4=='stbl':
                                    for bt5,bo5,bs5 in parse_boxes(moov_data, bo4+8, bo4+bs4):
                                        if bt5=='stsd':
                                            return _normalize_hevc_stsd(moov_data[bo5:bo5+bs5])
    return make_full_box('stsd', 0, 0, struct.pack('>I', 0))


def _normalize_hevc_stsd(stsd_bytes: bytes) -> bytes:
    """Rewrite `hev1` HEVC sample entry tags to `hvc1`.

    LG webOS (and many TVs) accept only `hvc1`-tagged HEVC in progressive
    MP4 — `hev1` streams are rejected with "this file cannot be recognized".
    The difference is whether parameter sets (VPS/SPS/PPS) are required to
    live in the hvcC box (hvc1) or may appear inline in mdat (hev1). B站
    streams put them in the hvcC regardless, so a byte-level tag swap is
    safe and preserves the rest of the box intact.

    stsd layout:
      [4 size][4 'stsd'][4 ver+flags][4 entry_count]
        [4 entry_size][4 codec_tag][6 reserved][2 data_ref_index]
        [... visual sample entry fields + hvcC ...]

    We scan entries and, for any whose codec_tag is 'hev1', rewrite it to
    'hvc1'. No other bytes change; box sizes stay the same.
    """
    buf = bytearray(stsd_bytes)
    # Skip stsd header: 8 (size+type) + 4 (version/flags) + 4 (entry_count)
    if len(buf) < 16:
        return bytes(buf)
    entry_count = struct.unpack('>I', bytes(buf[12:16]))[0]
    p = 16
    for _ in range(entry_count):
        if p + 8 > len(buf):
            break
        esize = struct.unpack('>I', bytes(buf[p:p+4]))[0]
        etag = bytes(buf[p+4:p+8])
        if etag == b'hev1':
            buf[p+4:p+8] = b'hvc1'
        p += esize if esize > 0 else 8
    return bytes(buf)

def parse_trun(data, default_duration=0, default_size=0, default_flags=0):
    """Parse trun box, return list of (sample_size, sample_duration, is_sync)."""
    ver = data[8]
    flags = struct.unpack('>I', b'\x00' + data[9:12])[0]
    sample_count = struct.unpack('>I', data[12:16])[0]
    
    off = 16
    if flags & 0x1:  # data_offset
        off += 4
    first_sample_flags = None
    if flags & 0x4:  # first_sample_flags
        first_sample_flags = struct.unpack('>I', data[off:off+4])[0]
        off += 4
    
    has_duration = bool(flags & 0x100)
    has_size = bool(flags & 0x200)
    has_flags = bool(flags & 0x400)
    has_cts = bool(flags & 0x800)
    
    samples = []
    for i in range(sample_count):
        dur = struct.unpack('>I', data[off:off+4])[0] if has_duration else default_duration
        if has_duration: off += 4
        sz = struct.unpack('>I', data[off:off+4])[0] if has_size else default_size
        if has_size: off += 4
        sf = struct.unpack('>I', data[off:off+4])[0] if has_flags else (first_sample_flags if i == 0 and first_sample_flags is not None else default_flags)
        if has_flags: off += 4
        if has_cts: off += 4
        
        # is_sync: bit 16 of sample_flags (0x00010000 = non-sync)
        is_sync = not (sf & 0x00010000)
        samples.append((sz, dur, is_sync))
    
    return samples

def parse_tfhd(data):
    """Parse tfhd, return dict of defaults."""
    flags = struct.unpack('>I', b'\x00' + data[9:12])[0]
    off = 16  # skip version+flags(4) + track_id(4)
    result = {}
    if flags & 0x01: off += 8  # base_data_offset
    if flags & 0x02: off += 4  # sample_description_index
    if flags & 0x08:
        result['default_sample_duration'] = struct.unpack('>I', data[off:off+4])[0]; off += 4
    if flags & 0x10:
        result['default_sample_size'] = struct.unpack('>I', data[off:off+4])[0]; off += 4
    if flags & 0x20:
        result['default_sample_flags'] = struct.unpack('>I', data[off:off+4])[0]; off += 4
    return result


class TrackInfo:
    def __init__(self, url, handler_type, workers=20):
        self.workers = workers
        self.url = url
        self.handler_type = handler_type
        self.timescale = 0
        self.refs = []
        self.data_start = 0
        self.stsd = b''
        # Per-sample info (from all moof/trun parsing)
        self.samples = []  # [(size, duration, is_sync)]
        # Per-segment: (cdn_mdat_body_offset, mdat_body_size)
        self.seg_mdat = []
    
    def initialize(self):
        print(f'  Fetching {self.handler_type} header...', flush=True)
        header = cdn_fetch(self.url, 0, 20479)
        
        for bt, bo, bs in parse_boxes(header):
            if bt == 'moov':
                self.stsd = extract_stsd(header[bo:bo+bs])
            elif bt == 'sidx':
                self.timescale, self.refs = parse_sidx(header, bo, bs)
            elif bt == 'moof':
                self.data_start = bo
                break
        
        print(f'  {self.handler_type}: {len(self.refs)} segs, ts={self.timescale}', flush=True)
        self._scan_moofs()
    
    def _scan_moofs(self):
        """Fetch and parse all moof boxes using parallel 4KB requests."""
        seg_offsets = []
        pos = self.data_start
        for ref_sz, _, _ in self.refs:
            seg_offsets.append(pos)
            pos += ref_sz
        
        n = len(seg_offsets)
        print(f'  Scanning {n} moofs (parallel)...', flush=True)
        
        moof_data = [None] * n
        t0 = time.time()
        done = [0]  # mutable counter
        lock = threading.Lock()
        
        # For moof scan we use direct per-worker URLs (many small requests,
        # pool overhead not worth it). Distribute workers across the top
        # mirrors so no single host is saturated by 4KB request spam.
        if isinstance(self.url, MirrorPool):
            mirror_urls = self.url.urls or [self.url.primary_url]
        else:
            mirror_urls = [self.url]

        def fetch_worker(indices, worker_idx):
            """Each worker pins to one mirror, reuses its Session."""
            my_url = mirror_urls[worker_idx % len(mirror_urls)]
            sess = req_lib.Session()
            sess.headers.update(CDN_H)
            for i in indices:
                off = seg_offsets[i]
                for attempt in range(5):
                    try:
                        resp = sess.get(my_url,
                            headers={'Range': f'bytes={off}-{off+4095}'}, timeout=30)
                        chunk = resp.content
                        moof_sz = struct.unpack('>I', chunk[0:4])[0]
                        if moof_sz > len(chunk):
                            resp2 = sess.get(my_url,
                                headers={'Range': f'bytes={off}-{off+moof_sz-1}'}, timeout=30)
                            chunk = resp2.content
                        moof_data[i] = (chunk[:moof_sz], moof_sz)
                        break
                    except Exception:
                        if attempt < 4:
                            time.sleep(0.2 * (attempt + 1))
                            try: sess.close()
                            except: pass
                            # On retry, rotate to next mirror
                            my_url = mirror_urls[(worker_idx + attempt + 1) % len(mirror_urls)]
                            sess = req_lib.Session()
                            sess.headers.update(CDN_H)
                        else:
                            raise
                with lock:
                    done[0] += 1
                    if done[0] % 100 == 0:
                        print(f'    {done[0]}/{n} ({time.time()-t0:.1f}s)', flush=True)
            sess.close()
        
        # Split work into contiguous chunks across configurable worker threads
        NUM_WORKERS = max(1, int(self.workers))
        chunk_size = (n + NUM_WORKERS - 1) // NUM_WORKERS
        chunks = [list(range(i*chunk_size, min((i+1)*chunk_size, n))) for i in range(NUM_WORKERS)]
        chunks = [c for c in chunks if c]
        threads = [threading.Thread(target=fetch_worker, args=(c, i)) for i, c in enumerate(chunks)]
        for t in threads: t.start()
        for t in threads: t.join()
        
        elapsed = time.time() - t0
        print(f'  Scanned {n} moofs in {elapsed:.1f}s ({n // max(1,int(elapsed))} moofs/s)', flush=True)
        
        for i in range(n):
            if moof_data[i] is None:
                raise RuntimeError(f'Failed to fetch moof {i}')
        
        # Parse each moof to extract trun samples
        total_samples = 0
        self.samples_per_seg = []  # number of samples in each segment
        for i, (mdata, moof_sz) in enumerate(moof_data):
            ref_sz = self.refs[i][0]
            mdat_body_off = seg_offsets[i] + moof_sz + 8
            mdat_body_sz = ref_sz - moof_sz - 8
            self.seg_mdat.append((mdat_body_off, mdat_body_sz))
            
            seg_samples = 0
            for bt, bo, bs in parse_boxes(mdata, 8, moof_sz):
                if bt == 'traf':
                    # First find tfhd defaults
                    dd = ds = df = 0
                    for bt2, bo2, bs2 in parse_boxes(mdata, bo+8, bo+bs):
                        if bt2 == 'tfhd':
                            defs = parse_tfhd(mdata[bo2:bo2+bs2])
                            dd = defs.get('default_sample_duration', 0)
                            ds = defs.get('default_sample_size', 0)
                            df = defs.get('default_sample_flags', 0)
                            break
                    for bt2, bo2, bs2 in parse_boxes(mdata, bo+8, bo+bs):
                        if bt2 == 'trun':
                            samples = parse_trun(mdata[bo2:bo2+bs2], dd, ds, df)
                            self.samples.extend(samples)
                            total_samples += len(samples)
                            seg_samples += len(samples)
            self.samples_per_seg.append(seg_samples)
        
        total_body = sum(s[1] for s in self.seg_mdat)
        print(f'  {self.handler_type}: {total_samples} samples, {total_body//1024//1024}MB', flush=True)


class VirtualMP4:
    def __init__(self, vid_url, aud_url, duration, workers=20):
        self.vid = TrackInfo(vid_url, 'vide', workers=workers)
        self.aud = TrackInfo(aud_url, 'soun', workers=workers)
        self.workers = workers
        self.duration = duration
        self.ftyp = b''
        self.moov = b''
        self.mdat_header = b''
        self.header_blob = b''
        self.total_size = 0
        # Chunk map: each segment's mdat body is one chunk
        # (virtual_offset, length, track_char, seg_index)
        self.chunks = []
    
    def initialize(self):
        print('Initializing...', flush=True)
        t0 = time.time()
        
        # Parallel init of both tracks
        vt = threading.Thread(target=self.vid.initialize)
        at = threading.Thread(target=self.aud.initialize)
        vt.start(); at.start()
        vt.join(); at.join()
        
        # ftyp
        hdr = cdn_fetch(self.vid.url, 0, 4095)
        self.ftyp = hdr[0:parse_boxes(hdr)[0][2]]
        
        self._build_moov()
        
        # mdat: interleave video and audio segment mdat bodies
        total_mdat = sum(s[1] for s in self.vid.seg_mdat) + sum(s[1] for s in self.aud.seg_mdat)
        
        if total_mdat + 8 > 0xFFFFFFFF:
            self.mdat_header = struct.pack('>I', 1) + b'mdat' + struct.pack('>Q', total_mdat + 16)
        else:
            self.mdat_header = struct.pack('>I', total_mdat + 8) + b'mdat'
        
        vp = len(self.ftyp) + len(self.moov) + len(self.mdat_header)
        mn = min(len(self.vid.seg_mdat), len(self.aud.seg_mdat))
        
        for i in range(mn):
            vo, vl = self.vid.seg_mdat[i]
            ao, al = self.aud.seg_mdat[i]
            self.chunks.append((vp, vl, 'v', i)); vp += vl
            self.chunks.append((vp, al, 'a', i)); vp += al
        for i in range(mn, len(self.vid.seg_mdat)):
            vo, vl = self.vid.seg_mdat[i]; self.chunks.append((vp, vl, 'v', i)); vp += vl
        for i in range(mn, len(self.aud.seg_mdat)):
            ao, al = self.aud.seg_mdat[i]; self.chunks.append((vp, al, 'a', i)); vp += al
        
        self.total_size = vp
        self._fix_stco()
        self.header_blob = self.ftyp + self.moov + self.mdat_header
        
        elapsed = time.time() - t0
        print(f'Ready: {self.total_size//1024//1024}MB virtual, '
              f'{len(self.vid.samples)}+{len(self.aud.samples)} samples, '
              f'{elapsed:.1f}s init', flush=True)
    
    def _build_moov(self):
        vts = self.vid.timescale
        dur = int(self.duration * vts)
        mvhd = struct.pack('>I',0) + struct.pack('>II',0,0) + struct.pack('>I',vts)
        mvhd += struct.pack('>I',dur) + struct.pack('>I',0x10000) + struct.pack('>H',0x100)
        mvhd += b'\x00'*10 + struct.pack('>9I',0x10000,0,0,0,0x10000,0,0,0,0x40000000)
        mvhd += b'\x00'*24 + struct.pack('>I',3)
        
        self.moov = make_box('moov',
            make_box('mvhd', mvhd) +
            self._build_trak(1, self.vid) +
            self._build_trak(2, self.aud))
    
    def _build_trak(self, tid, tr):
        ts = tr.timescale; dur = int(self.duration * ts); ht = tr.handler_type
        
        # tkhd
        td = struct.pack('>I',3) + struct.pack('>II',0,0) + struct.pack('>I',tid)
        td += struct.pack('>I',0) + struct.pack('>I',dur) + b'\x00'*8
        td += struct.pack('>HH',0,0)
        td += struct.pack('>H', 0x100 if ht=='soun' else 0) + b'\x00'*2
        td += struct.pack('>9I',0x10000,0,0,0,0x10000,0,0,0,0x40000000)
        td += struct.pack('>II', 1920<<16, 1080<<16) if ht=='vide' else struct.pack('>II',0,0)
        tkhd = make_box('tkhd', td)
        
        md = struct.pack('>I',0) + struct.pack('>II',0,0) + struct.pack('>I',ts)
        md += struct.pack('>I',dur) + struct.pack('>HH',0x55C4,0)
        mdhd = make_box('mdhd', md)
        
        hd = struct.pack('>II',0,0) + ht.encode() + b'\x00'*12
        hd += (b'VideoHandler\x00' if ht=='vide' else b'SoundHandler\x00')
        hdlr = make_box('hdlr', hd)
        
        # stts: run-length encode durations
        entries = b''; n = 0; i = 0; samples = tr.samples
        while i < len(samples):
            d = samples[i][1]; c = 1
            while i+c < len(samples) and samples[i+c][1] == d: c += 1
            entries += struct.pack('>II', c, d); n += 1; i += c
        stts = make_full_box('stts', 0, 0, struct.pack('>I', n) + entries)
        
        # stsz: per-sample sizes
        se = b''
        for sz, _, _ in samples:
            se += struct.pack('>I', sz)
        stsz = make_full_box('stsz', 0, 0, struct.pack('>II', 0, len(samples)) + se)
        
        # stsc: samples per chunk from actual trun sample counts
        stsc_entries = b''; stsc_n = 0; prev_count = -1
        for seg_i, spc in enumerate(tr.samples_per_seg):
            if spc != prev_count:
                stsc_entries += struct.pack('>III', seg_i + 1, spc, 1)
                stsc_n += 1
                prev_count = spc
        stsc = make_full_box('stsc', 0, 0, struct.pack('>I', stsc_n) + stsc_entries)
        
        # co64: 64-bit chunk offsets, one entry per segment (placeholder,
        # fixed later by _fix_stco). We always use co64 instead of stco
        # because B站 4K videos can exceed 4 GB total size, which would
        # overflow stco's 32-bit offsets. Cost is +4 bytes per segment ×
        # 2 tracks (~5 KB on a typical 700-segment video).
        stco = make_full_box('co64', 0, 0,
            struct.pack('>I', len(tr.seg_mdat)) + b'\x00' * (8 * len(tr.seg_mdat)))
        
        # stss: sync samples
        ss = b''; sc = 0; si = 1
        for sz, dur, is_sync in samples:
            if is_sync: ss += struct.pack('>I', si); sc += 1
            si += 1
        stss = make_full_box('stss', 0, 0, struct.pack('>I', sc) + ss)
        
        stbl = make_box('stbl', tr.stsd + stts + stsz + stsc + stco + stss)
        
        dref = make_full_box('dref',0,0,struct.pack('>I',1)+make_full_box('url ',0,1,b''))
        dinf = make_box('dinf', dref)
        xmhd = make_full_box('vmhd',0,1,struct.pack('>H3H',0,0,0,0)) if ht=='vide' else make_full_box('smhd',0,0,struct.pack('>HH',0,0))
        
        minf = make_box('minf', xmhd + dinf + stbl)
        mdia = make_box('mdia', mdhd + hdlr + minf)
        return make_box('trak', tkhd + mdia)
    
    def _fix_stco(self):
        """Patch co64 chunk-offset entries with the real virtual offsets.

        We emit co64 (not stco) so all offsets are 64-bit, supporting
        files >4 GB. Each entry is 8 bytes packed big-endian.
        """
        vo = [c[0] for c in self.chunks if c[2]=='v']
        ao = [c[0] for c in self.chunks if c[2]=='a']
        m = bytearray(self.moov); idx = [0]
        def fix(s, e):
            p = s
            while p < e-8:
                sz = struct.unpack('>I', m[p:p+4])[0]; bt = m[p+4:p+8]
                if sz <= 0: break
                if bt == b'co64':
                    cnt = struct.unpack('>I', m[p+12:p+16])[0]
                    offs = vo if idx[0]==0 else ao
                    for i in range(min(cnt, len(offs))):
                        struct.pack_into('>Q', m, p+16+i*8, offs[i])
                    idx[0] += 1
                elif bt in (b'moov',b'trak',b'mdia',b'minf',b'stbl'):
                    fix(p+8, p+sz)
                p += sz
        fix(8, len(m))
        self.moov = bytes(m)
    
    def find_chunk(self, voff):
        lo, hi = 0, len(self.chunks)-1
        while lo <= hi:
            mid = (lo+hi)//2
            co, cl, _, _ = self.chunks[mid]
            if voff < co: hi = mid-1
            elif voff >= co+cl: lo = mid+1
            else: return self.chunks[mid]
        return None


class Cache:
    """Segment cache with in-flight tracking and background prefetch.

    Two-level state per key:
      - self.d[k]      = bytes  (fully fetched, ready to serve)
      - self.inflight[k] = Future (fetch in progress)

    get() blocks on inflight if needed, otherwise issues a synchronous
    fetch. prefetch() schedules a fetch on a background thread without
    blocking — used to warm the cache for upcoming segments.
    """
    def __init__(self, max_mb=500, prefetch_workers=8):
        self.d: dict = {}
        self.sz = 0
        self.mx = max_mb * 1024 * 1024
        self.lk = threading.Lock()
        self.inflight: dict = {}
        self._executor = ThreadPoolExecutor(max_workers=prefetch_workers,
                                            thread_name_prefix='prefetch')

    def _evict_locked(self, needed: int):
        # LRU-ish: drop oldest insertion order until enough room
        while self.sz + needed > self.mx and self.d:
            ok = next(iter(self.d))
            self.sz -= len(self.d[ok])
            del self.d[ok]

    def _store(self, k, v: bytes):
        with self.lk:
            if k in self.d:
                return
            self._evict_locked(len(v))
            self.d[k] = v
            self.sz += len(v)
            self.inflight.pop(k, None)

    def get(self, k, fn):
        """Synchronous get — blocks until data is ready."""
        with self.lk:
            if k in self.d:
                # Refresh LRU position
                v = self.d.pop(k)
                self.d[k] = v
                return v
            if k in self.inflight:
                fut = self.inflight[k]
            else:
                fut = self._executor.submit(self._wrap_fetch, k, fn)
                self.inflight[k] = fut
        return fut.result()

    def _wrap_fetch(self, k, fn):
        v = fn()
        self._store(k, v)
        return v

    def prefetch(self, k, fn):
        """Fire-and-forget background fetch. Idempotent."""
        with self.lk:
            if k in self.d or k in self.inflight:
                return
            fut = self._executor.submit(self._wrap_fetch, k, fn)
            self.inflight[k] = fut

# Larger cache + prefetch worker pool — ~8 simultaneous segment downloads
# means we always have a few segments warmed ahead of the playhead.
cache = Cache(max_mb=600, prefetch_workers=8)

def _build_yt_cookie_args() -> list[str]:
    """Build yt-dlp --cookies args from bili-cli credential when available."""
    cred_path = Path.home() / '.bilibili-cli' / 'credential.json'
    if not cred_path.exists():
        return []
    try:
        cred = json.loads(cred_path.read_text())
    except Exception:
        return []

    cookie_map = {
        'SESSDATA': cred.get('sessdata') or cred.get('SESSDATA'),
        'bili_jct': cred.get('bili_jct') or cred.get('biliJct'),
        'DedeUserID': cred.get('dedeuserid') or cred.get('DedeUserID'),
        'buvid3': cred.get('buvid3'),
        'buvid4': cred.get('buvid4'),
        'ac_time_value': cred.get('ac_time_value'),
    }
    lines = ['# Netscape HTTP Cookie File', '# Generated by mp4_proxy_v3.py', '']
    count = 0
    for k, v in cookie_map.items():
        if v:
            lines.append(f'.bilibili.com\tTRUE\t/\tTRUE\t2147483647\t{k}\t{v}')
            count += 1
    if count == 0:
        return []

    cookie_file = Path('/tmp/mp4_proxy_bili.cookies.txt')
    cookie_file.write_text('\n'.join(lines) + '\n')
    return ['--cookies', str(cookie_file)]

def _load_bili_cookies() -> dict:
    """Load bili-cli credentials as a requests-style cookie dict."""
    cred_path = Path.home() / '.bilibili-cli' / 'credential.json'
    if not cred_path.exists():
        return {}
    try:
        cred = json.loads(cred_path.read_text())
    except Exception:
        return {}
    out = {}
    for src_key, dst_key in [
        ('sessdata', 'SESSDATA'), ('bili_jct', 'bili_jct'),
        ('dedeuserid', 'DedeUserID'), ('buvid3', 'buvid3'),
        ('buvid4', 'buvid4'), ('ac_time_value', 'ac_time_value'),
    ]:
        v = cred.get(src_key) or cred.get(dst_key)
        if v:
            out[dst_key] = str(v)
    return out


def resolve_urls_from_bilibili(url: str, format_selector: str = "30080+30280") -> dict:
    """Resolve Bilibili URL → fastest CDN URLs via direct playurl API.

    Calls B站 playurl API directly to get all mirror URLs, then speed-tests
    them and picks the fastest. Bypasses yt-dlp's URL selection (which only
    returns the first mirror in the API response, not always the fastest).
    """
    # Step 1: resolve to BV id (short link or long URL)
    import re
    if 'b23.tv' in url:
        rr = req_lib.get(url, headers={'User-Agent': CDN_H['User-Agent']}, allow_redirects=True, timeout=15)
        url = rr.url
    m = re.search(r'(BV\w+)', url)
    if not m:
        raise RuntimeError(f"Could not extract BV id from {url}")
    bvid = m.group(1)

    # Step 2: parse format_selector to get qn + codec preference
    # Bilibili format IDs (yt-dlp convention):
    #   30120 = AVC  4K       30121 = HEVC 4K       30125/30126 = HDR/DV variants
    #   30116 = AVC  1080P60  30117 = HEVC 1080P60
    #   30112 = AVC  1080高码  30114 = HEVC 1080高码
    #   30080 = AVC  1080P    30077 = HEVC 1080P
    #   30064 = AVC  720P     30066 = HEVC 720P
    # Same qn can have multiple codecs — _select must match both qn AND codec family.
    _FMT_TO_QN_CODEC = {
        30120: (120, 'avc'),  30121: (120, 'hev'),  30125: (120, 'hev'),  30126: (120, 'hev'),
        30116: (116, 'avc'),  30117: (116, 'hev'),
        30112: (112, 'avc'),  30114: (112, 'hev'),
        30080: (80,  'avc'),  30077: (80,  'hev'),
        30064: (64,  'avc'),  30066: (64,  'hev'),
        30032: (32,  'avc'),  30033: (32,  'hev'),
        30016: (16,  'avc'),  30011: (16,  'hev'),
    }
    parts = format_selector.split('+')
    vid_fmt = int(parts[0]) if parts[0].isdigit() else 30080
    aud_fmt = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 30280
    qn, want_codec = _FMT_TO_QN_CODEC.get(vid_fmt, (vid_fmt - 30000 if vid_fmt > 30000 else vid_fmt, 'avc'))

    cookies = _load_bili_cookies()
    api_h = {'User-Agent': CDN_H['User-Agent'], 'Referer': f'https://www.bilibili.com/video/{bvid}/'}

    # Step 3: get cid + duration + title
    info = req_lib.get(f'https://api.bilibili.com/x/web-interface/view?bvid={bvid}',
                       cookies=cookies, headers=api_h, timeout=15).json()
    if info.get('code') != 0:
        raise RuntimeError(f"view API error: {info.get('message')}")
    info_data = info['data']
    cid = info_data['cid']
    duration = float(info_data.get('duration') or 0)
    title = info_data.get('title') or 'Bilibili Video'

    # Step 4: get playurl with all mirrors
    pu = req_lib.get(
        f'https://api.bilibili.com/x/player/playurl?bvid={bvid}&cid={cid}&qn={qn}&fnval=4048&fnver=0&fourk=1',
        cookies=cookies, headers=api_h, timeout=15
    ).json()
    if pu.get('code') != 0:
        raise RuntimeError(f"playurl API error: {pu.get('message')}")
    dash = pu['data'].get('dash', {})
    if not dash:
        raise RuntimeError("playurl returned no DASH streams (video may be DRM/restricted)")

    # Step 5: pick the requested video stream (match qn AND codec family)
    def _codec_family(codec: str) -> str:
        c = (codec or '').lower()
        if c.startswith('avc') or c.startswith('h264'): return 'avc'
        if c.startswith('hev') or c.startswith('hvc') or c.startswith('h265'): return 'hev'
        if c.startswith('av0') or c.startswith('av1'):  return 'av1'
        return 'other'

    def _select_video(streams, target_id, want):
        # 1. Exact qn + desired codec family
        for s in streams:
            if s.get('id') == target_id and _codec_family(s.get('codecs','')) == want:
                return s
        # 2. Exact qn, any codec (warn that codec fell back)
        for s in streams:
            if s.get('id') == target_id:
                return s
        # 3. Highest-qn stream with desired codec family
        matching = sorted([s for s in streams if _codec_family(s.get('codecs','')) == want],
                          key=lambda s: (s.get('id', 0), s.get('bandwidth', 0)), reverse=True)
        if matching: return matching[0]
        # 4. Last resort: first stream
        return streams[0] if streams else None

    vid = _select_video(dash.get('video', []), qn, want_codec)
    if vid and _codec_family(vid.get('codecs','')) != want_codec:
        print(f"  ⚠ Requested {want_codec.upper()} but only {_codec_family(vid.get('codecs','')).upper()} available for qn={qn}",
              flush=True)
    # Audio: match by id only (single codec per audio id on B站)
    aud_streams = dash.get('audio', [])
    aud = next((s for s in aud_streams if s.get('id') == aud_fmt),
               aud_streams[0] if aud_streams else None)
    if not vid or not aud:
        raise RuntimeError("Could not select video/audio streams from playurl response")

    print(f"  Selected: vid id={vid.get('id')} {vid.get('width')}x{vid.get('height')}@{vid.get('frameRate')}fps "
          f"codec={vid.get('codecs','')[:15]} bw={vid.get('bandwidth',0)//1000}kbps", flush=True)

    # Step 6: build MirrorPool for each track. The pool includes the
    # API-returned primary + backupUrls, PLUS hardcoded extra mirror hosts
    # (cn-hk-eq-01-02, mirrorali, mirrorhw, ...) that the playurl API
    # doesn't return for overseas clients but still serve the same paths.
    # Speed-probe happens inside MirrorPool on first fetch.
    vid_primary = vid['baseUrl']
    vid_backups = list(vid.get('backupUrl') or vid.get('backup_url') or [])
    aud_primary = aud['baseUrl']
    aud_backups = list(aud.get('backupUrl') or aud.get('backup_url') or [])

    vid_pool = MirrorPool(vid_primary, vid_backups, label='video')
    aud_pool = MirrorPool(aud_primary, aud_backups, label='audio')

    # Probe video pool now so the user sees speed info up front. Audio is
    # tiny (a few MB total) and probe time would dominate, so skip it.
    vid_pool.probe()

    return {
        'video_url': vid_pool,
        'audio_url': aud_pool,
        'duration': duration,
        'title': title,
    }


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 mp4_proxy_v3.py <bilibili_url> [tv_ip] [format_selector] [workers]", flush=True)
        print("Example: python3 mp4_proxy_v3.py https://b23.tv/xxxx 192.168.1.102 30080+30280 20", flush=True)
        sys.exit(1)

    bilibili_url = sys.argv[1]
    tv_ip_arg = sys.argv[2] if len(sys.argv) > 2 else None
    tv_ip = resolve_tv_ip(tv_ip_arg)
    format_selector = sys.argv[3] if len(sys.argv) > 3 else '30080+30280'
    workers = int(sys.argv[4]) if len(sys.argv) > 4 else 20

    print(f"Resolving URL via yt-dlp... ({format_selector})", flush=True)
    urls = resolve_urls_from_bilibili(bilibili_url, format_selector)
    print(f"Title: {urls['title'][:80]}", flush=True)
    print(f"Duration: {urls['duration']:.1f}s", flush=True)
    print(f"Workers: {workers}", flush=True)

    vmp4 = VirtualMP4(urls['video_url'], urls['audio_url'], urls['duration'], workers=workers)
    vmp4.initialize()
    
    hdr = vmp4.header_blob; hdr_len = len(hdr)
    
    # Number of upcoming segments to prefetch when serving the current one.
    # Each segment is ~5-10 MB, so 4 ahead = ~30 MB readahead buffer per
    # track — enough headroom that mirror jitter doesn't cause underrun.
    PREFETCH_AHEAD = 4

    def _seg_fetcher(tr, si):
        track = vmp4.vid if tr == 'v' else vmp4.aud
        cdn_off, cdn_len = track.seg_mdat[si]
        url = track.url
        return lambda: cdn_fetch(url, cdn_off, cdn_off + cdn_len - 1)

    def _trigger_prefetch(tr, si):
        track = vmp4.vid if tr == 'v' else vmp4.aud
        n_segs = len(track.seg_mdat)
        for k in range(1, PREFETCH_AHEAD + 1):
            ni = si + k
            if ni >= n_segs: break
            cache.prefetch((tr, ni), _seg_fetcher(tr, ni))

    def read_range(offset, length):
        r = bytearray(); p = offset; rem = length
        while rem > 0 and p < vmp4.total_size:
            if p < hdr_len:
                n = min(rem, hdr_len-p); r.extend(hdr[p:p+n]); p += n; rem -= n
            else:
                ch = vmp4.find_chunk(p)
                if not ch: break
                co, cl, tr, si = ch
                seg = cache.get((tr, si), _seg_fetcher(tr, si))
                # Warm upcoming segments on the same track in the background
                _trigger_prefetch(tr, si)
                ci = p - co; n = min(rem, cl-ci)
                r.extend(seg[ci:ci+n]); p += n; rem -= n
        return bytes(r)
    
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            tot = vmp4.total_size; rh = self.headers.get('Range')
            if rh:
                pts = rh.replace('bytes=','').split('-')
                s = int(pts[0]); e = int(pts[1]) if pts[1] else tot-1; l = e-s+1
                self.send_response(206)
                self.send_header('Content-Range', f'bytes {s}-{e}/{tot}')
                self.send_header('Content-Length', str(l))
            else:
                s = 0; l = tot; self.send_response(200)
                self.send_header('Content-Length', str(tot))
            self.send_header('Content-Type', 'video/mp4')
            self.send_header('Accept-Ranges', 'bytes')
            self.end_headers()
            p = s; sent = 0
            try:
                while sent < l:
                    n = min(65536, l-sent); d = read_range(p, n)
                    if not d: break
                    self.wfile.write(d); p += len(d); sent += len(d)
            except (BrokenPipeError, ConnectionResetError, OSError): pass
        def do_HEAD(self):
            self.send_response(200)
            self.send_header('Content-Type','video/mp4')
            self.send_header('Content-Length',str(vmp4.total_size))
            self.send_header('Accept-Ranges','bytes')
            self.end_headers()
        def log_message(self, *a): pass
    
    from http.server import ThreadingHTTPServer
    srv = ThreadingHTTPServer(('0.0.0.0', PORT), H)
    srv.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect(('192.168.1.1',80)); LAN_IP = s.getsockname()[0]; s.close()
    
    print(f'Proxy: http://{LAN_IP}:{PORT}/video.mp4 ({vmp4.total_size//1024//1024}MB)', flush=True)
    
    # Push to TV using shared helper
    from tv_discovery import push_to_tv
    c = get_tv_client(tv_ip)
    media_url = f'http://{LAN_IP}:{PORT}/video.mp4'
    resp = push_to_tv(c, media_url, urls['title'],
                      content_length=str(vmp4.total_size),
                      duration=int(urls['duration']))
    print(f'TV: {resp}', flush=True)
    
    try:
        while True: time.sleep(60)
    except KeyboardInterrupt: pass
    srv.shutdown()

if __name__ == '__main__':
    main()
