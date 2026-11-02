"""Cancel public guide reads as well as controller requests in player groups."""
import contextlib
import importlib.util
import io
import json
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


spec = importlib.util.spec_from_file_location(
    'ott_group_epg_cancel', Path(__file__).resolve().parents[1] / 'cli' / 'ott.py')
ott = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ott)
SETTINGS = {'url': 'https://guide.invalid/epg/v1', 'source': 'epg-one'}


class GroupEpgCancellationTest(unittest.TestCase):
    def test_group_interrupt_cancels_inflight_public_guide_reads(self):
        client = ott.Client.__new__(ott.Client)
        client.config = {'players': {'t1': 'dev_1', 't2': 'dev_2'}, 'epg': SETTINGS}
        client.credentials = {}
        client.timeout = 45
        client.on_receipt = None
        entered = threading.Barrier(3, timeout=5)
        retired = [threading.Event(), threading.Event()]
        local_stops, controller_calls = [], []
        lock = threading.Lock()
        interrupted = False
        original_wait = ott.wait

        def guide_read(epg, body, deadline, cancelled):
            with lock:
                index = len(local_stops)
                local_stops.append(cancelled)
            try:
                entered.wait()
                if not cancelled.wait(5):
                    raise AssertionError('guide transport was not cancelled')
                raise ott.EpgTransportError('cancelled test read')
            finally:
                retired[index].set()

        def catalog(local, device, action, params):
            with lock:
                controller_calls.append((device, action, params))
            return {'catalog': 'snapshot-1', 'channels': [
                {'id': 'one', 'number': 1, 'name': 'Cartoons',
                 'tvgId': '', 'tvgName': '', 'shift': 0}]}

        def interrupt_when_reading(*args, **kwargs):
            nonlocal interrupted
            if not interrupted:
                entered.wait()
                interrupted = True
                raise KeyboardInterrupt
            return original_wait(*args, **kwargs)

        args = SimpleNamespace(words=['tt', 'p', '-l', 'Cats'], json=True,
                               timeout=45, config='/unused/config.json', refresh=False)
        output = io.StringIO()
        with mock.patch.object(ott.Client, 'call', catalog), \
                mock.patch.object(ott.EpgClient, '_current', guide_read), \
                mock.patch.object(ott, 'wait', side_effect=interrupt_when_reading), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            code = ott.player_group_command(client, client.config,
                                            [('t1', 'dev_1'), ('t2', 'dev_2')], args, [])
        self.assertTrue(interrupted)
        self.assertEqual(code, 130)
        self.assertTrue(all(event.is_set() for event in local_stops))
        self.assertTrue(all(event.wait(5) for event in retired))
        self.assertEqual(sorted(controller_calls), [('dev_1', 'epg_catalog', {}),
                                                    ('dev_2', 'epg_catalog', {})])
        result = json.loads(output.getvalue())
        self.assertEqual([row['exit_code'] for row in result['results']], [130, 130])

    def test_cancelled_guide_does_not_start_transport(self):
        epg = ott.EpgClient(SETTINGS, 45)
        epg.group_cancelled = threading.Event()
        epg.group_cancelled.set()
        with mock.patch.object(epg, '_current') as transport, self.assertRaises(KeyboardInterrupt):
            epg.current([], 'Cats')
        transport.assert_not_called()


if __name__ == '__main__':
    unittest.main()
