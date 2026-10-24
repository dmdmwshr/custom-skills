"""drawio-local 脚本回归（合成 .drawio，仅需 Node ≥ 18；不启动 draw.io Desktop、不联网）。
运行：python -I -B -m unittest discover -s drawio-local/tests -v
"""
import base64
import shutil
import subprocess
import tempfile
import unittest
import urllib.parse
import zlib
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
NODE = shutil.which("node")
MODEL = ('<mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/>'
         '<mxCell id="{i}" value="{v}" vertex="1" parent="1"><mxGeometry width="9" height="9" as="geometry"/></mxCell></root></mxGraphModel>')


def deflate(xml):
    raw = zlib.compressobj(wbits=-15)
    return base64.b64encode(raw.compress(urllib.parse.quote(xml, safe="~()*!.'").encode()) + raw.flush()).decode()


def node(*args, stdin=None):
    p = subprocess.run([NODE, *map(str, args)], capture_output=True, input=stdin, text=True, encoding="utf-8")
    return p.returncode, p.stdout, p.stderr


@unittest.skipUnless(NODE, "需要 node")
class PagesAndRepair(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="drawio-t-"); self.dir = Path(self.tmp.name)
        self.file = self.dir / "m.drawio"
        self.file.write_text(f'<mxfile><diagram id="p1" name="压缩页">{deflate(MODEL.format(i="a", v="开始"))}</diagram>'
                             f'<diagram id="p2" name="明文页">{MODEL.format(i="b", v="第二页")}</diagram></mxfile>', encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_list_get_set_keep_other_pages_and_compression(self):
        code, out, _ = node(SCRIPTS / "drawio_pages.mjs", "list", self.file)
        self.assertEqual(code, 0); self.assertIn("压缩页", out); self.assertIn("明文页", out)
        code, out, _ = node(SCRIPTS / "drawio_pages.mjs", "get", self.file, "压缩页")
        self.assertIn('value="开始"', out)
        before_plain = node(SCRIPTS / "drawio_pages.mjs", "get", self.file, "p2")[1]
        code, out, err = node(SCRIPTS / "drawio_pages.mjs", "set", self.file, "p1", "-", stdin=MODEL.format(i="z", v="已替换&amp;中文"))
        self.assertEqual(code, 0, err)
        text = self.file.read_text(encoding="utf-8")
        self.assertNotIn("<mxGraphModel", text.split('name="压缩页">')[1].split("</diagram>")[0])  # 仍是压缩存储
        self.assertIn("已替换&amp;中文", node(SCRIPTS / "drawio_pages.mjs", "get", self.file, "0")[1])
        self.assertEqual(node(SCRIPTS / "drawio_pages.mjs", "get", self.file, "p2")[1], before_plain)
        self.assertEqual(len(list(self.dir.glob("m.drawio.bak-*"))), 1)

    def test_rejections_leave_file_untouched(self):
        original = self.file.read_bytes()
        for args, stdin in ((("get", self.file, "不存在"), None), (("get", self.dir / "x.txt", "0"), None),
                            (("set", self.file, "0", "-"), "<mxfile/>"), (("set", self.file, "0", "-"), MODEL.format(i="q", v="x") + "<diagram/>")):
            code, _, err = node(SCRIPTS / "drawio_pages.mjs", *args, stdin=stdin)
            self.assertNotEqual(code, 0, args)
        self.assertEqual(self.file.read_bytes(), original)
        self.assertEqual(list(self.dir.glob("*.bak-*")), [])

    def test_repair_fixes_slips_and_is_idempotent_on_valid_files(self):
        bad = self.dir / "bad.drawio"
        bad.write_text('前言\n<mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="1"/>'
                       '<mxCell id="x" value="R&D <b>" vertex="1" parent="1"/></root></mxGraphModel> 结尾', encoding="utf-8")
        self.assertEqual(node(SCRIPTS / "drawio_pages.mjs", "list", bad)[0], 0)  # 修复前也不应崩
        self.assertEqual(node(SCRIPTS / "repair_drawio.mjs", bad)[0], 0)
        fixed = bad.read_text(encoding="utf-8")
        self.assertTrue(fixed.startswith("<mxGraphModel")); self.assertIn("R&amp;D &lt;b>", fixed)
        self.assertIn('id="1" parent="0"', fixed)
        before = self.file.read_bytes()
        node(SCRIPTS / "repair_drawio.mjs", self.file)
        self.assertEqual(self.file.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
