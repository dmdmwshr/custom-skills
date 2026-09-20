"""Build or normalize a local expert-review DOCX; external templates stay authoritative.

Requires python-docx. Writes a new file only; the caller owns backup and publication.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import re

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_COLOR_INDEX
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Mm, Pt

FONT = "方正仿宋_GBK"
DATE_RE = re.compile(r"[0-9X]{4}年[0-9X]{1,2}月[0-9X]{1,2}日")


def add_text(paragraph, text, size=16, font=FONT, bold=False):
    for chunk in re.split(r"(X+)", text):
        if not chunk:
            continue
        run = paragraph.add_run(chunk)
        run.font.name, run.font.size, run.bold = font, Pt(size), bold
        run._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), font)
        if re.fullmatch(r"X+", chunk):
            run.font.highlight_color = WD_COLOR_INDEX.YELLOW


def no_grid(paragraph):
    pr = paragraph._p.get_or_add_pPr()
    for tag in ("w:snapToGrid",):
        for old in list(pr.findall(qn(tag))):
            pr.remove(old)
    element = OxmlElement("w:snapToGrid")
    element.set(qn("w:val"), "0")
    pr.append(element)


def border(paragraph, edges, space=8):
    pr = paragraph._p.get_or_add_pPr()
    element = OxmlElement("w:pBdr")
    for edge in edges:
        line = OxmlElement("w:" + edge)
        for key, value in {"val": "single", "sz": "16", "space": str(space), "color": "000000"}.items():
            line.set(qn("w:" + key), value)
        element.append(line)
    # Borders precede tabs/spacing/ind/jc in paragraph properties.
    before = next((c for c in pr if c.tag in {qn("w:tabs"), qn("w:spacing"), qn("w:ind"), qn("w:jc"), qn("w:rPr")}), None)
    if before is None:
        pr.append(element)
    else:
        before.addprevious(element)


def source_fields(document):
    paragraphs = list(document.paragraphs)
    title = next((p.text.strip() for p in paragraphs if p.text.strip()), "")
    start = next((i for i, p in enumerate(paragraphs)
                  if DATE_RE.search(p.text) and ("形成" in p.text or "组织召开" in p.text)), None)
    if start is None:
        raise ValueError("Cannot locate dated meeting introduction; inspect the source manually")
    experts = []
    if document.tables:
        table = document.tables[0]
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells]
            if len(cells) >= 2 and cells[0] and not DATE_RE.fullmatch(cells[0]):
                if DATE_RE.search(cells[1]):
                    continue
                experts.append([cells[0], cells[1]])
    else:
        for p in paragraphs[:start]:
            if "\t" in p.text and "评审人员姓名" not in p.text:
                fields = p.text.split("\t", 1)
                if all(f.strip() for f in fields):
                    experts.append([f.strip() for f in fields])
    if not 1 <= len(experts) <= 8:
        raise ValueError("Expected 1-8 expert rows; do not guess missing names")
    date = DATE_RE.search(paragraphs[start].text).group()
    return title, experts, date, [deepcopy(p._p) for p in paragraphs[start:]]


def restore_placeholder_highlights(root):
    for hi in list(root.iter(qn("w:highlight"))):
        hi.getparent().remove(hi)
    for run in list(root.iter(qn("w:r"))):
        ts = run.findall(qn("w:t"))
        if len(ts) != 1 or not re.search(r"X+", ts[0].text or ""):
            continue
        # Split a simple text run only; retain its original font and size.
        if any(c.tag not in {qn("w:rPr"), qn("w:t")} for c in run):
            continue
        for chunk in re.split(r"(X+)", ts[0].text or ""):
            if not chunk:
                continue
            new = OxmlElement("w:r")
            pr = deepcopy(run.find(qn("w:rPr")))
            if pr is None:
                pr = OxmlElement("w:rPr")
            if re.fullmatch(r"X+", chunk):
                hi = OxmlElement("w:highlight")
                hi.set(qn("w:val"), "yellow")
                pr.append(hi)
            new.append(pr)
            t = OxmlElement("w:t")
            t.set(qn("xml:space"), "preserve")
            t.text = chunk
            new.append(t)
            run.addprevious(new)
        run.getparent().remove(run)


def prefix_advice(element):
    texts = list(element.iter(qn("w:t")))
    full = "".join(t.text or "" for t in texts)
    match = re.match(r"[1-3][、.．]\s*", full)
    if not match or full[match.end():].startswith("建议"):
        return
    position = match.end()
    for text in texts:
        current = text.text or ""
        if position <= len(current):
            text.text = current[:position] + "建议" + current[position:]
            return
        position -= len(current)


def build(source, output, blank=False, content=None, prefix_suggestions=False):
    source, output = Path(source), Path(output)
    if source.resolve() == output.resolve() or output.exists():
        raise ValueError("Output must be a new path; never overwrite a source or candidate")
    doc = Document(source)
    if len(doc.sections) != 1:
        raise ValueError("Only the checked single-section review format is supported")
    title, experts, date, body = source_fields(doc)
    if blank:
        experts = [["XXX", "XXXXXXXXXXXXX"] for _ in range(5)]
        date = "XXXX年XX月XX日"
        phase = "专家验收评审会" if "验收" in title else ("技术需求分析报告评审会" if "技术需求" in title else "需求及预算专家论证会")
        content = {"title": title, "experts": experts, "date": date,
                   "introduction": f"{date}，无锡市消防救援局在XXX组织召开XXX项目{phase}。与会专家审阅相关材料、听取汇报，经质询和讨论，形成意见如下：",
                   "opinions": ["建议XXX", "建议XXX", "建议XXX"], "conclusion": "评审结论：XXX。"}
    if content is not None:
        required = {"title", "experts", "date", "introduction", "opinions", "conclusion"}
        if not required <= content.keys():
            raise ValueError("Content is missing: " + ",".join(sorted(required - content.keys())))
        title, experts, date = content["title"], content["experts"], content["date"]
        if len(content["opinions"]) != 3:
            raise ValueError("This template expects exactly 3 project-specific opinions")
        if not 1 <= len(experts) <= 8 or any(len(e) != 2 for e in experts):
            raise ValueError("Experts must be 1-8 name/description pairs")
    experts = [[name, ("XXXXXXXXXXXXX" if desc.startswith("基层代表：") else desc)] for name, desc in experts]
    body_element = doc._element.body
    for element in list(body_element):
        if element.tag != qn("w:sectPr"):
            body_element.remove(element)
    section = doc.sections[0]
    section.page_width, section.page_height = Mm(210), Mm(297)
    section.top_margin, section.bottom_margin = Mm(25.4), Mm(25.4)
    section.left_margin, section.right_margin = Mm(31.75), Mm(31.75)
    section.footer_distance = Mm(20)
    section.different_first_page_header_footer = True
    # One first-page footer anchors date and bottom rule independently of expert rows.
    for hf in (section.header, section.first_page_header, section.footer, section.first_page_footer):
        for e in list(hf._element):
            hf._element.remove(e)
        hf.add_paragraph()
    foot = section.first_page_footer.paragraphs[0]
    foot.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    foot.paragraph_format.space_after = Pt(0)
    foot.paragraph_format.line_spacing = Pt(24)
    no_grid(foot)
    add_text(foot, date)
    border(foot, ["bottom"], 10)

    heading = doc.add_paragraph()
    heading.alignment = WD_ALIGN_PARAGRAPH.CENTER
    heading.paragraph_format.line_spacing = Pt(34)
    heading.paragraph_format.space_before = Pt(6)
    heading.paragraph_format.space_after = Pt(62)
    no_grid(heading)
    add_text(heading, title, 22, "方正小标宋简体")
    border(heading, ["top", "bottom"], 10)
    header = doc.add_paragraph()
    header.paragraph_format.tab_stops.add_tab_stop(Pt(185))
    header.paragraph_format.line_spacing = Pt(30)
    header.paragraph_format.space_after = Pt(0)
    no_grid(header)
    add_text(header, "评审人员姓名\t人员简介", bold=True, font="方正黑体_GBK")
    for name, desc in experts:
        p = doc.add_paragraph()
        p.paragraph_format.left_indent = Pt(12)
        p.paragraph_format.tab_stops.add_tab_stop(Pt(80))
        p.paragraph_format.line_spacing = Pt(30)
        p.paragraph_format.space_after = Pt(0)
        no_grid(p)
        add_text(p, name + "\t" + desc)
    if content is None:
        for i, element in enumerate(body):
            # Existing body is copied verbatim, except explicitly requested advice prefixes.
            if prefix_suggestions:
                prefix_advice(element)
            if i == 0:
                pr = element.get_or_add_pPr()
                for old in list(pr.findall(qn("w:pageBreakBefore"))):
                    pr.remove(old)
                flag = OxmlElement("w:pageBreakBefore")
                pr.insert(0, flag)
            body_element.insert(len(body_element) - 1, element)
    else:
        lines = [content["introduction"]] + [f"{i}、{opinion}" for i, opinion in enumerate(content["opinions"], 1)] + [content["conclusion"], "", "专家签字："]
        for i, text in enumerate(lines):
            p = doc.add_paragraph()
            p.paragraph_format.first_line_indent = Pt(32)
            p.paragraph_format.line_spacing = Pt(31.2)
            p.paragraph_format.space_after = Pt(0)
            p.paragraph_format.page_break_before = (i == 0)
            no_grid(p)
            add_text(p, text)
    restore_placeholder_highlights(doc._element)
    restore_placeholder_highlights(section.first_page_footer._element)
    output.parent.mkdir(parents=True, exist_ok=True)
    doc.save(output)
    check = Document(output)
    assert not check.tables
    assert len(check.sections) == 1
    assert all("基层代表" not in p.text for p in check.paragraphs)
    print(json.dumps({"output": str(output), "experts": len(experts), "tables": 0, "render_required": True}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", required=True)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--blank", action="store_true", help="Make a reusable blank template")
    group.add_argument("--content", help="UTF-8 JSON with reviewed title, experts, date, introduction, opinions, conclusion")
    parser.add_argument("--prefix-suggestions", action="store_true", help="Explicitly prefix existing numbered opinions with 建议; never duplicate")
    args = parser.parse_args()
    payload = json.loads(Path(args.content).read_text(encoding="utf-8-sig")) if args.content else None
    build(args.source, args.output, args.blank, payload, args.prefix_suggestions)
