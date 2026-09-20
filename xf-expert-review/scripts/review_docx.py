"""Fill a checked table-based review template without rebuilding its layout.

The template supplies formatting; source/content supplies reviewed wording.
Only document.xml is edited; every other ZIP part is copied byte-for-byte.
"""
from __future__ import annotations
import argparse
from copy import deepcopy
import json
from pathlib import Path
import re
from zipfile import ZipFile
from lxml import etree
from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

DATE_RE = re.compile(r"[0-9X]{4}年[0-9X]{1,2}月[0-9X]{1,2}日")
OPINION_RE = re.compile(r"^[1-3][、.．]")


def locate(document):
    paragraphs = document.paragraphs
    title = next(p for p in paragraphs if p.text.strip())
    introductions = [p for p in paragraphs if DATE_RE.search(p.text) and
                     ("形成" in p.text or "组织召开" in p.text)]
    opinions = [p for p in paragraphs if OPINION_RE.match(p.text)]
    if len(introductions) != 1 or len(opinions) != 3:
        raise ValueError("Expected one dated introduction and three numbered opinions")
    end = next(i for i,p in enumerate(paragraphs) if p._p is opinions[-1]._p)
    tail = [p for p in paragraphs[end+1:] if p.text.strip()]
    if len(tail) != 2 or "签字" not in tail[1].text:
        raise ValueError("Expected conclusion and intact signature area; inspect unfamiliar structure")
    return title, introductions[0], opinions, tail[0]


def table_fields(document):
    if len(document.tables) != 1 or len(document.sections) != 1:
        raise ValueError("Use an explicitly selected single-section, table-based layout template")
    table = document.tables[0]
    if any(len(r.cells) != 2 for r in table.rows):
        raise ValueError("Expected two-column cover table without merged cells")
    people, spacers, dates = [], [], []
    for row in table.rows:
        cells = [c.text.strip() for c in row.cells]
        if any(DATE_RE.fullmatch(c) for c in cells):
            dates.append(row)
        elif not any(cells):
            spacers.append(row)
        elif all(cells):
            people.append(row)
        else:
            raise ValueError("Unrecognized cover row")
    if len(people) != 5 or len(dates) != 1 or not spacers or dates[0]._tr is not table.rows[-1]._tr:
        raise ValueError("Expected five people, blank spacer rows and a final date row")
    return table, people, spacers, dates[0]


def source_fields(document):
    title, intro, opinions, conclusion = locate(document)
    experts = []
    if document.tables:
        _, people, _, _ = table_fields(document)
        experts = [[c.text.strip() for c in row.cells] for row in people]
    else:
        for p in document.paragraphs:
            if p._p is intro._p:
                break
            if "\t" in p.text and "评审人员姓名" not in p.text:
                experts.append([v.strip() for v in p.text.split("\t", 1)])
    if len(experts) != 5:
        raise ValueError("Expected five reviewed name/description pairs")
    return {"title": title.text, "experts": experts,
            "date": DATE_RE.search(intro.text).group(), "introduction": intro.text,
            "opinions": [OPINION_RE.sub("",p.text, count=1) for p in opinions],
            "conclusion": conclusion.text}


def replace_text(paragraph, value):
    # Keep paragraph properties, run properties and non-text objects in place.
    texts = list(paragraph._p.iter(qn("w:t")))
    if not texts:
        raise ValueError("Replacement field has no template text run")
    if paragraph._p.xpath('.//w:fldChar|.//w:instrText|.//w:br|.//w:tab'):
        raise ValueError("Complex replacement field requires manual review")
    offset = 0
    for i,t in enumerate(texts):
        n = len(value)-offset if i == len(texts)-1 else min(len(t.text or ""), len(value)-offset)
        t.text = value[offset:offset+n]
        t.set(qn("xml:space"), "preserve")
        offset += n


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


def build(source, output, blank=False, content=None, prefix_suggestions=False,
          template=None, name_width_twips=None, spacer_total_twips=None,
          body_page_break=False):
    source, output = Path(source), Path(output)
    template = Path(template) if template else source
    if output.exists() or output.resolve() in {source.resolve(), template.resolve()}:
        raise ValueError("Output must be a new path; caller owns backup and publication")
    doc = Document(template)
    table, people, spacers, date_row = table_fields(doc)
    fields = source_fields(Document(source)) if content is None else deepcopy(content)
    required = {"title", "experts", "date", "introduction", "opinions", "conclusion"}
    if not required <= fields.keys():
        raise ValueError("Missing content fields: " + ",".join(sorted(required-fields.keys())))
    if blank:
        title = fields["title"]
        phase = "专家验收评审会" if "验收" in title else ("技术需求分析报告评审会" if "技术需求" in title else "需求及预算专家论证会")
        fields.update(experts=[["XXX", "XXXXXXXXXXXXX"] for _ in range(5)], date="XXXX年XX月XX日",
                      introduction=f"XXXX年XX月XX日，无锡市消防救援局在XXX组织召开XXX项目{phase}。与会专家审阅相关材料、听取汇报，经质询和讨论，形成意见如下：",
                      opinions=["建议XXX"]*3, conclusion="评审结论：XXX。")
    if len(fields["experts"]) != 5 or any(len(p) != 2 for p in fields["experts"]):
        raise ValueError("Expected five name/description pairs")
    if len(fields["opinions"]) != 3 or not DATE_RE.fullmatch(fields["date"]):
        raise ValueError("Expected three opinions and a valid numeric/X date")
    if DATE_RE.search(fields["introduction"]) is None or DATE_RE.search(fields["introduction"]).group() != fields["date"]:
        raise ValueError("Cover and introduction dates must agree")
    title, introduction, opinions, conclusion = locate(doc)
    for p,text in [(title,fields["title"]),(introduction,fields["introduction"]),(conclusion,fields["conclusion"])]:
        replace_text(p,text)
    for i,(p,text) in enumerate(zip(opinions,fields["opinions"]),1):
        if OPINION_RE.match(text):
            raise ValueError("Opinion payload excludes its numeric prefix")
        replace_text(p,f"{i}、{text}")
        if prefix_suggestions:
            prefix_advice(p._p)
    for row,pair in zip(people,fields["experts"]):
        for cell,text in zip(row.cells,pair):
            if len(cell.paragraphs) != 1:
                raise ValueError("Expected one paragraph per cover cell")
            replace_text(cell.paragraphs[0],text)
    date_cell = next(c for c in date_row.cells if DATE_RE.fullmatch(c.text.strip()))
    replace_text(date_cell.paragraphs[0],fields["date"])
    if name_width_twips is not None:
        grid = table._tbl.tblGrid.gridCol_lst
        total = sum(g.w.twips for g in grid)
        if not 0 < name_width_twips < total:
            raise ValueError("Name-column width must be inside existing table width")
        widths = [name_width_twips,total-name_width_twips]
        for g,width in zip(grid,widths):
            g.set(qn("w:w"),str(width))
        for row in table.rows:
            for cell,width in zip(row.cells,widths):
                cell._tc.get_or_add_tcPr().get_or_add_tcW().set(qn("w:w"),str(width))
    if spacer_total_twips is not None:
        if spacer_total_twips < len(spacers):
            raise ValueError("Spacer height must be positive")
        each,extra = divmod(spacer_total_twips,len(spacers))
        for i,row in enumerate(spacers):
            pr = row._tr.get_or_add_trPr()
            for old in list(pr.findall(qn("w:trHeight"))):
                pr.remove(old)
            height = OxmlElement("w:trHeight")
            height.set(qn("w:val"),str(each+(i<extra)))
            height.set(qn("w:hRule"),"exact")
            pr.append(height)
    if body_page_break:
        introduction.paragraph_format.page_break_before = True
    # Unknown participant labels must be explicitly normalized in reviewed input.
    if any("基层代表" in c.text for r in table.rows for c in r.cells):
        raise ValueError("Use X placeholders for the unnamed participant")
    restore_placeholder_highlights(doc._element)
    output.parent.mkdir(parents=True, exist_ok=True)
    document_xml = etree.tostring(doc._element,xml_declaration=True,encoding="UTF-8",standalone=True)
    with ZipFile(template) as src, ZipFile(output,"w") as dst:
        for entry in src.infolist():
            dst.writestr(entry,document_xml if entry.filename == "word/document.xml" else src.read(entry.filename))
    print(json.dumps({"output":str(output),"template":str(template),"tables":1,"render_required":True},ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="Reviewed content source")
    parser.add_argument("--template", help="Explicit layout authority; defaults to source only when table-based")
    parser.add_argument("--output", required=True)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--blank", action="store_true")
    group.add_argument("--content", help="Reviewed UTF-8 JSON content")
    parser.add_argument("--prefix-suggestions", action="store_true")
    parser.add_argument("--name-width-twips", type=int, help="Optional name column width; retain total width")
    parser.add_argument("--spacer-total-twips", type=int, help="Optional total blank-row height, calibrated by Word render")
    parser.add_argument("--body-page-break", action="store_true", help="Explicitly keep introduction on page 2")
    args = parser.parse_args()
    payload = json.loads(Path(args.content).read_text(encoding="utf-8-sig")) if args.content else None
    build(args.source,args.output,args.blank,payload,args.prefix_suggestions,args.template,
          args.name_width_twips,args.spacer_total_twips,args.body_page_break)
