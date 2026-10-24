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
import xml.etree.ElementTree as ET
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

    def test_r1_malformed_or_wrong_root_page_content_is_rejected_and_nothing_changes(self):
        original = self.file.read_bytes()
        bad = [
            "<mxGraphModel><root><mxCell id=\"0\"/></root>",                      # 缺少结束标签
            "<mxGraphModelNotActuallyAGraph/>",                                     # 只是前缀相同
            "<mxGraphModel/><mxGraphModel/>",                                       # 多个根
            "<mxGraphModel><root></mxGraphModel>",                                  # 标签交叉
            "<mxGraphModel a=\"1\" a=\"2\"/>",                                      # 属性重复
            "<mxGraphModel a=1/>",                                                  # 属性值未加引号
            "<mxGraphModel a=\"x<y\"/>",                                            # 值内裸 <
            "<mxGraphModel a=\"R&D\"/>",                                            # 孤立 &
            "<mxGraphModel a=\"&nbsp;\"/>",                                         # 未定义实体
            "<mxGraphModel a=\"&#0;\"/>",                                           # 非法字符引用
            "<!DOCTYPE x [<!ENTITY a \"b\">]><mxGraphModel/>",                      # DOCTYPE
            "<mxGraphModel/>tail",                                                  # 根后多余文字
            "<mxGraphModel><root/><!-- a -- b --></mxGraphModel>",                  # 注释内 --
            "<mxfile><diagram id=\"z\"></diagram></mxfile>",                        # 错误根
            "<mxGraphModel><root><diagram/></root></mxGraphModel>",                 # 内含 diagram
            '<mxGraphModel a="bad\x01value"/>',
            '<mxGraphModel><root><![CDATA[bad\x01value]]></root></mxGraphModel>',
            '<mxGraphModel><!-- bad\x01 --></mxGraphModel>',
            '<mxGraphModel><?xml version="1.0"?><root/></mxGraphModel>',
            "",
        ]
        for content in bad:
            code, _, err = node(SCRIPTS / "drawio_pages.mjs", "set", self.file, "p1", "-", stdin=content)
            self.assertNotEqual(code, 0, content)
        self.assertEqual(self.file.read_bytes(), original)
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["m.drawio"])  # 没有备份、没有残留临时文件

    def test_r1_valid_content_with_chinese_entities_cdata_comment_still_passes(self):
        good = ('<?xml version="1.0" encoding="UTF-8"?><!-- 注释 --><mxGraphModel grid="1"><root><mxCell id="0"/><mxCell id="1" parent="0"/>'
                '<mxCell id="q" value="中文 &amp; &lt;b&gt;粗&lt;/b&gt; &#x4e2d;&#25991;" style=\'a=b;\' vertex="1" parent="1"><mxGeometry as="geometry"/></mxCell>'
                '<UserObject id="u" label="x"><![CDATA[ <raw> & ]]></UserObject></root></mxGraphModel>')
        code, _, err = node(SCRIPTS / "drawio_pages.mjs", "set", self.file, "p2", "-", stdin=good)
        self.assertEqual(code, 0, err)
        ET.fromstring(self.file.read_bytes())  # Whole file must remain valid, including declaration placement.
        self.assertNotIn('<?xml', self.file.read_text(encoding='utf-8'))
        self.assertIn("中文 &amp;", node(SCRIPTS / "drawio_pages.mjs", "get", self.file, "明文页")[1])

    def test_r2_every_successful_set_leaves_its_own_unique_recovery_point(self):
        states = [self.file.read_bytes()]
        for n in range(12):  # 同一秒/毫秒内连续写入
            code, _, err = node(SCRIPTS / "drawio_pages.mjs", "set", self.file, "p2", "-", stdin=MODEL.format(i=f"n{n}", v=f"第{n}版"))
            self.assertEqual(code, 0, err)
            states.append(self.file.read_bytes())
        backups = sorted(self.dir.glob("m.drawio.bak-*"))
        self.assertEqual(len(backups), 12)
        self.assertEqual(sorted(b.read_bytes() for b in backups), sorted(states[:-1]))  # 每次写入前的完整状态都在，含最初原件
        self.assertEqual(len(set(b.name for b in backups)), 12)
        self.assertEqual(list(self.dir.glob("*.tmp")), [])

    def test_r2_existing_files_are_never_overwritten(self):
        sentinel = self.dir / "m.drawio.bak-sentinel"; sentinel.write_text("别人的恢复点", encoding="utf-8")
        for _ in range(3):
            self.assertEqual(node(SCRIPTS / "drawio_pages.mjs", "set", self.file, "p1", "-", stdin=MODEL.format(i="k", v="x"))[0], 0)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "别人的恢复点")

    def test_editor_url_is_built_locally_and_roundtrips(self):
        url = node(SCRIPTS / "drawio_url.mjs", self.file, "--lightbox", "--dark", "true")[1].strip()
        self.assertTrue(url.startswith("https://app.diagrams.net/?lightbox=1&dark=1&border=10&edit=_blank#R"))
        packed = urllib.parse.unquote(url.split("#R", 1)[1])
        xml = urllib.parse.unquote(zlib.decompress(base64.b64decode(packed), -15).decode("utf-8"))
        self.assertEqual(xml, self.file.read_text(encoding="utf-8"))
        mmd = self.dir / "a.mmd"; mmd.write_text("graph TD; A-->B", encoding="utf-8")
        url = node(SCRIPTS / "drawio_url.mjs", mmd)[1].strip()
        self.assertIn("#create=", url); self.assertIn("mermaid", urllib.parse.unquote(url))
        bad = self.dir / "bad.drawio"; bad.write_text("<mxfile>", encoding="utf-8")
        self.assertNotEqual(node(SCRIPTS / "drawio_url.mjs", bad)[0], 0)

    def test_page_spans_ignore_markup_in_cdata_comments_and_quoted_attributes(self):
        graph = '<mxGraphModel><root><UserObject><![CDATA[<diagram>只是文字</diagram>]]></UserObject><!-- <diagram/> --></root></mxGraphModel>'
        for compressed_page in (False, True):
            body = deflate(graph) if compressed_page else graph
            self.file.write_text('<mxfile><diagram id="p1" name="中文 > 甲">' + body + '</diagram><diagram id="p2" name="乙"/></mxfile>', encoding="utf-8")
            code, out, err = node(SCRIPTS / "drawio_pages.mjs", "get", self.file, "p1")
            self.assertEqual(code, 0, err); self.assertEqual(out.strip(), graph); ET.fromstring(out)
            code, out, err = node(SCRIPTS / "drawio_pages.mjs", "set", self.file, "p1", "-", stdin=graph.replace('只是文字', '替换后'))
            self.assertEqual(code, 0, err); ET.fromstring(self.file.read_bytes())
            self.assertIn('<diagram id="p2" name="乙"/>', self.file.read_text(encoding='utf-8'))
            self.assertEqual(node(SCRIPTS / "drawio_pages.mjs", "get", self.file, "p1")[1].strip(), graph.replace('只是文字', '替换后'))

    def test_border_zero_and_invalid_values(self):
        code, out, err = node(SCRIPTS / "drawio_url.mjs", self.file, "--border", "0")
        self.assertEqual(code, 0, err)
        self.assertEqual(urllib.parse.parse_qs(urllib.parse.urlsplit(out.strip()).query)["border"], ["0"])
        for value in ("-1", "x", "1.5", "12x", "9007199254740992"):
            self.assertNotEqual(node(SCRIPTS / "drawio_url.mjs", self.file, "--border", value)[0], 0, value)
        self.assertNotEqual(node(SCRIPTS / "drawio_url.mjs", self.file, "--border")[0], 0)

    def test_repair_fixes_slips_and_is_idempotent_on_valid_files(self):
        bad = self.dir / "bad.drawio"
        bad.write_text('前言\n<mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="1"/>'
                       '<mxCell id="x" value="R&D <b>" vertex="1" parent="1"/></root></mxGraphModel> 结尾', encoding="utf-8")
        self.assertNotEqual(node(SCRIPTS / "drawio_pages.mjs", "list", bad)[0], 0)  # 非XML输入明确拒绝，修复入口单独处理
        self.assertEqual(node(SCRIPTS / "repair_drawio.mjs", bad)[0], 0)
        fixed = bad.read_text(encoding="utf-8")
        self.assertTrue(fixed.startswith("<mxGraphModel")); self.assertIn("R&amp;D &lt;b>", fixed)
        self.assertIn('id="1" parent="0"', fixed)
        before = self.file.read_bytes()
        node(SCRIPTS / "repair_drawio.mjs", self.file)
        self.assertEqual(self.file.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
