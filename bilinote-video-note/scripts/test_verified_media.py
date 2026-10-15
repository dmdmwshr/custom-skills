"""Validate media binding before any upload or model request (Windows only)."""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest


class VerifiedMediaTests(unittest.TestCase):
    def invoke(self, change=None):
        with tempfile.TemporaryDirectory(prefix='bn-media-test-') as temporary:
            root=Path(temporary)
            media=root/'sample [7692081714696293682].mp4'
            media.write_bytes(b'test media')
            receipt={'success':True,'status':'completed','action':'download','source_url':'https://v.douyin.com/TEST123/',
                'source_id':'7692081714696293682','file_path':str(media),'bytes':media.stat().st_size,
                'sha256':hashlib.sha256(media.read_bytes()).hexdigest(),'title':'sample'}
            if change:change(receipt,media)
            path=root/'receipt.json';path.write_text(json.dumps(receipt),encoding='utf-8')
            test=root/'test.ps1'
            helper=Path(__file__).with_name('verified-media.ps1')
            quote=lambda value:"'"+str(value).replace("'","''")+"'"
            test.write_text("$ErrorActionPreference='Stop'\n. "+quote(helper)+"\n$v=Resolve-VerifiedVideoMedia -ReceiptPath "+quote(path)+" -VideoUrl 'https://v.douyin.com/TEST123/' -Platform douyin\n$v.SourceId\n",encoding='utf-8-sig')
            return subprocess.run(['pwsh','-NoProfile','-File',str(test)],capture_output=True,timeout=20)

    def test_completed_receipt_accepts_matching_file(self):
        self.assertEqual(self.invoke().returncode,0)

    def test_changed_file_is_rejected(self):
        self.assertNotEqual(self.invoke(lambda r,p:p.write_bytes(b'fake media')).returncode,0)

    def test_wrong_source_is_rejected(self):
        self.assertNotEqual(self.invoke(lambda r,p:r.update(source_url='https://v.douyin.com/OTHER/')).returncode,0)

    def test_unfinished_receipt_is_rejected(self):
        self.assertNotEqual(self.invoke(lambda r,p:r.update(status='running')).returncode,0)


if __name__=='__main__':unittest.main()
