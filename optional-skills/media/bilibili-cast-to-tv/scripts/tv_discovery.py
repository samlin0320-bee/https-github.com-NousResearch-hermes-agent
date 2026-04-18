"""LG TV discovery and connection helpers.

Shared by bili_cast.py and mp4_proxy_v3.py.
Probes WSS port 3001 to verify TV is reachable, scans subnet if not.
Auto-updates lgtv.yaml when IP changes.
"""
import json
import os
import socket
import ssl
import sys
import concurrent.futures
from pathlib import Path


DEFAULT_TV_IP = '192.168.1.103'
CONFIG_PATH = Path.home() / '.hermes' / 'smart_home' / 'lgtv.yaml'
KEYS_PATH = Path.home() / '.hermes' / 'smart_home' / 'lgtv_keys.json'


def _probe_wss(ip: str, timeout: float = 2.0) -> bool:
    """Check if LG TV WSS port 3001 is reachable."""
    try:
        sock = socket.create_connection((ip, 3001), timeout=timeout)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        ssock = ctx.wrap_socket(sock, server_hostname=ip)
        ssock.close()
        return True
    except Exception:
        return False


def _discover_ssdp(local_ip: str = None, timeout: float = 3.0) -> list[str]:
    """Discover LG TVs via SSDP multicast. Fast (~3s), returns list of IPs.

    Requires Hyper-V firewall allow rule on Windows for WSL2 mirrored mode:
    Set-NetFirewallHyperVVMSetting -Name '{40E0AC32-...}' -DefaultInboundAction Allow
    """
    SSDP_ADDR = '239.255.255.250'
    SSDP_PORT = 1900
    msg = (
        'M-SEARCH * HTTP/1.1\r\n'
        'HOST: 239.255.255.250:1900\r\n'
        'MAN: "ssdp:discover"\r\n'
        'MX: 2\r\n'
        'ST: urn:schemas-upnp-org:device:MediaRenderer:1\r\n'
        '\r\n'
    )

    if not local_ip:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(('192.168.1.1', 80))
            local_ip = s.getsockname()[0]
            s.close()
        except Exception:
            return []

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.settimeout(timeout)
        sock.bind((local_ip, 0))
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF,
                        socket.inet_aton(local_ip))
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 4)
        sock.sendto(msg.encode(), (SSDP_ADDR, SSDP_PORT))
    except Exception:
        return []

    found = set()
    try:
        while True:
            data, addr = sock.recvfrom(4096)
            text = data.decode(errors='ignore')
            if 'LG' in text.upper() or 'webos' in text.lower():
                found.add(addr[0])
    except socket.timeout:
        pass
    finally:
        sock.close()

    return list(found)


def _scan_subnet(prefix: str) -> list[str]:
    """Scan /24 subnet for WSS port 3001 (LG TVs). Fallback when SSDP fails."""
    found = []

    def try_ip(i):
        ip = f'{prefix}.{i}'
        if _probe_wss(ip, timeout=1.5):
            return ip
        return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=50) as ex:
        futures = {ex.submit(try_ip, i): i for i in range(1, 255)}
        for f in concurrent.futures.as_completed(futures):
            result = f.result()
            if result:
                found.append(result)

    return found


def _read_config_tv(tv_name: str = 'Living Room TV') -> dict | None:
    """Read TV entry from lgtv.yaml config. Returns dict with 'ip', 'mac', 'name'."""
    if not CONFIG_PATH.exists():
        return None
    try:
        import yaml
        cfg = yaml.safe_load(CONFIG_PATH.read_text())
        for tv in cfg.get('tvs', []):
            if tv.get('name', '').lower() == tv_name.lower():
                return tv
        # Fallback: return first TV
        tvs = cfg.get('tvs', [])
        return tvs[0] if tvs else None
    except Exception:
        return None


def _get_mac_for_ip(ip: str) -> str | None:
    """Get MAC address for an IP via ARP table."""
    import subprocess
    try:
        # Ping first to populate ARP cache
        subprocess.run(['ping', '-c', '1', '-W', '1', ip],
                       capture_output=True, timeout=3)
        result = subprocess.run(['ip', 'neigh', 'show', ip],
                                capture_output=True, text=True, timeout=3)
        for line in result.stdout.strip().split('\n'):
            parts = line.split()
            if 'lladdr' in parts:
                idx = parts.index('lladdr')
                return parts[idx + 1].lower()
    except Exception:
        pass
    return None


def _match_tv_by_mac(found_ips: list[str], target_mac: str) -> str | None:
    """Match a discovered TV IP by MAC address."""
    if not target_mac:
        return None
    target_mac = target_mac.lower()
    for ip in found_ips:
        mac = _get_mac_for_ip(ip)
        if mac and mac == target_mac:
            return ip
    return None


def _update_config_ip(old_ip: str, new_ip: str):
    """Update lgtv.yaml with new IP."""
    if not CONFIG_PATH.exists():
        return
    try:
        text = CONFIG_PATH.read_text()
        if old_ip in text:
            text = text.replace(old_ip, new_ip, 1)
            CONFIG_PATH.write_text(text)
            print(f'   📝 Updated lgtv.yaml: {old_ip} → {new_ip}', flush=True)
    except Exception as e:
        print(f'   ⚠ Could not update config: {e}', flush=True)

    # Also update keys file if needed
    if KEYS_PATH.exists():
        try:
            keys = json.loads(KEYS_PATH.read_text())
            if old_ip in keys and new_ip not in keys:
                keys[new_ip] = keys[old_ip]
                KEYS_PATH.write_text(json.dumps(keys))
                print(f'   📝 Copied TV key: {old_ip} → {new_ip}', flush=True)
        except Exception:
            pass


def resolve_tv_ip(tv_ip: str | None = None, tv_name: str = 'Living Room TV') -> str:
    """Resolve and verify TV IP. Always probes before returning.

    1. If tv_ip given, probe it first
    2. If not given or unreachable, read from config and probe
    3. If still unreachable, scan subnet
    4. When multiple TVs found, match by MAC address from config
    5. Auto-update config if IP changed

    Returns verified IP or raises RuntimeError.
    """
    # Load config for MAC matching
    config_tv = _read_config_tv(tv_name)
    config_ip = config_tv.get('ip') if config_tv else None
    config_mac = config_tv.get('mac', '').lower() if config_tv else ''

    # Step 1: Try provided IP
    if tv_ip and _probe_wss(tv_ip):
        return tv_ip

    # Step 2: Try config IP (if different from provided)
    original_ip = tv_ip or config_ip or DEFAULT_TV_IP

    if config_ip and config_ip != tv_ip and _probe_wss(config_ip):
        return config_ip

    # Step 3: Discover via SSDP first (fast ~3s), then TCP scan as fallback
    print(f'   ⚠ TV at {original_ip} unreachable, discovering...', flush=True)
    found = _discover_ssdp()
    if found:
        print(f'   📡 SSDP found: {found}', flush=True)
    else:
        print(f'   📡 SSDP no response, scanning TCP 3001...', flush=True)
        prefix = '.'.join(original_ip.split('.')[:3])
        found = _scan_subnet(prefix)

    if not found:
        raise RuntimeError(
            f'No LG TV found on {prefix}.0/24. '
            f'Is the TV on and connected to WiFi?'
        )

    # Step 4: Match by MAC if multiple TVs found
    new_ip = None
    if len(found) > 1 and config_mac:
        print(f'   ℹ Found {len(found)} TVs: {found}. Matching by MAC...', flush=True)
        new_ip = _match_tv_by_mac(found, config_mac)
        if new_ip:
            print(f'   ✅ Matched {tv_name} at {new_ip} (MAC: {config_mac})', flush=True)
        else:
            print(f'   ⚠ MAC {config_mac} not matched, using first: {found[0]}', flush=True)

    if not new_ip:
        new_ip = found[0]
        if len(found) == 1:
            print(f'   ✅ Found TV at {new_ip}', flush=True)

    # Update config
    if original_ip != new_ip:
        _update_config_ip(original_ip, new_ip)

    return new_ip


def get_tv_client(tv_ip: str):
    """Connect to LG TV and return WebOSClient.

    Reuses the stored client_key for this IP if present. If the TV prompts
    for pairing (new IP, key lost, or key invalidated), the newly-issued
    key is persisted back to lgtv_keys.json so future connections are silent.

    Legacy cross-IP key reuse: if no key exists for this IP but another IP's
    key is present, we try it first — webOS accepts the same client_key on
    the same physical TV, so DHCP drift doesn't force re-pairing.
    """
    sys.path.insert(0, os.path.expanduser('~/.hermes/hermes-agent'))
    from pywebostv.connection import WebOSClient

    keys = json.loads(KEYS_PATH.read_text()) if KEYS_PATH.exists() else {}

    def _normalize(raw):
        if isinstance(raw, str):
            return {'client_key': raw}
        if isinstance(raw, dict):
            return dict(raw)
        return {}

    store = _normalize(keys.get(tv_ip))

    # Fallback: if we have no key for this IP, try any existing client_key
    # from the keys file (other IPs or the legacy top-level "client_key").
    # webOS binds the key to the TV hardware, not the IP, so this silently
    # survives DHCP drift.
    if not store.get('client_key'):
        candidates = []
        for k, v in keys.items():
            if k == tv_ip:
                continue
            n = _normalize(v)
            if n.get('client_key'):
                candidates.append(n['client_key'])
        if candidates:
            store = {'client_key': candidates[0]}

    key_before = store.get('client_key')
    c = WebOSClient(tv_ip, secure=True)
    c.connect()
    for _ in c.register(store):
        pass
    key_after = store.get('client_key')

    # Save if the key changed or the IP entry is missing
    if key_after and (key_after != key_before or tv_ip not in keys):
        keys[tv_ip] = store
        try:
            KEYS_PATH.parent.mkdir(parents=True, exist_ok=True)
            KEYS_PATH.write_text(json.dumps(keys))
        except Exception as e:
            print(f'   ⚠ Could not save TV key to {KEYS_PATH}: {e}', flush=True)

    return c


def push_to_tv(client, url: str, title: str, content_length: str = '-1',
               duration: int = 0, mime: str = 'video/mp4'):
    """Push a media URL to photovideo on LG TV."""
    payload = {
        'id': 'com.webos.app.photovideo',
        'params': {
            'payload': [{
                'fullPath': url,
                'artist': '', 'subtitle': '',
                'dlnaInfo': {
                    'flagVal': 4096,
                    'cleartextSize': '-1',
                    'contentLength': content_length,
                    'opVal': 1,
                    'protocolInfo': f'http-get:*:{mime}:DLNA.ORG_OP=01;DLNA.ORG_CI=0;DLNA.ORG_FLAGS=01700000000000000000000000000000',
                    'duration': duration,
                },
                'mediaType': 'VIDEO',
                'thumbnail': '', 'deviceType': 'DMR', 'album': '',
                'fileName': title[:60],
                'lastPlayPosition': -1,
            }]
        }
    }
    q = client.send_message('request', 'ssap://system.launcher/launch', payload, get_queue=True)
    return q.get(timeout=10)
