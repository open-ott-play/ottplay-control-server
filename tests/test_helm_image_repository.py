"""Real Helm renders accept private registry ports without accepting URL syntax."""
import json
from pathlib import Path
import re
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / 'charts/ottplay-control-server'


class PortRangeTest(unittest.TestCase):
    def test_every_canonical_tcp_port_obeys_the_range(self):
        schema = json.loads((CHART / 'values.schema.json').read_text())
        expression = re.compile(schema['properties']['image']['properties']['repository']['pattern'])
        for port in range(65537):
            self.assertEqual(bool(expression.fullmatch(f'registry.example:{port}/team/image')),
                             1 <= port <= 65535, port)
        for port in ('0001', '-1', '+1', '1.0', '1e3', '655350', '999999999999'):
            self.assertIsNone(expression.fullmatch(f'registry.example:{port}/team/image'), port)


@unittest.skipUnless(shutil.which('helm'), 'Helm is required to validate rendered image references')
class RepositorySchemaTest(unittest.TestCase):
    def render(self, repository):
        return subprocess.run(
            ['helm', 'template', 'control', str(CHART), '--values', '-'],
            input=json.dumps({'image': {'repository': repository, 'tag': 'v0.1.0'}}),
            text=True, capture_output=True, timeout=15, check=False)

    def test_valid_repositories_and_registry_ports_reach_rendered_image(self):
        for repository in (
                'busybox', 'library/busybox', 'ghcr.io/open-ott-play/ottplay-control-server',
                'localhost:5050/ottplay-control-server',
                '192.168.167.25:5050/ottplay-control-server',
                'registry-1.example:1/team/image__worker',
                'registry.example:65535/team/image--worker',
                'localhost:5000/image.with_underscores'):
            with self.subTest(repository=repository):
                result = self.render(repository)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(f'image: "{repository}:v0.1.0"', result.stdout)

    def test_url_credentials_tags_junk_and_out_of_range_ports_are_rejected(self):
        for repository in (
                '', 'http://registry.example/image', 'https://registry.example/image',
                'user:password@registry.example/image', 'registry.example/image?token=secret',
                'registry.example/image#fragment', 'registry.example/image:v1',
                'registry.example/image@sha256:' + 'a' * 64,
                'registry.example:0/image', 'registry.example:65536/image',
                'registry.example:999999/image', 'registry.example:0001/image',
                'registry.example:/image', 'registry.example:5050',
                'registry.example:5050/', 'registry_bad.example:5050/image',
                '/image', 'image/', 'team//image', 'team/../image', 'image.',
                ' image', 'image ', 'image\n', 'image\r', 'image\t'):
            with self.subTest(repository=repository):
                result = self.render(repository)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('/image/repository', result.stderr)


if __name__ == '__main__':
    unittest.main()
