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

DEFAULT_TV_IP = None  # Auto-discovered via tv_discovery.py
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

def cdn_fetch(url, start, end, retries=3):
    for attempt in range(retries):
        try:
            return req_lib.get(url, headers={**CDN_H, 'Range': f'bytes={start}-{end}'}, timeout=30).content
        except Exception as e:
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
                                        if bt5=='stsd': return moov_data[bo5:bo5+bs5]
    return make_full_box('stsd', 0, 0, struct.pack('>I', 0))

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
        
        def fetch_worker(indices):
            """Each worker uses its own session, fetches 4KB per segment."""
            sess = req_lib.Session()
            sess.headers.update(CDN_H)
            for i in indices:
                off = seg_offsets[i]
                for attempt in range(5):
                    try:
                        resp = sess.get(self.url,
                            headers={'Range': f'bytes={off}-{off+4095}'}, timeout=30)
                        chunk = resp.content
                        moof_sz = struct.unpack('>I', chunk[0:4])[0]
                        if moof_sz > len(chunk):
                            resp2 = sess.get(self.url,
                                headers={'Range': f'bytes={off}-{off+moof_sz-1}'}, timeout=30)
                            chunk = resp2.content
                        moof_data[i] = (chunk[:moof_sz], moof_sz)
                        break
                    except Exception:
                        if attempt < 4:
                            time.sleep(0.2 * (attempt + 1))
                            try: sess.close()
                            except: pass
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
        threads = [threading.Thread(target=fetch_worker, args=(c,)) for c in chunks]
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
        
        # stco: one entry per segment (placeholder, fixed later)
        stco = make_full_box('stco', 0, 0,
            struct.pack('>I', len(tr.seg_mdat)) + b'\x00' * (4 * len(tr.seg_mdat)))
        
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
        vo = [c[0] for c in self.chunks if c[2]=='v']
        ao = [c[0] for c in self.chunks if c[2]=='a']
        m = bytearray(self.moov); idx = [0]
        def fix(s, e):
            p = s
            while p < e-8:
                sz = struct.unpack('>I', m[p:p+4])[0]; bt = m[p+4:p+8]
                if sz <= 0: break
                if bt == b'stco':
                    cnt = struct.unpack('>I', m[p+12:p+16])[0]
                    offs = vo if idx[0]==0 else ao
                    for i in range(min(cnt, len(offs))):
                        struct.pack_into('>I', m, p+16+i*4, offs[i])
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
    def __init__(self, max_mb=500):
        self.d = {}; self.sz = 0; self.mx = max_mb*1024*1024; self.lk = threading.Lock()
    def get(self, k, fn):
        with self.lk:
            if k in self.d: return self.d[k]
        v = fn()
        with self.lk:
            while self.sz + len(v) > self.mx and self.d:
                ok = next(iter(self.d)); self.sz -= len(self.d[ok]); del self.d[ok]
            self.d[k] = v; self.sz += len(v)
        return v

cache = Cache()

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

def resolve_urls_from_bilibili(url: str, format_selector: str = "30080+30280") -> dict:
    """Resolve Bilibili short/long URL to direct video/audio CDN URLs via yt-dlp."""
    yt_auth_args = _build_yt_cookie_args()
    r = subprocess.run(
        ['yt-dlp', '-j', '--no-warnings', *yt_auth_args, '-f', format_selector, url],
        capture_output=True, text=True, timeout=45
    )
    if r.returncode != 0 or not r.stdout.strip():
        raise RuntimeError(f"yt-dlp failed resolving URL: {r.stderr.strip()[:300]}")
    
    v = json.loads(r.stdout)
    vu = au = None
    for f in v.get('requested_formats', []):
        if f.get('vcodec', 'none') != 'none':
            vu = f.get('url')
        elif f.get('acodec', 'none') != 'none':
            au = f.get('url')
    if not vu or not au:
        raise RuntimeError("Could not resolve both video and audio URLs from yt-dlp output")
    
    return {
        'video_url': vu,
        'audio_url': au,
        'duration': float(v.get('duration') or 0),
        'title': v.get('title') or 'Bilibili Video'
    }


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 mp4_proxy_v3.py <bilibili_url> [tv_ip] [format_selector] [workers]", flush=True)
        print("Example: python3 mp4_proxy_v3.py https://b23.tv/xxxx <TV_IP> 30080+30280 20", flush=True)
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
    
    def read_range(offset, length):
        r = bytearray(); p = offset; rem = length
        while rem > 0 and p < vmp4.total_size:
            if p < hdr_len:
                n = min(rem, hdr_len-p); r.extend(hdr[p:p+n]); p += n; rem -= n
            else:
                ch = vmp4.find_chunk(p)
                if not ch: break
                co, cl, tr, si = ch
                track = vmp4.vid if tr=='v' else vmp4.aud
                cdn_off, cdn_len = track.seg_mdat[si]
                seg = cache.get((tr, si), lambda t=tr,o=cdn_off,l=cdn_len: cdn_fetch(
                    vmp4.vid.url if t=='v' else vmp4.aud.url, o, o+l-1))
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
    s.connect(('8.8.8.8',80)); LAN_IP = s.getsockname()[0]; s.close()
    
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
