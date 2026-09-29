import argparse
import asyncio
import ast
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parent.parent


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


vps = load('vps', 'deploy/vps.py')
recovery = load('recovery', 'deploy/restore.py')


class DeploymentTests(unittest.TestCase):
    def test_bundle_excludes_private_and_unlisted_files_and_hashes_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in vps.FILES:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('test source: ' + name)
            for name in ('.env', 'SOUL.md', 'deploy/private.py', 'recipes/credentials.md'):
                (root / name).write_text('SECRET THAT MUST NOT SHIP')
            with tarfile.open(fileobj=io.BytesIO(vps.bundle(root)), mode='r:gz') as archive:
                self.assertEqual(set(archive.getnames()), set(vps.FILES) | {'release-manifest.json'})
                manifest = json.load(archive.extractfile('release-manifest.json'))
                for name in vps.FILES:
                    content = archive.extractfile(name).read()
                    self.assertNotIn(b'SECRET', content)
                    self.assertEqual(hashlib.sha256(content).hexdigest(), manifest[name])
            (root / 'bot.py').unlink()
            (root / 'bot.py').symlink_to(root / '.env')
            with self.assertRaises(ValueError):
                vps.bundle(root)

    def test_host_and_timezone_validation(self):
        self.assertEqual(vps.validate_host('root@192.0.2.10'), 'root@192.0.2.10')
        for value in ('-oProxyCommand=evil', 'host; touch /tmp/evil', '$(whoami)', 'a\nb'):
            with self.assertRaises(argparse.ArgumentTypeError):
                vps.validate_host(value)
        for value in ('../../etc/passwd', 'Not/AZone'):
            with self.assertRaises(argparse.ArgumentTypeError):
                vps.validate_timezone(value)
        self.assertEqual(vps.validate_timezone('Europe/Lisbon'), 'Europe/Lisbon')

    def test_preflight_failure_prevents_upload(self):
        with patch.object(vps, 'ssh', side_effect=subprocess.CalledProcessError(1, 'ssh')) as ssh:
            with self.assertRaises(subprocess.CalledProcessError):
                vps.prepare(SimpleNamespace(host='new-vm', timezone='Europe/Lisbon'))
            self.assertEqual(ssh.call_count, 1)
            self.assertEqual(ssh.call_args.args[1], 'bash -s')
            self.assertIn(b'/opt/ken', ssh.call_args.kwargs['data'])

    def test_failed_bootstrap_cleans_staging_and_does_not_onboard(self):
        calls = []
        def ssh(host, command, **kwargs):
            calls.append(command)
            if command.startswith('mktemp'):
                return SimpleNamespace(stdout=b'/tmp/ken-deploy.ABCD1234\n')
            if '/deploy/bootstrap.sh' in command:
                raise subprocess.CalledProcessError(1, 'bootstrap')
            return SimpleNamespace(stdout=b'')
        with patch.object(vps, 'ssh', side_effect=ssh):
            with self.assertRaises(subprocess.CalledProcessError):
                vps.prepare(SimpleNamespace(host='new-vm', timezone='Europe/Lisbon'))
        self.assertEqual(calls[-1], 'rm -rf -- /tmp/ken-deploy.ABCD1234')
        self.assertFalse(any('install.sh' in c for c in calls))

    def test_root_check_executes_operator_script(self):
        with patch.object(vps, 'ssh') as ssh:
            vps.check(SimpleNamespace(host='new-vm'))
        self.assertEqual(ssh.call_args.args, ('new-vm', 'bash -s'))
        self.assertEqual(ssh.call_args.kwargs['data'], (ROOT / 'deploy/check.sh').read_bytes())

    def test_failed_backup_leaves_no_archive(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / 'backup.tar.gz'
            with patch.object(vps.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, 'ssh')):
                with self.assertRaises(subprocess.CalledProcessError):
                    vps.backup(SimpleNamespace(host='new-vm', output=str(target)))
            self.assertEqual(list(Path(temp).iterdir()), [])

    def test_private_backup_and_restore_roundtrip(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'home'
            (source / '.ken/work').mkdir(parents=True)
            (source / '.ken/.env').write_text('ANTHROPIC_API_KEY=test-only\n')
            (source / '.ken/work/SOUL.md').write_text('Remember the pilot')
            (source / '.ken/jobs.json').write_text('[{"time":"07:00"}]')
            (source / '.local/bin').mkdir(parents=True)
            (source / '.local/tool').write_text('binary fixture')
            (source / '.local/bin/tool').symlink_to('/home/ken/.local/tool')
            payload = io.BytesIO()
            with tarfile.open(fileobj=payload, mode='w:gz') as archive:
                archive.add(source, arcname='.')
            def run(*args, **kwargs):
                kwargs['stdout'].write(payload.getvalue())
                return SimpleNamespace(returncode=0)
            target = root / 'backup.tar.gz'
            with patch.object(vps.subprocess, 'run', side_effect=run):
                vps.backup(SimpleNamespace(host='new-vm', output=str(target)))
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            restored = recovery.restore(target, root / 'restored')
            self.assertEqual(restored.stat().st_mode & 0o777, 0o700)
            self.assertEqual((restored / '.ken/work/SOUL.md').read_text(), 'Remember the pilot')
            self.assertEqual((restored / '.local/bin/tool').read_text(), 'binary fixture')
            self.assertEqual((restored / '.ken/.env').stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                recovery.restore(target, restored)

    def test_restore_rejects_traversal_and_external_links(self):
        for name, link in (('../escaped', None), ('/escaped', None), ('link', '/etc/passwd')):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / 'bad.tar.gz'
                with tarfile.open(path, 'w:gz') as archive:
                    info = tarfile.TarInfo(name)
                    if link:
                        info.type, info.linkname = tarfile.SYMTYPE, link
                    archive.addfile(info)
                with self.assertRaises((ValueError, tarfile.FilterError)):
                    recovery.restore(path, Path(temp) / 'restored')
                self.assertFalse((Path(temp) / 'escaped').exists())

    def test_pairing_ignores_other_users_and_group_messages(self):
        script = (ROOT / 'install.sh').read_text()
        # Source function definitions without running the installer or changing this machine.
        script = script.rsplit('\nmain\n', 1)[0]
        messages = [
            {'message': {'text': 'ken-test-code', 'chat': {'type': 'private'}, 'from': {'id': 42}}},
            {'message': {'text': 'ken-test-code', 'chat': {'type': 'group'}, 'from': {'id': 99}}},
            {'message': {'text': 'hello', 'chat': {'type': 'private'}, 'from': {'id': 100}}},
        ]
        result = subprocess.run(['bash', '-c', script + '\nPAIRING_CODE=ken-test-code\nlast_sender id'],
                                input=json.dumps({'result': messages}), text=True, capture_output=True, check=True)
        self.assertEqual(result.stdout.strip(), '42')


class ModelAuthenticationTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_list_uses_selected_credentials(self):
        # Load just the I/O boundary to avoid starting the bot or loading real credentials.
        tree = ast.parse((ROOT / 'bot.py').read_text())
        node = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'refresh_models_file')
        for api_key in ('test-api', ''):
            with self.subTest(api=bool(api_key)), tempfile.TemporaryDirectory() as temp:
                client = AsyncMock()
                client.__aenter__.return_value = client
                response = Mock()
                response.json.return_value = {'data': [{'id': 'test-model'}]}
                client.get.return_value = response
                oauth = Mock(return_value='test-oauth')
                namespace = dict(os=SimpleNamespace(environ={'ANTHROPIC_API_KEY': api_key}),
                                 asyncio=asyncio, get_oauth_token=oauth, MODELS_FILE=Path(temp) / 'models',
                                 httpx=SimpleNamespace(AsyncClient=lambda: client), log=Mock())
                exec(compile(ast.Module(body=[node], type_ignores=[]), 'bot.py', 'exec'), namespace)
                await namespace['refresh_models_file']()
                headers = client.get.call_args.kwargs['headers']
                if api_key:
                    self.assertEqual(headers['x-api-key'], api_key)
                    self.assertNotIn('Authorization', headers)
                    oauth.assert_not_called()
                else:
                    self.assertEqual(headers['Authorization'], 'Bearer test-oauth')
                    self.assertNotIn('x-api-key', headers)
                self.assertEqual(namespace['MODELS_FILE'].read_text(), 'test-model\n')


if __name__ == '__main__':
    unittest.main()
