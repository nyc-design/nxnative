"""scripts/deploy.py: the device it picks, when it copies the game files, how it reads the build's result and how it explains a signing failure. Nothing here talks to a device."""
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / 'scripts'))
import deploy


def device(udid, name, platform, kind, reality='physical', paired='paired', devmode='enabled'):   # one entry of devicectl list devices --json-output
    return {'identifier': udid + '-core', 'hardwareProperties': {'udid': udid, 'platform': platform, 'reality': reality, 'deviceType': kind},
            'deviceProperties': {'name': name, 'developerModeStatus': devmode}, 'connectionProperties': {'pairingState': paired}}


LISTED = [device('00008150-PHONE', 'Neil\u2019s iPhone', 'iOS', 'iPhone'),
          device('00008112-VISION', 'Apple Vision Pro', 'visionOS', 'realityDevice'),
          device('SIM-1', 'iPhone 17 Pro', 'iOS', 'iPhone', reality='simulated'),
          device('00008030-WATCH', 'Watch', 'watchOS', 'appleWatch'),
          device('00008101-UNPAIRED', 'Old iPad', 'iOS', 'iPad', paired='unpaired')]


class Devices(unittest.TestCase):
    def test_only_paired_real_phones_tablets_and_headsets(self):
        self.assertEqual([(d['udid'], d['platform']) for d in deploy.physical(LISTED)], [('00008150-PHONE', 'ios'), ('00008112-VISION', 'visionos')])

    def test_pick_by_kind_name_or_udid(self):
        devs = deploy.physical(LISTED)
        self.assertEqual(deploy.pick_device(devs, 'vision')['udid'], '00008112-VISION')
        self.assertEqual(deploy.pick_device(devs, 'iphone')['udid'], '00008150-PHONE')
        self.assertEqual(deploy.pick_device(devs, 'APPLE VISION PRO')['platform'], 'visionos')
        self.assertEqual(deploy.pick_device(devs, '00008150-phone')['name'], 'Neil\u2019s iPhone')
        self.assertEqual(deploy.pick_device(devs[:1], None)['udid'], '00008150-PHONE')   # the only one needs no --device

    def test_pick_refuses_ambiguity_absence_and_developer_mode_off(self):
        devs = deploy.physical(LISTED)
        with self.assertRaisesRegex(deploy.DeployError, '2 devices match'): deploy.pick_device(devs, None)
        with self.assertRaisesRegex(deploy.DeployError, 'no paired device matches'): deploy.pick_device(devs, 'ipad')
        with self.assertRaisesRegex(deploy.DeployError, 'no paired iPhone'): deploy.pick_device([], None)
        off = deploy.physical([device('X', 'Phone', 'iOS', 'iPhone', devmode='disabled')])
        with self.assertRaisesRegex(deploy.DeployError, 'Developer Mode is off'): deploy.pick_device(off, None)


class GameFiles(unittest.TestCase):
    def test_a_crash_in_the_log_is_reported(self):   # the headset's first start: the launcher threw before the game ran, and deploy had said it was running
        log = ('launcher: 13 settings shown\n*** Terminating app due to uncaught exception \'UIApplicationInvalidInterfaceOrientation\', reason: \'...\'\n'
               'libc++abi: terminating due to uncaught exception of type NSException\n[dm] CRASH sig=6 addr=__pthread_kill+0x8\n')
        self.assertEqual(len(deploy.crashed(log)), 3)
        self.assertEqual(deploy.crashed('boot: game language en-US\n[dm] front end: thermal state 0\n'), [])
    def test_copy_unless_a_file_of_the_images_size_is_there(self):
        self.assertFalse(deploy.need_push(5, 5))   # copied before, or put there by hand (AirDrop): the app checks its identity
        self.assertTrue(deploy.need_push(5, 3))   # a copy cut short
        self.assertTrue(deploy.need_push(5, None))   # reinstalled: no copy at all


class Build(unittest.TestCase):
    def test_prepare_reads_the_result_event_and_hides_the_manual_directions(self):
        events = [{'ev': 'stage', 'k': 1, 'n': 2, 'name': 'prepare Xcode'},
                  {'ev': 'say', 'msg': '[02:01:05] the game files are not in this build. Copy them to the phone once'},
                  {'ev': 'say', 'msg': '  scripts/push_romfs.sh <device UDID> com.example.game "/x/RomFS.img"'},
                  {'ev': 'say', 'msg': '[02:01:05] choose the game scheme in Xcode, select your Team'},
                  {'ev': 'say', 'msg': '[02:01:05] app name: Game'},
                  {'ev': 'stage_end', 'k': 1, 'name': 'prepare Xcode', 'ok': True, 'summary': '', 'elapsed': 6.2},
                  {'ev': 'result', 'kind': 'xcode', 'platform': 'visionos', 'project': '/x/xcode-visionos/DmIOS.xcodeproj', 'bundle_id': 'com.example.game.visionos'}]

        class Proc:
            stdout = io.StringIO(''.join(json.dumps(e) + '\n' for e in events)); returncode = 0
            def wait(self): return 0

        out = io.StringIO()
        with patch.object(deploy.subprocess, 'Popen', return_value=Proc()) as popen, patch('sys.stdout', out):
            res = deploy.prepare(Path('/games/Game'), 'visionos', '/k/prod.keys', 'ABCDEFGHIJ', 'com.example', ['--language', 'ja'])
        self.assertEqual(res['bundle_id'], 'com.example.game.visionos')
        cmd = popen.call_args.args[0]
        for want in (['--romfs', 'separate'], ['--progress', 'json'], ['--platform', 'visionos'], ['-k', '/k/prod.keys'], ['--team-id', 'ABCDEFGHIJ'],
                     ['--bundle-prefix', 'com.example'], ['--language', 'ja']):
            self.assertTrue(any(cmd[i:i + len(want)] == want for i in range(len(cmd))), f'{want} not in {cmd}')
        self.assertNotIn('--prepare-xcode', cmd)   # --platform visionos implies it
        text = out.getvalue()
        self.assertIn('app name: Game', text)
        self.assertNotIn('push_romfs.sh', text); self.assertNotIn('choose the game scheme', text); self.assertNotIn('not in this build', text)

    def test_mac_builds_the_mac_app(self):
        class Proc:
            stdout = io.StringIO(json.dumps({'ev': 'result', 'kind': 'mac-app', 'app': '/games/Game/Game.app', 'log': '/logs/0100.log'}) + '\n'); returncode = 0
            def wait(self): return 0

        with patch.object(deploy.subprocess, 'Popen', return_value=Proc()) as popen, patch('sys.stdout', io.StringIO()):
            res = deploy.prepare(Path('/games/Game'), 'mac', None, 'ABCDEFGHIJ', None, [])
        cmd = popen.call_args.args[0]
        self.assertEqual(res['kind'], 'mac-app')
        self.assertIn('mac-app', cmd); self.assertNotIn('--romfs', cmd); self.assertNotIn('--team-id', cmd)   # the Mac app carries its game files and is signed ad-hoc

    def test_signing_failures_say_what_to_do(self):
        log = ('/x/DmIOS.xcodeproj: error: Failed Registering Bundle Identifier: The app identifier "org.nxnative.0100000000010000" cannot be registered to your development team '
               'because it is not available.\n** BUILD FAILED **\n')
        with tempfile.TemporaryDirectory() as d, patch.object(deploy, 'scheme_of', return_value='Game (0100)'), \
             patch.object(deploy, 'run', return_value=(65, log)) as run:
            res = {'platform': 'ios', 'project': '/x/DmIOS.xcodeproj', 'derived_data': d, 'team_id': 'ABCDEFGHIJ'}
            with self.assertRaises(deploy.DeployError) as e:
                deploy.xcode_build(res, {'udid': 'UDID'}, Path(d) / 'xcodebuild.log')
        self.assertIn('--bundle-prefix', str(e.exception))
        cmd = run.call_args.args[0]
        self.assertIn('-allowProvisioningUpdates', cmd); self.assertIn('id=UDID', cmd); self.assertIn('DEVELOPMENT_TEAM=ABCDEFGHIJ', cmd)

    def test_built_app_is_found_in_the_platforms_products(self):
        with tempfile.TemporaryDirectory() as d, patch.object(deploy, 'scheme_of', return_value='Game'), patch.object(deploy, 'run', return_value=(0, '')):
            (Path(d) / 'Build/Products/Release-xros/DmIOS.app').mkdir(parents=True)
            (Path(d) / 'Build/Products/Release-iphoneos/DmIOS.app').mkdir(parents=True)
            app = deploy.xcode_build({'platform': 'visionos', 'project': 'p', 'derived_data': d}, {'udid': 'U'}, Path(d) / 'log')
        self.assertEqual(app.parent.name, 'Release-xros')


if __name__ == '__main__':
    unittest.main()
