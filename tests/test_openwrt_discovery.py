import importlib.util
from pathlib import Path
import struct
import unittest

spec = importlib.util.spec_from_file_location('openwrt_discovery', Path(__file__).resolve().parents[1] / 'scripts/openwrt-discovery.py')
discovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(discovery)


class OpenWrtDiscoveryTest(unittest.TestCase):
    def test_standard_wire_records_preserve_scheme_port_and_path(self):
        rows = discovery.records('home.arpa.', 'https://controller.example:9443/control/', 'LivingRoom')
        self.assertEqual([row[2] for row in rows], [12, 33, 16])
        self.assertEqual(rows[0][1], '_ottplay-ctrl._tcp.home.arpa')
        self.assertEqual(rows[0][3], discovery.dns_name('livingroom._ottplay-ctrl._tcp.home.arpa'))
        self.assertEqual(rows[1][3][:6], struct.pack('!HHH', 0, 0, 9443))
        self.assertEqual(rows[1][3][6:], discovery.dns_name('controller.example'))
        txt, fields = rows[2][3], []
        while txt:
            fields.append(txt[1:1 + txt[0]].decode())
            txt = txt[1 + txt[0]:]
        self.assertEqual(fields, ['txtvers=1', 'scheme=https', 'path=/control'])

    def test_private_credentials_and_invalid_dns_values_are_rejected(self):
        for address in ['http://controller.example', 'https://user:secret@controller.example',
                        'https://controller.example/?token=secret', 'https://controller.example/#token',
                        'https://controller.example/../admin', 'https://controller.example/%0aattack',
                        'https://controller.example/;touch file', 'https://controller.example:99999']:
            with self.subTest(address=address), self.assertRaises(ValueError):
                discovery.records('home.arpa', address)
        for domain in ['local', 'home.local', 'home.arpa\nset dhcp.test=host', '-bad.example', 'x' * 64 + '.test']:
            with self.subTest(domain=domain), self.assertRaises(ValueError):
                discovery.records(domain, 'https://controller.example')

    def test_installer_scopes_changes_and_checks_drift(self):
        script = discovery.install_script(discovery.records('home.arpa', 'https://controller.example'))
        self.assertEqual(script.count('=dnsrr\n'), 3)
        self.assertIn('dnsmasq --test', script)
        self.assertIn('DHCP configuration changed concurrently', script)
        self.assertIn('New pending DHCP edits appeared', script)
        self.assertNotIn('uci commit dhcp', script)
        self.assertIn('cp "$backup" /etc/config/dhcp', script)


if __name__ == '__main__':
    unittest.main()
