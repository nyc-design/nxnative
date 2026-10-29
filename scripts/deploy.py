#!/usr/bin/env python3
# One command from a game's NSP folder to the game running on your own iPhone, iPad or Apple Vision Pro, signed with your Apple developer account:
#   build_ipa_from_nsp.py --romfs separate (the game's Xcode project; --platform visionos for Apple Vision Pro, picked from the device) → xcodebuild (signed with an
#   Xcode-managed profile) → devicectl install → the game files (the RomFS image) copied once per game version and device → launch → the app's Documents/log.txt back to the Mac.
# Usage: python3 scripts/deploy.py <NSP folder> [-k <prod.keys>] [--device iphone | ipad | vision | <device name or UDID>] [--team-id <team>] [--bundle-prefix com.<you>.nxnative]
#   Any other option goes to build_ipa_from_nsp.py (--language ja, --gpu g18p, ...). Exit code 0 when the game was installed (and launched), 1 on error, 130 when interrupted.
# Needs: what build_ipa_from_nsp.py needs; Xcode signed in to your Apple ID (Xcode → Settings → Accounts); the device paired with this Mac (once, in Xcode → Devices), with
#   Developer Mode on, unlocked, by cable or on the same network. A paid account's apps last a year; a free account's, 7 days.
# The team ID, bundle prefix and prod.keys given once are kept in ~/.config/nxnative/deploy.json for the next run; with no team ID anywhere, the team of the Apple Development
#   certificate in the keychain is used when there is exactly one. The bundle prefix names new games' apps (<prefix>.<title ID>, .visionos added for Apple Vision Pro):
#   it must be one your team can register (org.nxnative.* is taken). A game prepared before keeps the bundle ID saved in its Xcode project.
# The game files are copied only when the device has none from this Mac for this image (the image's identity is kept per device and app in <id>_build/deploy_state.json,
#   and the device's copy must have the image's size); --push-romfs copies them anyway. When the app's log says its image is unusable, they are copied again and the app restarted.
import argparse, json, os, re, subprocess, sys, tempfile, time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'dm/ios/tools'))
import apple_platform

CONFIG = Path(os.path.expanduser('~/.config/nxnative/deploy.json'))
UNUSABLE = 'game file image on this device is incomplete or invalid'   # main.m's verdict on Documents/romfs.img


class DeployError(Exception): pass


def say(msg): print(msg, flush=True)


def run(cmd, log=None, check=True):
    """Run a command; its output goes to log (a path) or is returned. A failure raises DeployError with the output's last lines"""
    if log:
        with open(log, 'w') as f: r = subprocess.run([str(c) for c in cmd], stdout=f, stderr=subprocess.STDOUT, text=True)
        out = Path(log).read_text(errors='replace')
    else:
        r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True); out = r.stdout + r.stderr
    if check and r.returncode: raise DeployError(f'{Path(str(cmd[0])).name} {cmd[1] if len(cmd) > 1 else ""} failed ({r.returncode}):\n' + '\n'.join(out.splitlines()[-15:]))
    return r.returncode, out


def devicectl_json(*args):
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / 'out.json'
        code, out = run(['xcrun', 'devicectl', *args, '--json-output', path], check=False)
        data = json.loads(path.read_text()) if path.exists() else {}
    if code or 'result' not in data: raise DeployError(f'devicectl {" ".join(map(str, args))} failed:\n' + '\n'.join(out.splitlines()[-10:]))
    return data['result']


# ---- devices ----

def physical(devs):   # paired real devices (no simulators), as (udid, name, platform, type, developer mode)
    out = []
    for d in devs:
        h, p, c = d.get('hardwareProperties', {}), d.get('deviceProperties', {}), d.get('connectionProperties', {})
        if h.get('reality') != 'physical' or c.get('pairingState') != 'paired': continue
        if h.get('platform') not in ('iOS', 'visionOS'): continue
        out.append({'udid': h.get('udid') or d.get('identifier'), 'name': p.get('name', '?'), 'platform': 'visionos' if h['platform'] == 'visionOS' else 'ios',
                    'type': h.get('deviceType', ''), 'devmode': p.get('developerModeStatus')})
    return out


def pick_device(devs, want):
    """want: None (the only paired device), iphone / ipad / vision, or a device name or UDID (case-insensitive)"""
    if not devs: raise DeployError('no paired iPhone, iPad or Apple Vision Pro: connect it once by cable (or pair it in Xcode → Window → Devices and Simulators) and trust this Mac')
    w = (want or '').lower()
    kinds = {'iphone': lambda d: d['type'] == 'iPhone', 'ipad': lambda d: d['type'] == 'iPad',
             'vision': lambda d: d['platform'] == 'visionos', 'visionos': lambda d: d['platform'] == 'visionos', 'visionpro': lambda d: d['platform'] == 'visionos'}
    hits = [d for d in devs if kinds[w](d)] if w in kinds else [d for d in devs if w in (d['udid'].lower(), d['name'].lower())] if w else devs
    names = ', '.join(f'{d["name"]} ({d["udid"]})' for d in devs)
    if not hits: raise DeployError(f'no paired device matches {want!r}; paired: {names}')
    if len(hits) > 1: raise DeployError(f'{len(hits)} devices match{" " + repr(want) if want else ""}; choose one with --device <name or UDID>: {", ".join(d["name"] + " (" + d["udid"] + ")" for d in hits)}')
    d = hits[0]
    if d['devmode'] not in (None, 'enabled'): raise DeployError(f'{d["name"]}: Developer Mode is off (Settings → Privacy & Security → Developer Mode, then restart the device)')
    return d


# ---- signing ----

def detect_team():
    """The team (certificate OU) of the Apple Development identities in the keychain, when there is exactly one; else None"""
    _, out = run(['security', 'find-identity', '-v', '-p', 'codesigning'], check=False)
    teams = set()
    for name in sorted(set(re.findall(r'"(Apple Development: [^"]+)"', out))):
        code, pem = run(['security', 'find-certificate', '-c', name, '-p'], check=False)
        if code: continue
        r = subprocess.run(['openssl', 'x509', '-noout', '-subject'], input=pem, capture_output=True, text=True)
        m = re.search(r'OU\s*=\s*([A-Z0-9]{10})', r.stdout)
        if m: teams.add(m.group(1))
    return teams.pop() if len(teams) == 1 else None


def load_config():
    try: return json.loads(CONFIG.read_text())
    except (OSError, ValueError): return {}


def save_config(cfg):
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(cfg, indent=1, sort_keys=True) + '\n')


# ---- build ----

SKIP_SAY = ('the game files are not in this build', 'scripts/push_romfs.sh', 'choose the game scheme')   # the build's directions for the steps deploy does itself


def prepare(nsp_dir, dev, keys, team, prefix, extra):
    """build_ipa_from_nsp.py for the device's platform, with the game files kept out of the app; returns its result event (project, bundle ID, RomFS image, ...)"""
    cmd = [sys.executable, REPO / 'scripts/build_ipa_from_nsp.py', nsp_dir, '--romfs', 'separate', '--progress', 'json']
    cmd += ['--platform', 'visionos'] if dev['platform'] == 'visionos' else ['--prepare-xcode']
    if keys: cmd += ['-k', keys]
    if team: cmd += ['--team-id', team]
    if prefix: cmd += ['--bundle-prefix', prefix]
    cmd += extra
    result = None
    p = subprocess.Popen([str(c) for c in cmd], stdout=subprocess.PIPE, text=True)
    for line in p.stdout:
        try: ev = json.loads(line)
        except ValueError: say(line.rstrip()); continue
        kind = ev.get('ev')
        if kind == 'result': result = ev
        elif kind == 'say' and not re.sub(r'^\[\d\d:\d\d:\d\d\] ', '', ev['msg']).lstrip().startswith(SKIP_SAY): say('  ' + ev['msg'])
        elif kind == 'stage': say(f'  [{ev["k"]}/{ev["n"]}] {ev["name"]} …')
        elif kind == 'stage_end': say(f'  {"✓" if ev["ok"] else "✗"} {ev["name"]} ({ev["elapsed"]:.0f} s)' + (f': {ev["summary"]}' if ev.get('summary') else ''))
    if p.wait(): raise DeployError(f'build_ipa_from_nsp.py failed ({p.returncode}); see the lines above')
    if not result: raise DeployError('build_ipa_from_nsp.py reported no Xcode project')
    return result


def scheme_of(project):
    _, out = run(['xcodebuild', '-list', '-json', '-project', project])
    schemes = json.loads(out[out.index('{'):])['project']['schemes']
    if len(schemes) != 1: raise DeployError(f'{project}: expected one scheme, found {schemes}')
    return schemes[0]


SIGNING_HINTS = (('No Accounts', 'Xcode is not signed in: Xcode → Settings → Accounts → + → Apple ID, then run again'),
                 ('cannot be registered to your development team', 'this bundle ID belongs to another team: run again with --bundle-prefix com.<you>.nxnative '
                  '(a game prepared before keeps its saved ID: also give --bundle-id <new ID> once)'),
                 ('requires a provisioning profile', 'no profile: check the team ID (--team-id) and that Xcode is signed in to that team'),
                 ('Unable to find a destination', 'Xcode does not see the device: unlock it, keep it on the same network or connect the cable, and check Xcode → Window → Devices'))


def xcode_build(res, dev, log):
    platform = res['platform']
    cmd = ['xcodebuild', '-project', res['project'], '-scheme', scheme_of(res['project']), '-configuration', 'Release', '-destination', f'id={dev["udid"]}',
           '-derivedDataPath', res['derived_data'], '-allowProvisioningUpdates']
    if res.get('team_id') and res['team_id'] != '0000000000': cmd.append(f'DEVELOPMENT_TEAM={res["team_id"]}')
    code, out = run(cmd + ['build'], log=log, check=False)
    if code:
        errors = [l.strip() for l in out.splitlines() if ' error: ' in l or l.startswith('error:')][:8]
        hints = [h for k, h in SIGNING_HINTS if k in out]
        raise DeployError('xcodebuild failed (full log: ' + str(log) + '):\n  ' + '\n  '.join(errors or out.splitlines()[-10:]) + ''.join('\n→ ' + h for h in hints))
    apps = sorted((Path(res['derived_data']) / 'Build/Products' / apple_platform.products({'DM_APP_PLATFORM': platform})).glob('*.app'), key=lambda p: p.stat().st_mtime)
    if not apps: raise DeployError(f'xcodebuild made no app under {res["derived_data"]}')
    return apps[-1]


# ---- device steps ----

def remote_size(dev, bundle, name):
    try: files = devicectl_json('device', 'info', 'files', '--device', dev['udid'], '--domain-type', 'appDataContainer', '--domain-identifier', bundle, '--subdirectory', 'Documents')['files']
    except DeployError: return None
    return next((f['metadata'].get('size') for f in files if f.get('relativePath') == name), None)


def image_identity(img):
    _, out = run([sys.executable, '-I', REPO / 'dm/tools/romfs_image.py', '--identity', img])
    return out.strip()


def need_push(state, key, ident, size, remote):   # copy unless this device got this very image from here and still holds a file of its size
    return not (state.get(key) == ident and remote == size)


def retrying(what, fn, tries=3):
    """A device transfer over the network can drop (the headset taken off, Wi-Fi roaming): try again, after a pause, while the error is a dropped connection"""
    for n in range(1, tries + 1):
        try: return fn()
        except DeployError as e:
            if n == tries or not any(k in str(e) for k in DROPPED): raise
            say(f'  {what}: the connection dropped; trying again in 10 s ({n + 1}/{tries}). Keep the device awake and unlocked')
            time.sleep(10)


DROPPED = ('Connection interrupted', 'unexpectedly closed', 'error 3002', 'Connection invalid', 'tunnel', 'timed out')


def push_romfs(dev, bundle, img):
    say(f'copying the game files ({Path(img).stat().st_size / 2**30:.1f} GB) to {dev["name"]}: keep it unlocked; a cable is far faster than the network')
    t = time.time()
    retrying('game files', lambda: run(['bash', REPO / 'scripts/push_romfs.sh', dev['udid'], bundle, img]))
    say(f'  game files copied ({time.time() - t:.0f} s)')


def launch(dev, bundle, wait=120):
    t = time.time(); told = False
    while True:
        code, out = run(['xcrun', 'devicectl', 'device', 'process', 'launch', '--terminate-existing', '--device', dev['udid'], bundle], check=False)
        if not code: return
        if 'Locked' not in out and 'could not be, unlocked' not in out: raise DeployError('launch failed:\n' + '\n'.join(out.splitlines()[-8:]))
        if time.time() - t > wait: raise DeployError(f'{dev["name"]} stayed locked; unlock it and open the game yourself')
        if not told: say(f'{dev["name"]} is locked: unlock it (waiting up to {wait} s)'); told = True
        time.sleep(5)


def fetch_log(dev, bundle, dest):
    code, out = run(['xcrun', 'devicectl', 'device', 'copy', 'from', '--device', dev['udid'], '--domain-type', 'appDataContainer', '--domain-identifier', bundle,
                     '--source', 'Documents/log.txt', '--destination', dest], check=False)
    return Path(dest).read_text(errors='replace') if not code and Path(dest).exists() else None


def summarize_log(text, dest):
    lines = text.splitlines(); red = [l for l in lines if l.startswith('[RED] ')]
    say(f'device log: {dest} ({len(lines):,} lines, {len(red)} [RED])')
    for l in lines[-12:]: say('  ' + l[:200])


# ---- main ----

def parse_args(argv):
    ap = argparse.ArgumentParser(prog='deploy.py', description='Build a game for your own iPhone, iPad or Apple Vision Pro, sign it with your Apple developer account, install it, '
                                 'copy its game files and launch it. Options this script does not know go to build_ipa_from_nsp.py.',
                                 epilog='example: python3 scripts/deploy.py ~/Games/Odyssey -k ~/keys/prod.keys --device vision --bundle-prefix com.example.nxnative')
    ap.add_argument('nsp_dir', help='folder holding one game\'s NSPs')
    ap.add_argument('-k', '--keys', help='prod.keys (kept for later runs)')
    ap.add_argument('--device', help='iphone, ipad, vision, or a device name or UDID (default: the only paired device)')
    ap.add_argument('--team-id', help='Apple developer team ID (kept; default: the saved one, else the team of the only Apple Development certificate)')
    ap.add_argument('--bundle-prefix', help='start of new games\' bundle IDs, e.g. com.<you>.nxnative (kept)')
    ap.add_argument('--push-romfs', action='store_true', help='copy the game files even when the device has them')
    ap.add_argument('--no-launch', action='store_true', help='install only')
    ap.add_argument('--log-secs', type=int, default=60, help='seconds to let the game run before fetching its log (0: no log; default 60)')
    return ap.parse_known_args(argv)


def main(argv):
    args, extra = parse_args(argv)
    nsp_dir = Path(os.path.expanduser(args.nsp_dir)).resolve()
    if not nsp_dir.is_dir(): raise DeployError(f'no folder {nsp_dir}')
    cfg = load_config()
    for k in ('keys', 'team_id', 'bundle_prefix'):
        v = getattr(args, k)
        if v: cfg[k] = str(Path(os.path.expanduser(v)).resolve()) if k == 'keys' else v
    team = cfg.get('team_id') or detect_team()
    if team and not cfg.get('team_id'): say(f'team: {team} (from the Apple Development certificate)')
    if any(getattr(args, k) for k in ('keys', 'team_id', 'bundle_prefix')): save_config(cfg)

    dev = pick_device(physical(devicectl_json('list', 'devices')['devices']), args.device)
    say(f'device: {dev["name"]} ({dev["udid"]}, {"Apple Vision Pro" if dev["platform"] == "visionos" else dev["type"] or "iOS"})')
    say('preparing the Xcode project (build_ipa_from_nsp.py) …')
    res = prepare(nsp_dir, dev, cfg.get('keys'), team, cfg.get('bundle_prefix'), extra)
    work, bundle, platform = Path(res['work']), res['bundle_id'], res['platform']
    if res.get('team_id', '0000000000') == '0000000000': raise DeployError('no team ID: give --team-id (Xcode → Settings → Accounts shows it)')
    logs = work / 'logs'; logs.mkdir(parents=True, exist_ok=True)
    say(f'building and signing {bundle} for {dev["name"]} (team {res["team_id"]}) …')
    t = time.time()
    app = xcode_build(res, dev, logs / f'deploy_xcodebuild_{platform}.log')
    say(f'  built {app.name} ({time.time() - t:.0f} s)')
    say(f'installing on {dev["name"]} …')
    t = time.time()
    retrying('install', lambda: run(['xcrun', 'devicectl', 'device', 'install', 'app', '--device', dev['udid'], app]))
    say(f'  installed ({time.time() - t:.0f} s)')

    img, pushed = res.get('romfs'), False
    state_file = work / 'deploy_state.json'
    state = json.loads(state_file.read_text()) if state_file.exists() else {}
    key = f'{dev["udid"]} {bundle}'
    if img:
        ident = image_identity(img)
        if args.push_romfs or need_push(state, key, ident, Path(img).stat().st_size, remote_size(dev, bundle, 'romfs.img')):
            state.pop(key, None); state_file.write_text(json.dumps(state, indent=1))   # forgotten until the copy completes
            push_romfs(dev, bundle, img); pushed = True
            state[key] = ident; state_file.write_text(json.dumps(state, indent=1))
        else: say('game files: already on the device')
    else: say('[RED] the build names no RomFS image (an extraction with a RomFS folder): copy the game files with scripts/push_romfs.sh')

    if args.no_launch: say('done: installed'); return
    say(f'launching {bundle} …')
    launch(dev, bundle)
    if args.log_secs <= 0: say('done: running'); return
    say(f'letting it run {args.log_secs} s, then fetching its log …')
    time.sleep(args.log_secs)
    dest = logs / f'device_{platform}_log.txt'
    text = fetch_log(dev, bundle, dest)
    if text and UNUSABLE in text and img and not pushed:   # the device's copy is not this image after all (cut short, or another version): copy again and restart
        say('the app says its game files are unusable: copying them again')
        state.pop(key, None); state_file.write_text(json.dumps(state, indent=1))
        push_romfs(dev, bundle, img)
        state[key] = image_identity(img); state_file.write_text(json.dumps(state, indent=1))
        launch(dev, bundle); time.sleep(args.log_secs)
        text = fetch_log(dev, bundle, dest)
    if text is None: say('[RED] could not fetch Documents/log.txt'); return
    summarize_log(text, dest)
    if UNUSABLE in text: raise DeployError('the app still finds its game files unusable; run again with --push-romfs')
    say('done: the game is running')


def cli(argv):
    try: main(argv); return 0
    except DeployError as e: say(f'[RED] {e}'); return 1
    except KeyboardInterrupt: say('[RED] interrupted'); return 130


if __name__ == '__main__':
    sys.exit(cli(sys.argv[1:]))
