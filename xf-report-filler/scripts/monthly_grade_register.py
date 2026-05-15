import argparse
import copy
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from docx import Document
from openpyxl import load_workbook
from openpyxl.styles import Alignment
from openpyxl.utils import get_column_letter

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
ROOT_DIR = SKILL_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import writer

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


PRODUCT_ORDER = ["江阴", "宜兴", "梁溪", "锡山", "惠山", "滨湖", "新吴", "经开"]
OFFICE_ORDER = ["梁溪", "锡山", "惠山", "滨湖", "新吴", "江阴", "宜兴", "经开"]
CASE_ORDER = ["江阴", "宜兴", "梁溪", "锡山", "惠山", "滨湖", "新吴", "经开"]
PERSON_SCORE_COLUMNS = list(range(28, 36))  # AB:AI
REPORT_TITLE_FONT = "方正黑体_GBK"
REPORT_BODY_FONT = "方正楷体_GBK"


class HumanReviewRequired(RuntimeError):
    def __init__(self, issues):
        self.issues = issues
        super().__init__("人员信息需要人工核对")


def norm_text(value):
    return re.sub(r"\s+", "", str(value or "")).strip()


def brigade_short(value):
    return norm_text(value).replace("大队", "").replace("轨道交通", "轨交")


def score_text(value):
    if value is None or value == "":
        return ""
    num = float(value)
    return str(round(num, 2)).rstrip("0").rstrip(".")


def require_path(path, label):
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"{label}不存在：{path}")
    return path


def find_one(base, patterns, label):
    matches = []
    for pattern in patterns:
        matches.extend(Path(base).glob(pattern))
    matches = [p for p in matches if not p.name.startswith("~$")]
    if not matches:
        raise FileNotFoundError(f"未找到{label}：{patterns}")
    if len(matches) > 1:
        matches = sorted(matches, key=lambda p: p.stat().st_mtime, reverse=True)
    return matches[0]


def copy_output(src, dst, force=False):
    dst = Path(dst)
    if dst.exists() and not force:
        raise FileExistsError(f"目标文件已存在，使用 --force 覆盖生成文件：{dst}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return dst


def parse_deduction_value(line):
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", line)]
    small = [x for x in nums if x <= 1.0]
    return small[-1] if small else None


def parse_general_index(line):
    match = re.search(r"3\s*\.\s*(\d+)", line)
    return int(match.group(1)) if match else None


def clean_error_line(line):
    text = str(line or "").strip()
    text = re.sub(r"^\d+\s*[、.．]\s*", "", text)
    return text.strip(" ；;，,")


def parse_product_register(path):
    document = Document(str(path))
    texts = [p.text.strip() for p in document.paragraphs]
    blocks = []
    current = None
    for text in texts:
        if not text:
            continue
        if text.startswith("大队："):
            if current:
                blocks.append(current)
            current = {"大队": text.split("：", 1)[1].strip(), "lines": []}
        elif current:
            current["lines"].append(text)
    if current:
        blocks.append(current)

    records = []
    for block in blocks:
        record = {
            "大队": block["大队"],
            "short": brigade_short(block["大队"]),
            "题名": "",
            "编号": "",
            "立卷人": "",
            "检查人": "",
            "errors": [],
            "deductions": [],
            "score": None,
            "no_case": False,
        }
        pending_error = None
        for line in block["lines"]:
            if line.startswith("题名："):
                record["题名"] = line.split("：", 1)[1].strip()
            elif line.startswith("编号："):
                record["编号"] = line.split("：", 1)[1].strip()
            elif line.startswith("立卷人："):
                record["立卷人"] = line.split("：", 1)[1].strip()
            elif line.startswith("检查人："):
                record["检查人"] = line.split("：", 1)[1].strip()
            elif line == "错误" or re.fullmatch(r"\d+\s*[、.．]?", line):
                continue
            elif line.startswith("扣分"):
                value = parse_deduction_value(line)
                general_index = parse_general_index(line)
                if pending_error and value is not None and general_index is not None:
                    record["errors"].append(pending_error)
                    record["deductions"].append(
                        {
                            "general_index": general_index,
                            "value": value,
                            "line": line,
                            "description": pending_error,
                        }
                    )
                pending_error = None
            else:
                cleaned = clean_error_line(line)
                if cleaned:
                    pending_error = cleaned
        if pending_error:
            record["errors"].append(pending_error)

        if not record["题名"] and not record["编号"] and not record["立卷人"]:
            record["no_case"] = True
            record["score"] = 0.0
        else:
            total = sum(item["value"] for item in record["deductions"])
            record["score"] = round(10.0 - total, 1)
        records.append(record)

    by_short = {item["short"]: item for item in records}
    return [by_short[key] for key in PRODUCT_ORDER if key in by_short]


def load_product_template_record():
    template_path = SKILL_DIR / "resources" / "template.json"
    data = json.loads(template_path.read_text(encoding="utf-8"))
    return data[0]


def general_key(record, index):
    prefix = f"{index}_"
    for key in record["一般要素"].keys():
        if key.startswith(prefix):
            return key
    raise KeyError(f"template.json 中找不到一般要素 {index}")


def build_product_batch(product_records, year, month):
    template = load_product_template_record()
    batch = []
    for item in product_records:
        record = copy.deepcopy(template)
        record["fields"].update(
            {
                "被检查单位": item["大队"],
                "评查日期": f"{year}年{month}月",
                "题名": item["题名"],
                "编号": item["编号"],
                "立卷人": item["立卷人"],
                "检查人": item["检查人"],
            }
        )
        if item["no_case"]:
            record["no_case"] = True
            record["score_override"] = 0
        else:
            for deduction in item["deductions"]:
                key = general_key(record, deduction["general_index"])
                current_score = record["一般要素"][key].get("扣分情况", "")
                current_desc = record["一般要素"][key].get("扣分说明", "")
                new_score = deduction["value"] + (float(current_score) if current_score else 0.0)
                descriptions = [x for x in [current_desc, deduction["description"]] if x]
                record["一般要素"][key]["扣分情况"] = score_text(new_score)
                record["一般要素"][key]["扣分说明"] = "；".join(descriptions)
        batch.append(record)
    return batch


def find_soffice():
    found = shutil.which("soffice")
    if found:
        return found
    fallback = Path(r"D:\Program_Files\LibreOffice\program\soffice.com")
    if fallback.exists():
        return str(fallback)
    raise FileNotFoundError("未找到 LibreOffice soffice，用于读取 .xls")


def load_xls_as_workbook(path, data_only=True):
    soffice = find_soffice()
    temp_dir = tempfile.TemporaryDirectory(prefix="xf_grade_xls_")
    out_dir = Path(temp_dir.name)
    subprocess.run(
        [soffice, "--headless", "--convert-to", "xlsx", "--outdir", str(out_dir), str(path)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    converted = next(out_dir.glob("*.xlsx"))
    workbook = load_workbook(converted, data_only=data_only)
    workbook._xf_temp_dir = temp_dir
    return workbook


def read_monitor_scores(stats_path):
    workbook = load_xls_as_workbook(stats_path, data_only=True)
    ws = workbook.active
    scores = {}
    for row in range(3, ws.max_row + 1):
        short = brigade_short(ws.cell(row, 1).value)
        if short not in PRODUCT_ORDER:
            continue
        row_scores = [float(ws.cell(row, col).value) for col in range(2, 12)]
        avg = ws.cell(row, 12).value
        if avg is None:
            avg = sum(row_scores) / len(row_scores)
        scores[short] = {"scores": row_scores, "avg": round(float(avg), 2)}
    return scores


def normalize_monitor_issues(raw):
    text = str(raw or "").strip(" ；;，,、")
    parts = [p.strip() for p in re.split(r"[、,，]+", text) if p.strip()]
    normalized = []
    for part in parts:
        low = part.lower()
        if part in {"人员信息", "未上传人员信息"}:
            normalized.append("缺人员信息")
        elif low in {"pdf", "缺pdf"}:
            normalized.append("缺PDF")
        elif low in {"cad", "缺cad"}:
            normalized.append("缺CAD图")
        elif part == "火灾防控图":
            normalized.append("缺火灾防控图")
        elif part == "主机日期":
            normalized.append("消控主机生产日期未录入")
        elif part in {"未登录", "账号长期登录"}:
            normalized.append("账号长期未登录")
        elif part == "脱岗":
            normalized.append("消控室脱岗")
        elif part in {"消控室电话", "消控电话不是固话", "消控非固话", "没有消控电话"}:
            normalized.append("消控室电话不规范")
        elif part == "监督编码":
            normalized.append("监督编码缺失")
        elif part == "设备离线":
            normalized.append("设备离线")
        elif "维保合同维保记录" in part:
            normalized.append(part.replace("维保合同维保记录", "缺维保合同、缺维保记录"))
        elif part == "维保记录":
            normalized.append("缺维保记录")
        elif part == "维保合同":
            normalized.append("缺维保合同")
        elif part == "缺维保合同":
            normalized.append("缺维保合同")
        else:
            normalized.append(part.replace("维保合同维保记录", "缺维保合同、缺维保记录"))
    return "、".join(normalized)


def parse_monitor_note(note):
    note = str(note or "").strip()
    match = re.search(r"\d{3,4}\s*[，,、]?\s*([\u4e00-\u9fff]{2,4})", note)
    person = match.group(1) if match else ""
    if match:
        issues = note[match.end() :]
    else:
        issues = note
    issues = issues.strip(" /\\，,、")
    return person, issues


def read_monitor_details(base_info_path, monitor_scores):
    workbook = load_xls_as_workbook(base_info_path, data_only=False)
    details = []
    for ws in workbook.worksheets:
        short = brigade_short(ws.title)
        if short not in monitor_scores:
            continue
        scores = monitor_scores[short]["scores"]
        for row in range(1, min(ws.max_row, len(scores)) + 1):
            unit = ws.cell(row, 1).value
            non_empty = [
                ws.cell(row, col).value
                for col in range(1, ws.max_column + 1)
                if ws.cell(row, col).value not in (None, "")
            ]
            note = str(non_empty[-1]).strip() if non_empty else ""
            person, issues = parse_monitor_note(note)
            details.append(
                {
                    "short": short,
                    "大队": f"{short}大队",
                    "index": row,
                    "单位": str(unit or "").strip(),
                    "联系人": person,
                    "score": float(scores[row - 1]),
                    "issues_raw": issues,
                    "issues_report": normalize_monitor_issues(issues),
                    "note": note,
                }
            )
    return details


def validate_person_matches(template_path, product_records, monitor_details):
    workbook = load_workbook(template_path)
    ws = workbook.active
    person_index = build_person_index(ws)
    issues = []

    for item in product_records:
        if item["no_case"] or not item["立卷人"]:
            continue
        key = (item["short"], norm_text(item["立卷人"]))
        if key not in person_index:
            issues.append(
                {
                    "type": "product",
                    "大队": item["大队"],
                    "姓名": item["立卷人"],
                    "案卷": item["题名"],
                    "message": f"个人统计表未找到产品立卷人：{item['大队']} {item['立卷人']}",
                }
            )

    for detail in monitor_details:
        name = norm_text(detail["联系人"])
        key = (detail["short"], name)
        if not name or key not in person_index:
            issues.append(
                {
                    "type": "monitor",
                    "大队": detail["大队"],
                    "姓名": detail["联系人"],
                    "单位": detail["单位"],
                    "message": f"个人统计表未找到联网联系人：{detail['大队']} {detail['联系人']}",
                }
            )

    if issues:
        raise HumanReviewRequired(issues)
    return True


def write_product_summary(template_path, output_path, product_records, force):
    copy_output(template_path, output_path, force=force)
    workbook = load_workbook(output_path)
    ws = workbook.active
    scores = {item["short"]: item["score"] for item in product_records}
    for row in range(2, ws.max_row + 1):
        short = brigade_short(ws.cell(row, 1).value)
        if short in scores:
            ws.cell(row, 2).value = scores[short]
    workbook.save(output_path)


def build_person_index(ws):
    index = {}
    current_brigade = ""
    for row in range(6, ws.max_row + 1):
        if ws.cell(row, 1).value not in (None, ""):
            current_brigade = brigade_short(ws.cell(row, 1).value)
        name = norm_text(ws.cell(row, 2).value)
        if current_brigade and name:
            index[(current_brigade, name)] = row
    return index


def write_personal_stats(template_path, output_path, product_records, monitor_details, month, force):
    validate_person_matches(template_path, product_records, monitor_details)
    copy_output(template_path, output_path, force=force)
    workbook = load_workbook(output_path)
    ws = workbook.active
    for col in range(27, 36):
        ws.cell(5, col).value = f"{month}月"
    for row in range(6, ws.max_row + 1):
        for col in range(27, 36):
            ws.cell(row, col).value = None

    person_index = build_person_index(ws)
    warnings = []
    for item in product_records:
        if item["no_case"] or not item["立卷人"]:
            continue
        key = (item["short"], norm_text(item["立卷人"]))
        row = person_index.get(key)
        if row:
            ws.cell(row, 27).value = item["score"]
        else:
            warnings.append(f"个人统计表未找到产品立卷人：{item['大队']} {item['立卷人']}")

    next_col_by_person = {}
    for detail in monitor_details:
        key = (detail["short"], norm_text(detail["联系人"]))
        row = person_index.get(key)
        if not row:
            warnings.append(f"个人统计表未找到联网联系人：{detail['大队']} {detail['联系人']}")
            continue
        col = next_col_by_person.get(key, PERSON_SCORE_COLUMNS[0])
        if col > PERSON_SCORE_COLUMNS[-1]:
            warnings.append(f"联网联系人超过 AB:AI 可填列：{detail['大队']} {detail['联系人']}")
            continue
        ws.cell(row, col).value = detail["score"]
        next_col_by_person[key] = col + 1
    workbook.save(output_path)
    return warnings


def product_office_text(item):
    if item["no_case"]:
        return f"{item['大队']}本月无消防产品监督检查案卷，产品成绩按0分登记。"
    lines = [f"{item['题名']}产品监督检查中有以下错误"]
    for idx, err in enumerate(item["errors"], start=1):
        lines.append(f"{idx}、{clean_error_line(err)}")
    return "\n".join(lines)


def monitor_office_text(short, details):
    rows = [d for d in details if d["short"] == short]
    lines = []
    for idx, detail in enumerate(rows, start=1):
        issues = normalize_monitor_issues(detail["issues_raw"])
        lines.append(f"{idx}、{detail['单位']}{issues}")
    return "\n".join(lines)


def write_office_record(template_path, output_path, product_records, monitor_details, force):
    copy_output(template_path, output_path, force=force)
    workbook = load_workbook(output_path)
    ws = workbook.active
    product_by_short = {item["short"]: item for item in product_records}
    headers = {brigade_short(ws.cell(2, col).value): col for col in range(4, 15)}
    for short in OFFICE_ORDER:
        col = headers.get(short)
        if not col:
            continue
        if short in product_by_short:
            ws.cell(14, col).value = product_office_text(product_by_short[short])
            ws.cell(14, col).alignment = Alignment(wrap_text=True, vertical="top")
        ws.cell(16, col).value = monitor_office_text(short, monitor_details)
        ws.cell(16, col).alignment = Alignment(wrap_text=True, vertical="top")
    workbook.save(output_path)


def write_case_scores(template_path, output_path, product_records, monitor_scores, force):
    copy_output(template_path, output_path, force=force)
    import win32com.client

    product_scores = {item["short"]: item["score"] for item in product_records}
    excel = win32com.client.DispatchEx("Excel.Application")
    excel.Visible = False
    excel.DisplayAlerts = False
    workbook = excel.Workbooks.Open(str(Path(output_path).resolve()))
    try:
        ws = workbook.Worksheets(1)
        for row in range(3, 11):
            short = brigade_short(ws.Cells(row, 1).Value)
            if short in product_scores:
                ws.Cells(row, 2).Value = product_scores[short]
            if short in monitor_scores:
                ws.Cells(row, 11).Value = monitor_scores[short]["avg"]
        workbook.Save()
    finally:
        workbook.Close(False)
        excel.Quit()


def decode_legacy_text(raw):
    for encoding in ("gb18030", "utf-8", "utf-16"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("gb18030", errors="replace")


def extract_monitor_report_section(report_path):
    out_dir = Path(tempfile.mkdtemp(prefix="xf_report_history_"))
    try:
        subprocess.run(
            [find_soffice(), "--headless", "--convert-to", "txt:Text", "--outdir", str(out_dir), str(report_path)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        txt_files = list(out_dir.glob("*.txt"))
        if not txt_files:
            return ""
        text = decode_legacy_text(txt_files[0].read_bytes())
        start = text.find("（五）联网监测")
        end = text.find("三、下一步工作要求")
        if start < 0:
            return ""
        return text[start:end if end > start else len(text)]
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)


def collect_monitor_report_history(workspace_root, current_report_path=None):
    workspace_root = Path(workspace_root)
    current_resolved = str(Path(current_report_path).resolve()).lower() if current_report_path else ""
    counts = {}
    for report_path in workspace_root.glob("*月/*通报.doc"):
        if report_path.name.startswith("~$"):
            continue
        if current_resolved and str(report_path.resolve()).lower() == current_resolved:
            continue
        section = extract_monitor_report_section(report_path)
        for match in re.finditer(r"([\u4e00-\u9fff]{2})大队", section):
            short = match.group(1)
            counts[short] = counts.get(short, 0) + 1
    return counts


def select_monitor_report_cases(monitor_details, monitor_scores=None, history_counts=None):
    order_index = {name: idx for idx, name in enumerate(CASE_ORDER)}
    monitor_scores = monitor_scores or {}
    history_counts = history_counts or {}
    selected = []
    used = set()

    def sort_key(detail):
        avg = monitor_scores.get(detail["short"], {}).get("avg", 999)
        history = history_counts.get(detail["short"], 0)
        return (
            detail["score"],
            avg,
            history,
            order_index.get(detail["short"], 999),
            detail["index"],
        )

    for detail in sorted(
        monitor_details,
        key=sort_key,
    ):
        if detail["short"] in used:
            continue
        selected.append(detail)
        used.add(detail["short"])
        if len(selected) == 3:
            break
    return selected


def validate_text_quality(label, text):
    issues = []
    if re.search(r"[，,、；;。]{2,}", text):
        issues.append(f"{label}存在连续标点")
    if text.count("（") != text.count("）"):
        issues.append(f"{label}中文括号不闭合")
    if text.count("(") != text.count(")"):
        issues.append(f"{label}英文括号不闭合")
    bad_patterns = ["，。", "；。", "、。", "（)", "（）", "扣分：扣分：", "，，", "、、", "；；"]
    for pattern in bad_patterns:
        if pattern in text:
            issues.append(f"{label}存在异常片段：{pattern}")
    if re.search(r"[，,；;]\s*[。；;]", text):
        issues.append(f"{label}存在空错误项或空分句")
    return issues


def assert_report_text_quality(product_text, monitor_text):
    issues = []
    issues.extend(validate_text_quality("消防产品通报", product_text))
    issues.extend(validate_text_quality("联网监测通报", monitor_text))
    if issues:
        raise ValueError("通报文案自检未通过：" + "；".join(issues))


def build_report_sections(product_records, monitor_details, monitor_scores=None, history_counts=None):
    product_sentences = []
    for item in product_records:
        if item["no_case"]:
            product_sentences.append(f"{item['大队']}本月无消防产品监督检查案卷，产品成绩记0分")
        else:
            errors = "，".join(clean_error_line(err) for err in item["errors"] if clean_error_line(err))
            product_sentences.append(f"{item['大队']}{item['立卷人']}承办的{item['题名']}，{errors}")
    product_text = "；".join(product_sentences) + "。"

    monitor_sentences = []
    for detail in select_monitor_report_cases(monitor_details, monitor_scores, history_counts):
        monitor_sentences.append(
            f"{detail['大队']}{detail['联系人']}联系的{detail['单位']}"
            f"{detail['issues_report']}"
        )
    monitor_text = "。".join(monitor_sentences) + "。"
    assert_report_text_quality(product_text, monitor_text)
    return product_text, monitor_text


def ensure_word_fonts(word_app, fonts):
    available = {str(word_app.FontNames.Item(i)) for i in range(1, word_app.FontNames.Count + 1)}
    missing = [font for font in fonts if font not in available]
    if missing:
        raise RuntimeError("通报字体未安装：" + "、".join(missing))


def apply_report_fonts(doc):
    for paragraph in doc.Paragraphs:
        text = paragraph.Range.Text.replace("\r", "").replace("\x07", "").strip()
        if not text:
            continue
        is_title = text.startswith(("二、", "三、")) or text in {"（四）消防产品", "（五）联网监测"}
        font_name = REPORT_TITLE_FONT if is_title else REPORT_BODY_FONT
        paragraph.Range.Font.Name = font_name
        paragraph.Range.Font.NameFarEast = font_name
        paragraph.Range.Font.Bold = -1 if is_title else 0


def write_report_doc(template_path, output_path, product_records, monitor_details, force, monitor_scores=None, history_counts=None):
    if output_path.exists() and not force:
        raise FileExistsError(f"目标文件已存在，使用 --force 覆盖生成文件：{output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    import win32com.client

    product_text, monitor_text = build_report_sections(product_records, monitor_details, monitor_scores, history_counts)
    word = win32com.client.DispatchEx("Word.Application")
    word.Visible = False
    word.DisplayAlerts = False
    ensure_word_fonts(word, [REPORT_TITLE_FONT, REPORT_BODY_FONT])
    doc = word.Documents.Open(str(Path(template_path).resolve()), ReadOnly=True)
    try:
        raw = doc.Content.Text
        start_product = raw.find("（四）消防产品")
        start_monitor = raw.find("（五）联网监测")
        start_next = raw.find("三、下一步工作要求")
        if min(start_product, start_monitor, start_next) < 0:
            raise ValueError("通报模板缺少必要章节标记")
        before = raw[:start_product]
        after = raw[start_next:]
        new_text = (
            before
            + "（四）消防产品\r"
            + product_text
            + "\r"
            + "（五）联网监测\r"
            + monitor_text
            + "\r"
            + after
        )
        doc.Content.Text = new_text
        apply_report_fonts(doc)
        doc.SaveAs2(str(Path(output_path).resolve()), FileFormat=0)
    finally:
        doc.Close(False)
        word.Quit()


def generate_product_docs(product_records, product_output_dir, year, month, force):
    if product_output_dir.exists() and force:
        for path in product_output_dir.glob("*产品监督档案.doc"):
            path.unlink()
    product_output_dir.mkdir(parents=True, exist_ok=True)
    batch = build_product_batch(product_records, year, month)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as handle:
        json.dump(batch, handle, ensure_ascii=False, indent=2)
        batch_path = Path(handle.name)
    try:
        writer.batch_process(
            SKILL_DIR / "resources" / "空表.doc",
            batch_path,
            product_output_dir,
            month=month,
            export_pdf=False,
        )
    finally:
        try:
            batch_path.unlink()
        except OSError:
            pass


def run(args):
    month_dir = require_path(args.month_dir, "月份目录")
    product_register = find_one(month_dir, [f"*{args.month}月*产品巡查底册*.docx", "*产品巡查底册*.docx"], "产品巡查底册")
    network_dir = find_one(month_dir, ["*联网监测基础信息考评明细表"], "联网监测明细目录")
    network_stats = require_path(network_dir / "联网监测统计表.xls", "联网监测统计表")
    base_info = find_one(month_dir, ["*基础信息考评截图*.xls"], "基础信息考评截图")
    personal_template = find_one(month_dir, ["（模板）个人执法统计表*.xlsx", "个人执法统计表*.xlsx"], "个人执法统计表模板")
    report_output = month_dir / f"{args.year}年{args.month}月通报.doc"

    product_records = parse_product_register(product_register)
    monitor_scores = read_monitor_scores(network_stats)
    monitor_details = read_monitor_details(base_info, monitor_scores)
    validate_person_matches(personal_template, product_records, monitor_details)
    history_counts = collect_monitor_report_history(month_dir.parent, current_report_path=report_output)

    if args.dry_run:
        product_text, monitor_text = build_report_sections(product_records, monitor_details, monitor_scores, history_counts)
        print(
            json.dumps(
                {
                    "products": [
                        {
                            "大队": item["大队"],
                            "题名": item["题名"],
                            "立卷人": item["立卷人"],
                            "score": item["score"],
                            "no_case": item["no_case"],
                            "errors": item["errors"],
                        }
                        for item in product_records
                    ],
                    "monitor_avg": {k: v["avg"] for k, v in monitor_scores.items()},
                    "monitor_report_history_counts": history_counts,
                    "monitor_report_cases": select_monitor_report_cases(monitor_details, monitor_scores, history_counts),
                    "report_text_quality": {
                        "product": validate_text_quality("消防产品通报", product_text),
                        "monitor": validate_text_quality("联网监测通报", monitor_text),
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    product_dir = month_dir / f"{args.month}月消防产品监督成绩"
    if personal_template.resolve() == (month_dir / f"个人执法统计表{args.year}{args.month:02d}.xlsx").resolve():
        raise FileNotFoundError("月份目录缺少个人执法统计表模板，不能用已生成成品覆盖自身")
    generate_product_docs(product_records, product_dir, args.year, args.month, args.force)
    write_product_summary(
        month_dir / "（模板）产品监督成绩总表.xlsx",
        product_dir / "产品监督成绩总表.xlsx",
        product_records,
        args.force,
    )
    personal_warnings = write_personal_stats(
        personal_template,
        month_dir / f"个人执法统计表{args.year}{args.month:02d}.xlsx",
        product_records,
        monitor_details,
        args.month,
        args.force,
    )
    write_office_record(
        month_dir / "（模板）科室月考核情况记录表.xlsx",
        month_dir / f"{args.month}月科室月考核情况记录表.xlsx",
        product_records,
        monitor_details,
        args.force,
    )
    write_case_scores(
        month_dir / "(模板-成绩汇总)消防监督管理系统消防执法质量（个案成绩）.xls",
        month_dir / f"消防监督管理系统消防执法质量（{args.month}月个案成绩）.xls",
        product_records,
        monitor_scores,
        args.force,
    )
    write_report_doc(
        month_dir / "(样例)xxxx年x月通报.doc",
        report_output,
        product_records,
        monitor_details,
        args.force,
        monitor_scores,
        history_counts,
    )

    summary = {
        "product_dir": str(product_dir),
        "product_scores": {item["short"]: item["score"] for item in product_records},
        "monitor_avg": {k: v["avg"] for k, v in monitor_scores.items()},
        "personal_warnings": personal_warnings,
        "monitor_report_cases": [
            {
                "大队": item["大队"],
                "联系人": item["联系人"],
                "单位": item["单位"],
                "score": item["score"],
            }
            for item in select_monitor_report_cases(monitor_details, monitor_scores, history_counts)
        ],
        "monitor_report_history_counts": history_counts,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description="按月生成产品与联网监测考评成绩登记文件。")
    parser.add_argument("--month-dir", required=True, help="月份目录，例如 C:\\...\\5月")
    parser.add_argument("--year", type=int, required=True, help="年份，例如 2026")
    parser.add_argument("--month", type=int, required=True, help="月份，例如 5")
    parser.add_argument("--force", action="store_true", help="覆盖已生成的同名成品文件")
    parser.add_argument("--dry-run", action="store_true", help="只解析并输出数据，不写入文件")
    args = parser.parse_args()
    try:
        run(args)
    except HumanReviewRequired as exc:
        print(
            json.dumps(
                {
                    "status": "human_review_required",
                    "message": "个人成绩登记涉及未在表格出现的人员，已暂停任务，请人工核对。",
                    "issues": exc.issues,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        raise SystemExit(2)


if __name__ == "__main__":
    main()
