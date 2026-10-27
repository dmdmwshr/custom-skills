"""离线行为测试；设置 WPS_TEST_ROOT 为本任务已批准的仓外测试目录。"""
import concurrent.futures
import copy
import hashlib
import importlib.util
import io
import json
import ntpath
import os
import re
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid
import zipfile

sys.dont_write_bytecode = True
SOURCE = Path(__file__).resolve().parents[1] / 'scripts/wps_cli.py'
spec = importlib.util.spec_from_file_location('wps_cli_under_test', SOURCE)
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)
REAL_POPEN = subprocess.Popen
ORIGINAL_OBSERVE = w.observe_binary

FAKE = r'''
import json,ntpath,os,pathlib,subprocess,sys,time,zipfile
mode,argv,marker=sys.argv[1],json.loads(sys.argv[2]),sys.argv[3]
with open(marker,'a',encoding='utf-8') as f:f.write('spawn\n')
cmd=argv[1];inputs=[]
for a in argv[2:]:
 if a.startswith('--'):break
 inputs.append(a)
if mode.startswith('native-names'):
 secret=argv[argv.index('--password')+1]
 out=pathlib.Path(argv[argv.index('--output')+1])
 folder=out.parent/(secret+'-native');folder.mkdir()
 print(json.dumps({'type':'started','input':ntpath.basename(inputs[0])}),flush=True)
 if cmd=='pdf2md':
  target=folder/(secret+'-document.md')
  (folder/(secret+'-image.png')).write_bytes(b'synthetic-resource')
  target.write_text('![figure]('+secret+'-image.png)\n',encoding='utf-8')
 elif cmd=='pdf2cad':
  target=folder/(secret+'-archive.zip')
  with zipfile.ZipFile(target,'w') as z:z.writestr(secret+'/layer.dxf','synthetic entry')
 else:
  target=folder/(secret+'-actual.pdf');target.write_bytes(b'fixture-pdf')
 (out.parent/(secret+'-unbound.txt')).write_bytes(b'unbound-fixture')
 print(json.dumps({'type':'completed','status':'success','output':str(target),'echo':argv}),flush=True)
 if mode.endswith('nonzero'):sys.stderr.write(json.dumps(argv));sys.exit(214)
 if mode.endswith('timeout'):time.sleep(60)
 sys.exit(0)
if mode=='grandchild':
 child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 print(json.dumps({'type':'fixture-child','pid':child.pid}),flush=True)
 time.sleep(60)
if mode=='timeout':
 print(json.dumps({'type':'started','input':ntpath.basename(inputs[0])}),flush=True)
 print(json.dumps({'type':'progress','echo':argv}),flush=True)
 sys.stderr.write(json.dumps(argv));sys.stderr.flush();time.sleep(60)
if mode=='slow':time.sleep(.3)
if cmd=='pdfinfo':
 for p in inputs: print(json.dumps({'type':'completed','file':p,'page_count':2,'is_scan_document':False}))
 sys.exit(0)
out=pathlib.Path(argv[argv.index('--output')+1])
merged=cmd=='pdfmerge'
count=1 if merged else len(inputs)
for i in range(count):
 print(json.dumps({'type':'started',**({} if merged else {'input':ntpath.basename(inputs[i])})}),flush=True)
 if mode=='partial' and i==count-1:
  print(json.dumps({'type':'error','code':214}));sys.exit(1)
 target=out/(str(i)+'.pdf') if out.is_dir() else out
 if mode=='rename':target=target.with_stem(target.stem+'(1)')
 if mode!='missing':target.write_bytes(b'fixture-pdf')
 if mode=='mutate':pathlib.Path(inputs[0]).write_bytes(b'changed-fixture')
 print(json.dumps({'type':'completed','status':'success','output':str(target),'echo':argv}))
if mode=='nonzero':sys.stderr.write(json.dumps(argv));sys.exit(214)
'''


class Harness(unittest.TestCase):
    def setUp(self):
        root = os.environ.get('WPS_TEST_ROOT')
        if not root:
            raise RuntimeError('WPS_TEST_ROOT must name an authorized external test root')
        Path(root).mkdir(parents=True, exist_ok=True)
        self.base = Path(tempfile.mkdtemp(prefix='wps-offline-', dir=root))
        self.drive = self.base / 't'
        for name in ('in', 'work', 'bin'):
            (self.drive / name).mkdir(parents=True)
        self.binary = self.drive / 'bin/wpscli.exe';self.binary.write_bytes(b'INERT OFFLINE FIXTURE')
        self.program = self.base / 'fixture.py';self.program.write_text(FAKE, encoding='utf-8')
        self.marker = self.base / 'spawn-count.txt'
        self.mode = 'success';self.calls = []
        self.patches = [patch.object(w, 'require_windows', lambda: None),
                        patch.object(w, 'winpath', self.resolve),
                        patch.object(w, 'state_root', lambda: self.base / 'state'),
                        patch.object(w, 'observe_binary', self.observe),
                        patch.object(w.subprocess, 'Popen', self.spawn)]
        for p in self.patches:
            p.start();self.addCleanup(p.stop)
        # Deliberately retain all fixture/unknown evidence. Only live self-created processes are cleaned.

    def resolve(self, value):
        value = str(value)
        if value.startswith(str(self.base)):
            return Path(value).resolve()
        n = w.windows_syntax(value)
        return (self.base / n[0].lower() / n[3:].replace('\\', '/')).resolve()

    def observe(self, binary, commands, cwd):
        return {'path': str(binary), 'size': binary.stat().st_size, 'sha256': w.sha_file(binary),
                'version': 'wpscli 1.1.0 (inert test adapter)', 'wrapperSha256': w.sha_file(SOURCE),
                'help': {c: w.CATALOG[c]['helpSha256'] for c in commands}}

    def spawn(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        return REAL_POPEN([sys.executable, '-I', '-B', str(self.program), self.mode,
                           json.dumps(argv), str(self.marker)], **kwargs)

    def request(self, command='word2pdf', count=1):
        family = 'pdf' if command.startswith('pdf') else next(k for k in w.EXTENSIONS if command.startswith(k))
        ext = {'word': 'docx', 'excel': 'xlsx', 'ppt': 'pptx', 'pdf': 'pdf',
               'photo': 'png', 'txt': 'txt', 'cad': 'dxf', 'caj': 'caj'}[family]
        inputs = ['T:/in/中文 样本' + str(i) + '.' + ext for i in range(count)]
        for p in inputs:
            self.resolve(p).write_bytes(b'synthetic-input')
        directory = command in {'pdfsplit', 'pdf2photo', 'word2photo', 'excel2photo', 'ppt2photo'} or count > 1 and command != 'pdfmerge'
        run_id = 'fixture-' + uuid.uuid4().hex
        scope = 'T:/work/' + run_id
        suffix = w.DEFAULT_SUFFIX.get(command, 'pdf')
        req = {'schemaVersion': 1, 'runId': run_id, 'command': command, 'inputs': inputs,
               'outputKind': 'directory' if directory else 'file',
               'output': scope if directory else scope + '/new.' + suffix,
               'evidenceDir': 'T:/work/receipt-' + run_id, 'wpscliPath': 'T:/bin/wpscli.exe', 'options': {}}
        if command == 'pdfinfo':
            req.pop('output');req['outputKind'] = 'none'
        if command in {'pdfsplit', 'pdf2cad'}:
            req['options']['range'] = ';'.join('1-2' for _ in inputs)
        if command == 'pdfwatermark':
            req['options']['text'] = '中文; literal # watermark'
        if command == 'pdfencrypt':
            req['openPassword'] = 'P-' + uuid.uuid4().hex
        if command in w.PHOTOS:
            req['cloudProcessingAuthorized'] = True;req['cloudAuthorizationBasis'] = 'explicit synthetic fixture only'
            req['authorizedInputHashes'] = {p: w.sha_file(self.resolve(p)) for p in inputs}
        return req

    def assert_no_password(self, secret, result, request=None):
        serialized = w.json_bytes(result)
        forbidden = [secret.encode(), secret.encode('utf-16le'), json.dumps(secret)[1:-1].encode(),
                     hashlib.sha256(secret.encode()).hexdigest().encode(), b'"requestDigest"']
        if request is not None:
            forbidden.append(hashlib.sha256(w.json_bytes(request)).hexdigest().encode())
        for raw in [serialized] + [p.read_bytes() for p in self.base.rglob('*') if p.is_file()]:
            for token in forbidden:
                self.assertNotIn(token, raw)

    def main_call(self, request):
        output = io.BytesIO()
        with patch.object(w.sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(w.json_bytes(request)))), \
                patch.object(w.sys, 'stdout', SimpleNamespace(buffer=output)):
            code = w.main(['run'])
        self.last_stdout = output.getvalue()
        return code, json.loads(self.last_stdout)

    def test_native_name_sinks_redacted_without_changing_files_or_prefix(self):
        long_secret = 'R2秘密_' + uuid.uuid4().hex
        cases = [(command, secret, 'native-names') for command in ('word2pdf', 'pdf2md', 'pdf2cad')
                 for secret in (long_secret, '0', 'a')]
        cases += [('word2pdf', long_secret, 'native-names-' + suffix) for suffix in ('nonzero', 'timeout')]
        for command, secret, mode in cases:
            with self.subTest(command=command, secretKind='short' if len(secret) == 1 else 'long', mode=mode):
                r = self.request(command);r['password'] = secret
                r['output'] = r['output'].replace('/work/', '/work/public-a0-')
                scope = self.resolve(ntpath.dirname(r['output']));self.mode = mode
                if mode.endswith('timeout'):r['parentTimeoutSeconds'] = 1
                expected_status = 'completed' if mode == 'native-names' else 'unknown'
                before = len(self.calls);code, first = self.main_call(r)
                self.assertEqual((code, first['status']), (0 if expected_status == 'completed' else 3, expected_status))
                self.assertEqual(first['requestIdentity'], w.request_identity(r))
                self.assertEqual(first['loadedConfiguration']['outputScope'], str(scope))
                self.assertTrue(first['nativeNamesRedacted']);self.assertTrue(first['unboundFilesRedacted'])
                actual = {w.sha_file(p): p for p in scope.rglob('*') if p.is_file()}
                original_bytes = {p: p.read_bytes() for p in actual.values()}
                for info in first['artifacts']:
                    self.assertIn(info['sha256'], actual)
                    for item in [info, *info.get('resources', [])]:
                        self.assertTrue(item['pathRedacted']);self.assertTrue(item['path'].startswith(str(scope) + os.sep))
                        self.assertNotIn(secret, item['path'][len(str(scope)):])
                        self.assertIn('[REDACTED]', item['path']);self.assertIn(item['sha256'], actual)
                        self.assertEqual(item['size'], actual[item['sha256']].stat().st_size)
                    for entry in info.get('zipEntries', []):
                        self.assertTrue(entry['nameRedacted']);self.assertNotIn(secret, entry['name'])
                        with zipfile.ZipFile(actual[info['sha256']]) as z:
                            native_entry = z.infolist()[0]
                            self.assertIn(secret, native_entry.filename)
                            self.assertEqual((entry['size'], entry['crc']), (native_entry.file_size, native_entry.CRC))
                for name in first['unboundFiles']:
                    self.assertTrue(name.startswith(str(scope) + os.sep));self.assertNotIn(secret, name[len(str(scope)):])
                event_output = first['commands'][0]['completed']['output']
                self.assertTrue(event_output.startswith(str(scope) + os.sep))
                self.assertNotIn(secret, event_output[len(str(scope)):])
                self.assertEqual(first['nativeEvents'][0]['event']['input'], ntpath.basename(r['inputs'][0]))
                run_dir = w.state_root() / r['runId']
                persisted = [run_dir / n for n in ('intent.json', 'launched.json', 'result.json')]
                receipt = self.resolve(r['evidenceDir']) / 'receipt.json';persisted.append(receipt)
                self.assertEqual((run_dir / 'result.json').read_bytes(), self.last_stdout)
                self.assertEqual(receipt.read_bytes(), self.last_stdout)
                if len(secret) > 1:
                    for raw in [self.last_stdout, *(p.read_bytes() for p in persisted)]:
                        self.assertNotIn(secret.encode(), raw)
                        self.assertNotIn(json.dumps(secret)[1:-1].encode(), raw)
                        self.assertNotIn(hashlib.sha256(w.json_bytes(r)).hexdigest().encode(), raw)
                baseline = {p: p.read_bytes() for p in persisted}
                changed = dict(r, password='changed-' + uuid.uuid4().hex)
                code, second = self.main_call(changed)
                self.assertEqual(second['requestIdentity'], first['requestIdentity'])
                self.assertEqual(second['artifacts'], first['artifacts'])
                self.assertEqual(second['unboundFiles'], first['unboundFiles'])
                self.assertEqual(second['status'], expected_status)
                self.assertTrue(second['existingRun']);self.assertIs(second['replayed'], False)
                self.assertEqual(len(self.calls), before + 1)
                self.assertEqual({p: p.read_bytes() for p in persisted}, baseline)
                self.assertEqual({p: p.read_bytes() for p in original_bytes}, original_bytes)

    def test_request_given_paths_remain_exact_even_with_secret_collision(self):
        for secret in ('0', 'a', 'PublicPathCollision'):
            r = self.request();r['password'] = secret
            r['output'] = 'T:/work/' + secret + '-' + r['runId'] + '/' + secret + '.pdf'
            self.mode = 'success';code, result = self.main_call(r)
            self.assertEqual((code, result['status']), (0, 'completed'))
            actual = str(self.resolve(r['output']))
            self.assertEqual(result['artifacts'][0]['path'], actual)
            self.assertEqual(result['commands'][0]['completed']['output'], actual)
            self.assertNotIn('pathRedacted', result['artifacts'][0])
            self.assertNotIn('nativeNamesRedacted', result)

    def test_pre_name_view_schema2_readback_is_safe_without_rewriting(self):
        r = self.request();old_secret = 'old-native-' + uuid.uuid4().hex;r['password'] = old_secret
        identity = w.request_identity(r);run_dir = w.state_root() / r['runId'];run_dir.mkdir(parents=True)
        (run_dir / 'intent.json').write_bytes(w.json_bytes({'schemaVersion': 2, 'requestIdentity': identity}))
        (run_dir / 'result.json').write_bytes(w.json_bytes(
            {'schemaVersion': 2, 'requestIdentity': identity, 'status': 'completed',
             'artifacts': [{'path': 'T:/work/' + old_secret + '.pdf'}], 'unboundFiles': [old_secret]}))
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in run_dir.iterdir()}
        for secret in (old_secret, 'changed-' + uuid.uuid4().hex):
            code, out = self.main_call(dict(r, password=secret))
            self.assertEqual((code, out['status']), (3, 'unknown'));self.assertTrue(out['preNameViewRecord'])
            self.assertTrue(out['existingRun']);self.assertIs(out['replayed'], False)
            self.assertEqual(out['requestIdentity'], identity)
            self.assertNotIn(old_secret.encode(), self.last_stdout)
            self.assertNotIn('artifacts', out);self.assertNotIn('unboundFiles', out)
        self.assertEqual({p: (p.read_bytes(), p.stat().st_mtime_ns) for p in run_dir.iterdir()}, before)
        self.assertEqual(self.calls, [])

    def test_secret_changes_arrays_and_pairs_read_existing_without_spawn(self):
        for command, count in [('pdfwatermark', 1), ('pdf2word', 2), ('pdfencrypt', 1)]:
            with self.subTest(command=command):
                r = self.request(command, count)
                first_secret, second_secret = '秘密_' + uuid.uuid4().hex, 'changed_' + uuid.uuid4().hex
                batch_other = 'batch_' + uuid.uuid4().hex
                r['password'] = [first_secret, batch_other] if count > 1 else first_secret
                if command == 'pdfencrypt':
                    r['openPassword'] = first_secret;r['editPassword'] = first_secret
                before = len(self.calls)
                code, first = self.main_call(r)
                self.assertEqual((code, first['status']), (0, 'completed'))
                self.assertEqual(first['requestIdentity'], w.request_identity(r))
                argv = self.calls[-1][0]
                self.assertEqual(argv[argv.index('--password') + 1], ';'.join(r['password']) if count > 1 else first_secret)
                self.assert_no_password(first_secret, first, r)
                if count > 1:self.assert_no_password(batch_other, first, r)
                changed = copy.deepcopy(r)
                changed['password'] = [second_secret, batch_other] if count > 1 else second_secret
                if command == 'pdfencrypt':
                    changed['openPassword'] = second_secret;changed['editPassword'] = second_secret
                code, second = self.main_call(changed)
                self.assertEqual(code, 0);self.assertEqual(second['requestIdentity'], first['requestIdentity'])
                self.assertEqual(second['artifacts'], first['artifacts'])
                self.assertTrue(second['existingRun']);self.assertIs(second['replayed'], False)
                self.assertEqual(len(self.calls), before + 1)
                self.assert_no_password(second_secret, second, changed)

    def test_secret_identity_conflicts_preserve_record_and_never_spawn(self):
        r = self.request('pdfwatermark');r['password'] = 'source_' + uuid.uuid4().hex
        self.main_call(r)
        baseline = {p: p.read_bytes() for p in self.base.rglob('*.json')}
        for change in ('evidenceDir', 'text', 'presence', 'shape'):
            changed = copy.deepcopy(r)
            if change == 'evidenceDir':changed['evidenceDir'] += '-other'
            elif change == 'text':changed['options']['text'] += '-other'
            elif change == 'presence':changed.pop('password')
            else:changed['password'] = [changed['password']]
            if change in {'evidenceDir', 'text'}:changed['password'] = 'different_' + uuid.uuid4().hex
            code, out = self.main_call(changed)
            self.assertEqual((code, out), (2, {'status': 'rejected', 'reason': 'run_id_request_conflict'}))
            self.assertEqual(len(self.calls), 1)
            self.assertEqual({p: p.read_bytes() for p in self.base.rglob('*.json')}, baseline)
            self.assertFalse(self.resolve(r['evidenceDir'] + '-other').exists())

    def test_legacy_records_readback_only_without_digest_or_rewrite(self):
        r = self.request('pdfencrypt');secret = 'legacy_' + uuid.uuid4().hex;r['openPassword'] = secret
        old_digest = hashlib.sha256(w.json_bytes(r)).hexdigest()
        run_dir = w.state_root() / r['runId'];run_dir.mkdir(parents=True)
        for name, status in [('intent', 'intent'), ('launched', 'launched'), ('result', 'completed')]:
            (run_dir / (name + '.json')).write_bytes(w.json_bytes(
                {'schemaVersion': 1, 'requestDigest': old_digest, 'status': status, 'oldUnsafeEcho': secret}))
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in run_dir.iterdir()}
        for password in (secret, 'another_' + uuid.uuid4().hex):
            r['openPassword'] = password
            code, out = self.main_call(r)
            self.assertEqual((code, out['status']), (3, 'unknown'))
            self.assertTrue(out['legacyRecord']);self.assertEqual(out['legacyResultStatus'], 'completed')
            self.assertIs(out['replayed'], False)
            for token in (old_digest, secret, 'requestDigest'):
                self.assertNotIn(token.encode(), w.json_bytes(out))
        self.assertEqual(self.calls, []);self.assertFalse(self.resolve(r['evidenceDir']).exists())
        self.assertEqual({p: (p.read_bytes(), p.stat().st_mtime_ns) for p in run_dir.iterdir()}, before)

    def test_nested_secret_rejections_have_stable_public_error(self):
        for key, value in [('password', {'nested': ['a', '0']}),
                           ('options', {'password': {'nested': 'a'}}),
                           ('unexpected', {'items': [{'openPassword': ['0', 'a']}]})]:
            r = self.request();r[key] = value
            code, out = self.main_call(r)
            self.assertEqual(code, 2);self.assertEqual(out['status'], 'rejected')
            self.assertIn(out['reason'], {'invalid_string_value', 'option_not_allowed_for_command', 'unknown_request_field'})
            self.assertEqual(set(out), {'status', 'reason'})
        self.assertEqual(self.calls, []);self.assertFalse(w.state_root().exists())

    def test_short_password_main_in_fresh_process_keeps_public_schema(self):
        for secret in ('0', 'a'):
            with self.subTest(secret=secret):
                with REAL_POPEN([sys.executable, '-I', '-B', str(Path(__file__).resolve()), '--secret-fixture-worker'],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as p:
                    stdout, stderr = p.communicate(w.json_bytes({'secret': secret}), timeout=15)
                self.assertEqual(p.returncode, 0, stderr.decode())
                observed = json.loads(stdout)
                self.assertEqual(observed['calls'], 1);self.assertTrue(observed['argvSecretReceived'])
                self.assertEqual(observed['codes'], [0, 0])
                first, second = observed['first'], observed['second']
                self.assertEqual(first['status'], 'completed');self.assertEqual(second['status'], 'completed')
                self.assertTrue(second['existingRun']);self.assertIs(second['replayed'], False)
                identity = first['requestIdentity'];self.assertRegex(identity, r'^[0-9a-f]{64}$')
                self.assertEqual(identity, second['requestIdentity'])
                for record in observed['records']:
                    self.assertEqual(record['schemaVersion'], 2);self.assertEqual(record['requestIdentity'], identity)
                    self.assertNotIn('requestDigest', record)
                    self.assertIn(record['status'], {'intent', 'launched', 'completed'})
                self.assertEqual(first['commands'][0]['status'], 'success')
                self.assertEqual(first['commands'][0]['completed']['status'], 'success')
                self.assertEqual(first['artifacts'][0]['sha256'], hashlib.sha256(b'fixture-pdf').hexdigest())
                self.assertIn('[REDACTED]', str(first['commands'][0]['completed']['echo']))

    def test_concurrent_changed_secrets_claim_only_once(self):
        r = self.request('pdfencrypt');changed = dict(r, openPassword='other_' + uuid.uuid4().hex)
        self.mode = 'slow'
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            rows = list(pool.map(w.run_request, [r, changed]))
        self.assertEqual(len(self.marker.read_text().splitlines()), 1)
        self.assertTrue(any(row['status'] == 'completed' for row in rows))
        self.assertEqual(w.request_identity(r), w.request_identity(changed))
        before = len(self.calls)
        self.assertEqual(w.run_request(changed)['status'], 'completed')
        self.assertEqual(len(self.calls), before)

    def test_catalog_exact_28_and_no_passthrough(self):
        names = {'word2pdf','excel2pdf','ppt2pdf','pdf2word','pdf2excel','pdf2ppt','pdf2txt','pdf2md','pdf2photo',
                 'pdf2imgpdf','pdfcompress','pdfsplit','pdfmerge','pdfwatermark','pdfremovewatermark','pdfencrypt',
                 'pdfinfo','word2photo','excel2photo','ppt2photo','photo2pdf','photo2word','photo2excel','photo2ppt',
                 'txt2pdf','pdf2cad','cad2pdf','caj2pdf'}
        self.assertEqual(set(w.CATALOG), names)
        for bad in ('install', 'feedback', 'login', 'version', 'cmd'):
            r = self.request();r['command'] = bad
            with self.assertRaises(w.Rejected):w.run_request(r)
        self.assertEqual(self.calls, [])

    def test_every_command_per_command_schema(self):
        for command in w.CATALOG:
            with self.subTest(command=command):
                r = self.request(command);self.assertEqual(w.validate_request(r)[0], command)
                r['options']['install'] = True
                with self.assertRaises(w.Rejected):w.run_request(r)
        self.assertEqual(self.calls, [])

    def test_invalid_options_rejected_before_spawn(self):
        cases = [('pdf2word', {'scanned': True}), ('pdf2word', {'scanned': True, 'range': '1', 'engine': 'basic'}),
                 ('pdf2word', {'ai-fix': True}), ('pdf2md', {'scanned': True}),
                 ('pdf2excel', {'suffix':'csv','multi-sheet':True}), ('ppt2pdf', {'handout-layout':5}),
                 ('ppt2pdf', {'output-type':'handouts','handout-layout':3,'handout-order':'vertical'}),
                 ('word2pdf', {'image-quality':'best'}), ('word2photo', {'dpi':601}),
                 ('pdfcompress', {'color-img-dest-ppi':8}), ('pdfcompress', {'optimizer-settings':'unknown'}),
                 ('photo2pdf', {'overwrite':True}), ('txt2pdf', {'page-width':20}),
                 ('pdfwatermark', {'text':'ok','image':'T:/in/watermark.png'}),
                 ('pdfwatermark', {'text-color':'red'}), ('word2pdf', {'timeout':True})]
        for cmd, opts in cases:
            r = self.request(cmd);r['options'] = opts
            with self.subTest(cmd=cmd, opts=opts), self.assertRaises(w.Rejected):w.run_request(r)
        self.assertEqual(self.calls, [])

    def test_range_grammars(self):
        w.validate_ranges('1,3-5', 'excel2pdf', 1)
        w.validate_ranges('1,2;3-4', 'pdfsplit', 2)
        w.validate_ranges('1;2-3', 'pdfmerge', 2)
        for v,c,n in [('1,2','pdfmerge',1),('1,2','pdf2word',1),('1;2','pdf2word',1),('0','word2pdf',1),('4-2','pdfsplit',1)]:
            with self.assertRaises(w.Rejected):w.validate_ranges(v,c,n)

    def test_path_and_password_delimiters(self):
        for p in ('relative.pdf','C:relative.pdf','//host/share/a.pdf','C:/../a.pdf','C:/a.pdf:stream','C:/NUL.pdf',
                  'C:/bad./a.pdf','C:/a?.pdf','C:/a*.pdf','C:/bad /a.pdf','C:/a\n.pdf'):
            with self.subTest(p=p), self.assertRaises(w.Rejected):w.windows_syntax(p)
        r = self.request();r['password'] = 'not;native-safe'
        with self.assertRaises(w.Rejected):w.run_request(r)
        r = self.request();r['inputs'] = ['T:/in/has;delimiter.docx']
        with self.assertRaises(w.Rejected):w.run_request(r)
        self.assertEqual(self.calls, [])

    def test_plain_text_semicolon_exact_argv_and_no_shell(self):
        r = self.request('pdfwatermark')
        r['output'] = r['output'].replace('/new.pdf', '/中文; # new.pdf')
        out = w.run_request(r)
        self.assertEqual(out['status'], 'completed')
        argv, kw = self.calls[0]
        self.assertEqual(argv[argv.index('--text')+1], '中文; literal # watermark')
        self.assertIs(kw['shell'], False);self.assertEqual(kw['stdin'], subprocess.DEVNULL)
        self.assertEqual(kw['cwd'], str(self.resolve(r['evidenceDir'])))
        self.assertEqual(kw.get('creationflags', 0), subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
        self.assertEqual(w.sha_file(self.resolve(r['inputs'][0])), hashlib.sha256(b'synthetic-input').hexdigest())

    def test_replaced_scope_and_watermark_input_protection(self):
        r=self.request('pdfwatermark');r['options']={'image':'T:/in/水印; #.png'}
        image=self.resolve(r['options']['image']);image.write_bytes(b'watermark-image')
        out=w.run_request(r)
        self.assertEqual(out['status'],'completed')
        self.assertEqual(len(out['inputHashesAfter']),2)
        self.assertEqual(image.read_bytes(),b'watermark-image')
        scope=self.base/'root-to-replace';scope.mkdir();other=self.base/'unrelated';other.mkdir()
        (other/'original.pdf').write_bytes(b'protected-neighbor')
        scope.rmdir()
        try:scope.symlink_to(other,target_is_directory=True)
        except OSError:self.skipTest('symlink privilege unavailable; independent Windows junction test required')
        artifacts,errors,_=w.collect_artifacts([{'completed':{'output':str(scope/'original.pdf')}}],scope,'pdfsplit',resolver=Path)
        self.assertEqual(artifacts,[]);self.assertIn('output_scope_identity_changed',errors)

    def test_cloud_auth_before_spawn_and_hash_binding(self):
        r = self.request('photo2word');r['cloudProcessingAuthorized'] = False
        with self.assertRaises(w.Rejected):w.run_request(r)
        r['cloudProcessingAuthorized'] = True;r['authorizedInputHashes'][r['inputs'][0]] = '0'*64
        with self.assertRaises(w.Rejected):w.run_request(r)
        self.assertEqual(self.calls, [])

    def test_default_no_overwrite_and_occupied_scope(self):
        r = self.request();scope = self.resolve(ntpath.dirname(r['output']));scope.mkdir()
        old=scope/'old.pdf';old.write_bytes(b'original')
        with self.assertRaises(w.Rejected):w.run_request(r)
        self.assertEqual(old.read_bytes(),b'original');self.assertEqual(self.calls,[])

    def test_input_output_alias_and_basename_collision(self):
        r = self.request();r['output'] = r['inputs'][0].replace('.docx','.pdf')
        with self.assertRaises(w.Rejected):w.run_request(r)
        r = self.request(count=2);r['inputs'][1] = 'T:/other/中文 样本0.docx'
        with self.assertRaises(w.Rejected):w.validate_request(r)

    def test_auto_renamed_actual_file_and_hash(self):
        r = self.request();self.mode='rename';out=w.run_request(r)
        self.assertEqual(out['status'],'completed');self.assertEqual(len(out['artifacts']),1)
        artifact=out['artifacts'][0];self.assertIn('(1)',artifact['path'])
        self.assertEqual(artifact['sha256'],hashlib.sha256(b'fixture-pdf').hexdigest())
        self.assertFalse(self.resolve(r['output']).exists())

    def test_directory_and_ordered_batch(self):
        r=self.request(count=2);out=w.run_request(r)
        self.assertEqual(out['status'],'completed');self.assertEqual(len(out['artifacts']),2)
        self.assertEqual([x['inputIndices'] for x in out['commands']],[[0],[1]])

    def test_partial_and_nonzero_keep_known_artifact_unknown(self):
        for mode,count in [('partial',2),('nonzero',1),('missing',1)]:
            self.mode=mode;r=self.request(count=count);out=w.run_request(r)
            self.assertEqual(out['status'],'unknown')
            if mode!='missing':self.assertEqual(len(out['artifacts']),1)
            self.assertTrue(out['unknowns'])

    def test_pdfinfo_shape_has_no_output_or_timeout(self):
        r=self.request('pdfinfo');out=w.run_request(r)
        self.assertEqual(out['status'],'completed');self.assertEqual(out['artifacts'],[])
        argv=self.calls[0][0];self.assertNotIn('--output',argv);self.assertNotIn('--timeout',argv)
        self.assertEqual(out['commands'][0]['completed']['page_count'],2)

    def test_replay_same_run_and_changed_evidence_never_spawn(self):
        r=self.request();first=w.run_request(r);second=w.run_request(r)
        self.assertEqual(second['status'],'completed');self.assertIs(second['replayed'],False)
        self.assertEqual(len(self.calls),1)
        r['evidenceDir']+='-different'
        with self.assertRaisesRegex(w.Rejected,'run_id_request_conflict'):w.run_request(r)
        self.assertEqual(len(self.calls),1)

    def test_atomic_claim_concurrent_spawn_once(self):
        r=self.request();self.mode='slow'
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            rows=list(pool.map(w.run_request,[r,dict(r)]))
        self.assertEqual(len(self.marker.read_text().splitlines()),1)
        self.assertTrue(any(x['status']=='completed' for x in rows))

    def test_crash_after_intent_is_not_replayed(self):
        r=self.request()
        with patch.object(w,'observe_binary',side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):w.run_request(r)
        out=w.run_request(r);self.assertEqual(out['status'],'unknown');self.assertEqual(self.calls,[])

    def test_password_success_nonzero_and_timeout_absent_everywhere(self):
        for mode in ('success','nonzero','timeout'):
            self.mode=mode;r=self.request('pdfencrypt');secret='秘密_'+uuid.uuid4().hex
            r['openPassword']=secret;r['password']='src_'+uuid.uuid4().hex
            if mode=='timeout':r['parentTimeoutSeconds']=1
            out=w.run_request(r);self.assert_no_password(secret,out,r);self.assert_no_password(r['password'],out,r)
            self.assertEqual(out['status'],'completed' if mode=='success' else 'unknown')
            # Same timeout run cannot launch again.
            before=len(self.calls);w.run_request(r);self.assertEqual(len(self.calls),before)

    def test_input_mutation_observed(self):
        self.mode='mutate';out=w.run_request(self.request())
        self.assertEqual(out['status'],'unknown');self.assertIn('input_modified',out['unknowns'])

    def test_unexpected_error_after_real_child_effect_is_unknown_without_replay(self):
        r=self.request()
        with patch.object(w,'parse_capture',side_effect=RuntimeError('do not stringify arbitrary process context')):
            out=w.run_request(r)
        self.assertEqual(out['status'],'unknown')
        self.assertTrue(self.resolve(r['output']).is_file())
        before=len(self.calls);again=w.run_request(r)
        self.assertEqual(again['status'],'unknown');self.assertEqual(len(self.calls),before)

    def test_timeout_kills_only_owned_child_not_grandchild(self):
        self.mode='grandchild'
        p,out,err=w.capture_process([str(self.binary),'pdfinfo','fixture.pdf'],self.base,.4)
        self.assertTrue(p['timedOut'])
        child=json.loads(out.decode().splitlines()[0])['pid']
        try:
            if sys.platform=='win32':
                import ctypes
                handle=ctypes.windll.kernel32.OpenProcess(0x1000,False,child)
                self.assertTrue(handle);ctypes.windll.kernel32.CloseHandle(handle)
            else:os.kill(child,0)
        finally:
            os.kill(child,signal.SIGTERM)  # exact fixture-owned PID only

    def test_probe_only_version_and_selected_help_with_drift_guard(self):
        cmd='word2pdf';original=w.CATALOG[cmd]['helpSha256']
        sample=b'Known help\n';w.CATALOG[cmd]['helpSha256']=hashlib.sha256(sample).hexdigest()
        try:
            answers=[({'exitCode':0,'timedOut':False},b'wpscli 1.1.0\n',b''),
                     ({'exitCode':0,'timedOut':False},sample.replace(b'\n',b'\r\n'),b'')]
            with patch.object(w,'capture_process',side_effect=answers) as c:
                # Call source code, not Harness's binary observation adapter.
                original_observe=ORIGINAL_OBSERVE
                result=original_observe(self.binary,[cmd],self.base)
                self.assertEqual(result['help'][cmd],hashlib.sha256(sample).hexdigest())
                self.assertEqual(c.call_args_list[0].args[0],[str(self.binary),'--version'])
                self.assertEqual(c.call_args_list[1].args[0],[str(self.binary),cmd,'--help'])
            with patch.object(w,'capture_process',return_value=({'exitCode':0,'timedOut':False},b'wrong',b'')):
                with self.assertRaises(w.Rejected):original_observe(self.binary,[cmd],self.base)
        finally:w.CATALOG[cmd]['helpSha256']=original


class PureEvidenceTests(unittest.TestCase):
    def test_three_envelopes_jsonl_and_unknown_lines(self):
        native='\r\n'+json.dumps({'type':'started','input':'a.pdf'})+'\r\n\r\n'+json.dumps({'type':'completed','status':'success','output':'T:/new/x.pdf'})+'\r\n'
        for body in [native,json.dumps({'returncode':0,'stdout':native,'stderr':''}),
                     json.dumps({'rc':0,'stdout':native}),json.dumps({'exit':0,'source':'a','target':'b','stdout':native})]:
            events,errors=w.parse_capture(body);self.assertEqual(len(events),2);self.assertEqual(errors,[])
        events,errors=w.parse_capture('not json\n{"type":"future-event"}')
        self.assertEqual(len(events),1);self.assertEqual(len(errors),2)

    def test_event_binding_order_duplicate_names_merge_and_pdfinfo(self):
        events=[{'event':{'type':'started','input':'b.pdf'}},{'event':{'type':'completed','status':'success','output':'T:/new/b.docx'}}]
        rows,errors=w.bind_events(events,'pdf2word',['T:/in/a.pdf']);self.assertTrue(errors)
        events=[{'event':{'type':'started'}},{'event':{'type':'completed','status':'success','output':'T:/new/all.pdf'}}]
        rows,errors=w.bind_events(events,'pdfmerge',['T:/in/a.pdf','T:/in/b.pdf'],True)
        self.assertEqual(errors,[]);self.assertEqual(rows[0]['inputIndices'],[0,1])
        rows,errors=w.bind_events([{'event':{'type':'completed','file':'T:/else/a.pdf','page_count':2,'is_scan_document':False}}],'pdfinfo',['T:/in/a.pdf'])
        self.assertTrue(errors)

    def test_markdown_resources_directory_zip_and_escapes(self):
        root=Path(os.environ['WPS_TEST_ROOT']);root.mkdir(parents=True,exist_ok=True)
        base=Path(tempfile.mkdtemp(prefix='resource-',dir=root));scope=base/'fresh';scope.mkdir()
        assets=scope/'renamed.assets';assets.mkdir();(assets/'a image.png').write_bytes(b'image')
        md=scope/'renamed.md';md.write_text('![图片](renamed.assets/a%20image.png)\n<img src="renamed.assets/a%20image.png">',encoding='utf-8')
        rows=[{'completed':{'output':str(md)}}]
        files,errors,unbound=w.collect_artifacts(rows,scope,'pdf2md',resolver=Path)
        self.assertFalse(errors);self.assertEqual(len(files),2);self.assertFalse(unbound)
        other=base/'outside.png';other.write_bytes(b'outside')
        for reference in ('../outside.png','%2e%2e/outside.png','https://example.org/a.png','file:///x.png'):
            md.write_text('![]('+reference+')',encoding='utf-8')
            self.assertTrue(w.collect_artifacts(rows,scope,'pdf2md',resolver=Path)[1])
        z=scope/'cad.zip'
        with zipfile.ZipFile(z,'w') as f:f.writestr('images/1.jpg',b'image');f.writestr('drawing.dxf',b'dxf')
        self.assertEqual(len(w.zip_entries(z)),2)
        with zipfile.ZipFile(z,'w') as f:f.writestr('../escaped',b'bad')
        with self.assertRaises(w.Rejected):w.zip_entries(z)
        link=scope/'link'
        try:link.symlink_to(other)
        except OSError:self.skipTest('symlink privilege unavailable; independent Windows junction coverage required')
        self.assertTrue(w.collect_artifacts([{'completed':{'output':str(scope)}}],scope,'pdfsplit',resolver=Path)[1])

    def test_utf8_bom_duplicate_keys_and_corrupt_request(self):
        self.assertEqual(w.read_request(io.BytesIO(b'\xef\xbb\xbf{"hello":"world"}')),{'hello':'world'})
        for raw in (b'{"x":1,"x":2}',b'\xff',b'not json'):
            with self.assertRaises(w.Rejected):w.read_request(io.BytesIO(raw))

    def test_actual_image_header_dimensions_do_not_infer_page_mapping(self):
        root=Path(os.environ['WPS_TEST_ROOT']);root.mkdir(parents=True,exist_ok=True)
        path=Path(tempfile.mkdtemp(prefix='image-header-',dir=root))/'long.png'
        path.write_bytes(b'\x89PNG\r\n\x1a\n'+(13).to_bytes(4,'big')+b'IHDR'+(1122).to_bytes(4,'big')+(3174).to_bytes(4,'big'))
        result=w.image_dimensions(path)
        self.assertEqual((result['width'],result['height']),(1122,3174))
        self.assertEqual(result['pageMapping'],'not-assessed')

    def test_frozen_real_27_capture_shapes(self):
        path=os.environ.get('WPS_FIXTURE_INDEX')
        if not path:self.skipTest('not-covered: WPS_FIXTURE_INDEX not supplied; no native command invoked')
        index=json.loads(Path(path).read_text(encoding='utf-8'))
        self.assertEqual(len(index['fixtures']),27)
        for f in index['fixtures']:
            with self.subTest(path=f['path']):
                native=f['path']
                if sys.platform=='win32' and re.match(r'^/mnt/[a-z]/',native):
                    native=native[5].upper()+':/'+native[7:]
                raw=Path(native).read_bytes();self.assertEqual(hashlib.sha256(raw).hexdigest(),f['sha256'])
                events,unknowns=w.parse_capture(raw);self.assertEqual(len(events),f['eventCount']);self.assertEqual(unknowns,[])
                kinds={}
                for item in events:kinds[item['event']['type']]=kinds.get(item['event']['type'],0)+1
                self.assertEqual(kinds,f['eventTypes'])
                terminals=[x['event'] for x in events if x['event'].get('type')=='completed']
                self.assertEqual([sorted(x) for x in terminals],[sorted(x['keys']) for x in f['terminalSchemas']])


class ResultViewTests(unittest.TestCase):
    def test_aliases_are_copied_and_each_derived_occurrence_redacted_once(self):
        prefix = 'K:\\public-a0\\scope'
        requested = prefix + '\\requested-a0.pdf'
        resource = {'path': prefix + '\\secret-name.png', 'size': 3, 'sha256': 'a0' * 32}
        raw = {'schemaVersion': 2, 'status': 'completed', 'requestIdentity': 'a0' * 32,
               'nativeEvents': [], 'commands': [], 'unboundFiles': [resource['path']],
               'artifacts': [resource, {'path': requested, 'sha256': '0a' * 32,
                                        'resources': [resource], 'zipEntries': [{'name': 'secret/layer.dxf', 'crc': 10}]}]}
        before = copy.deepcopy(raw);seen = []
        def sanitize(value, secrets):
            self.assertNotIn(prefix, value)
            self.assertIn('secret', value)  # A second pass over the shown value fails.
            seen.append(value)
            return value.replace('secret', 'shown')
        with patch.object(w, 'redact', sanitize):
            shown = w.result_view(raw, prefix, requested, ['secret'])
        self.assertEqual(raw, before)
        self.assertEqual(seen.count('\\secret-name.png'), 3)
        self.assertEqual(seen.count('secret/layer.dxf'), 1)
        self.assertEqual(shown['artifacts'][0]['path'], prefix + '\\shown-name.png')
        self.assertEqual(shown['artifacts'][1]['resources'][0]['path'], prefix + '\\shown-name.png')
        self.assertEqual(shown['artifacts'][1]['zipEntries'][0]['name'], 'shown/layer.dxf')
        self.assertIsNot(shown['artifacts'][0], shown['artifacts'][1]['resources'][0])
        self.assertEqual(shown['artifacts'][1]['path'], requested)
        self.assertEqual(shown['artifacts'][0]['sha256'], 'a0' * 32)
        self.assertEqual(shown['requestIdentity'], 'a0' * 32);self.assertEqual(shown['status'], 'completed')


class RequestIdentityTests(unittest.TestCase):
    def test_identity_ignores_secret_values_even_when_public_text_collides(self):
        request = {'schemaVersion': 1, 'runId': 'a0', 'command': 'pdfwatermark',
                   'inputs': ['T:/data/alpha0.pdf'], 'evidenceDir': 'T:/data/evidence',
                   'options': {'text': 'alpha public watermark'}, 'password': 'first',
                   'openPassword': 'first', 'editPassword': ['first'],
                   'nested': [{'open-pwd': {'inside': ['first']}}, {'edit-pwd': ['first']}]}
        expected = w.request_identity(request)
        for secret in ('0', 'a', 'alpha', 'alpha public watermark', 'T:/data/alpha0.pdf', '秘密\\"值'):
            changed = copy.deepcopy(request)
            changed.update(password=secret, openPassword=secret, editPassword=[secret])
            changed['nested'] = [{'open-pwd': {'completelyDifferentKeys': [secret, {'leaf': secret}]}},
                                 {'edit-pwd': [secret, secret]}]
            self.assertEqual(w.request_identity(changed), expected)
        for key in ('evidenceDir', 'inputs', 'options'):
            changed = copy.deepcopy(request)
            changed[key] = {'text': 'changed'} if key == 'options' else ['T:/other.pdf'] if key == 'inputs' else 'T:/other'
            self.assertNotEqual(w.request_identity(changed), expected)
        for change in ('absent', 'array'):
            changed = copy.deepcopy(request)
            if change == 'absent':changed.pop('password')
            else:changed['password'] = ['first']
            self.assertNotEqual(w.request_identity(changed), expected)
        self.assertEqual(set(w.secret_values({'password': {'nested': ['one', {'value': 'two'}]}})), {'one', 'two'})


def short_password_worker():
    """测试进程适配器；生产封装无主机/状态/任意程序旁路。"""
    secret = json.loads(sys.stdin.buffer.read())['secret']
    harness = Harness('runTest');harness.setUp()
    try:
        request = harness.request('pdfencrypt');request['password'] = secret;request['openPassword'] = secret
        code1, first = harness.main_call(request)
        changed = dict(request, password='different-fixture-secret', openPassword='different-fixture-secret')
        code2, second = harness.main_call(changed)
        records = [json.loads(p.read_bytes()) for p in sorted(harness.base.rglob('*.json'))]
        argv = harness.calls[0][0]
        print(json.dumps({'codes': [code1, code2], 'first': first, 'second': second, 'records': records,
                          'calls': len(harness.calls),
                          'argvSecretReceived': argv[argv.index('--password') + 1] == secret and
                                                argv[argv.index('--open-pwd') + 1] == secret}))
    finally:
        harness.doCleanups()


if __name__ == '__main__':
    if sys.argv[1:] == ['--secret-fixture-worker']:
        short_password_worker()
    else:
        unittest.main(verbosity=2)
