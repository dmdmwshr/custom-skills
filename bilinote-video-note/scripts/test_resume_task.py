"""Exercise explicit cache recovery and dynamic model selection without a model call."""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest


HERE=Path(__file__).resolve().parent
TASK='11111111-1111-4111-8111-111111111111'
SOURCE='7693883674231788974'
URL='https://v.douyin.com/TEST123/'


def quote(value):
    return "'"+str(value).replace("'","''")+"'"


def run(root,code):
    path=root/'test.ps1'
    path.write_text("$ErrorActionPreference='Stop'\nSet-StrictMode -Version Latest\n"+code,encoding='utf-8-sig')
    return subprocess.run(['pwsh','-NoProfile','-File',str(path)],capture_output=True,timeout=20)


class RecoveryTests(unittest.TestCase):
    def fixture(self,root):
        backend=root/'backend'; results=backend/'note_results';results.mkdir(parents=True)
        uploads=backend/'uploads';uploads.mkdir()
        upload=uploads/f'douyin-{SOURCE}-{"1"*32}.mp4';upload.write_bytes(b'original video')
        audio=uploads/'original.mp3';audio.write_bytes(b'original audio')
        progress=root/'original-progress.json'
        progress.write_text(json.dumps({'task_id':TASK,'source_url':URL,'processing_platform':'local','uploaded_path':'/uploads/'+upload.name}),encoding='utf-8')
        (results/f'{TASK}.status.json').write_text(json.dumps({'status':'FAILED','message':'original failure'}),encoding='utf-8')
        (results/f'{TASK}_audio.json').write_text(json.dumps({'file_path':str(audio)}),encoding='utf-8')
        (results/f'{TASK}_transcript.json').write_text(json.dumps({'full_text':'已保存转写','segments':[{'start':0,'end':1,'text':'已保存转写'}]}),encoding='utf-8')
        sha=hashlib.sha256(upload.read_bytes()).hexdigest()
        code='function Write-JsonFile {param($Path,$Value) $Value | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $Path -Encoding UTF8}\n'
        code+='. '+quote(HERE/'resume-task.ps1')+'\n'
        code+='$media=[pscustomobject]@{SourceId='+quote(SOURCE)+';Sha256='+quote(sha)+'}\n'
        code+='Resolve-VideoNoteResume -ProgressPath '+quote(progress)+' -VideoUrl '+quote(URL)+' -Media $media -ResultDirectory '+quote(results)+' -EvidenceDirectory '+quote(root/'baseline')+' | ConvertTo-Json\n'
        return results,upload,code

    def test_failed_task_snapshots_original_and_never_mutates_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);results,_,code=self.fixture(root)
            baseline={p.name:p.read_bytes() for p in results.iterdir()}
            result=run(root,code)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(json.loads(result.stdout)['TaskId'],TASK)
            self.assertEqual(baseline,{p.name:p.read_bytes() for p in results.iterdir()})
            self.assertTrue((root/'baseline/manifest.json').is_file())
            self.assertNotEqual(run(root,code).returncode,0)

    def test_running_or_changed_media_cannot_be_replayed(self):
        for change in ('running','changed'):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);results,upload,code=self.fixture(root)
                if change=='running': (results/f'{TASK}.status.json').write_text('{"status":"TRANSCRIBING"}',encoding='utf-8')
                else: upload.write_bytes(b'other video')
                self.assertNotEqual(run(root,code).returncode,0)
                self.assertFalse((root/'baseline').exists())

    def test_missing_transcript_does_not_trigger_retranscription(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);results,_,code=self.fixture(root)
            (results/f'{TASK}_transcript.json').unlink()
            self.assertNotEqual(run(root,code).returncode,0)

    def test_role_is_read_on_each_call_and_explicit_model_wins(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);roles=root/'roles.json'
            roles.write_text('{"strong":"gpt-6.1-sol","light":"gpt-5.6-luna"}',encoding='utf-8')
            code='. '+quote(HERE/'model-role.ps1')+'\n'
            code+='$config=[pscustomobject]@{model_roles_file='+quote(roles)+';default_request=[pscustomobject]@{model_role="strong";model_name="fallback"}}\n'
            code+='Resolve-VideoNoteModel $config\nResolve-VideoNoteModel $config "explicit"\n'
            result=run(root,code)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(result.stdout.decode('utf-8').strip().splitlines(),['gpt-6.1-sol','explicit'])
            roles.write_text('{"strong":"gpt-6-sol","light":"gpt-5.6-luna"}',encoding='utf-8')
            self.assertIn(b'gpt-6-sol',run(root,code).stdout)
            roles.unlink()
            self.assertIn(b'fallback',run(root,code).stdout)


if __name__=='__main__':unittest.main()
