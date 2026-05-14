import os
import sys
import json
import argparse
from datetime import datetime

CELL_MAPPING = {
    # 只映射空白的地方
    "fields": {
        "评查组织单位": (1, 2),
        "评查人": (1, 4),
        "复核人": (1, 6),
        "被检查单位": (2, 2),
        "评查日期": (2, 4),
        "评查形式": (2, 6),
        "题名": (5, 1),
        "编号": (5, 2),
        "立卷人": (5, 3),
        "检查人": (5, 4),
        "立卷时间": (5, 5),
        "合计扣分": (28, 3),
        "实际得分": (29, 3)
    }
}

def auto_calculate_scores(record):
    """自动遍历扣分项，计算合计扣分和实际得分"""
    total_deduction = 0.0
    fields = record.get("fields", {})
    score_override = record.get("score_override", fields.get("score_override"))

    if record.get("no_case"):
        if "fields" not in record:
            record["fields"] = {}
        record["fields"]["合计扣分"] = "10"
        record["fields"]["实际得分"] = "0"
        return
    
    # 解析基本要素扣分
    basic_elements = record.get("基本要素", {})
    for _, d in basic_elements.items():
        if isinstance(d, dict) and "扣分情况" in d:
            try:
                score_str = str(d["扣分情况"]).strip()
                if score_str:
                    total_deduction += float(score_str)
            except ValueError:
                pass
                
    # 解析一般要素扣分
    general_elements = record.get("一般要素", {})
    for _, d in general_elements.items():
        if isinstance(d, dict) and "扣分情况" in d:
            try:
                score_str = str(d["扣分情况"]).strip()
                if score_str:
                    total_deduction += float(score_str)
            except ValueError:
                pass
                
    # 兼容通过 deductions 数组传值的模式
    deductions = record.get("deductions", [])
    for d in deductions:
        if isinstance(d, dict) and "扣分情况" in d:
            try:
                score_str = str(d["扣分情况"]).strip()
                if score_str:
                    total_deduction += float(score_str)
            except ValueError:
                pass

    if "fields" not in record:
        record["fields"] = {}

    if score_override not in (None, ""):
        try:
            actual_score = float(score_override)
            total_deduction = max(0.0, 10.0 - actual_score)
        except ValueError:
            actual_score = 10.0 - total_deduction
    else:
        actual_score = 10.0 - total_deduction

    record["fields"]["合计扣分"] = str(round(total_deduction, 1)).rstrip("0").rstrip(".")
    record["fields"]["实际得分"] = str(round(actual_score, 1)).rstrip("0").rstrip(".")

def safe_write(cell, text):
    """安全锁机制：只在单元格内容完全为空时才执行写入，防止挤占原有字和撑爆排版"""
    val_to_write = str(text).strip()
    if not val_to_write:
        return # 不写空字符串，保持原样
    
    current_text = cell.Range.Text.replace('\r', '').replace('\x07', '').replace('\x0b', '').strip()
    # 如果原本就是空的（没有我们不认识的奇怪印刷字），才写入
    if current_text == "":
        cell.Range.Text = val_to_write
        # print(f"  [SafeWrite] R:{cell.RowIndex}, C:{cell.ColumnIndex} -> {text}")
    else:
        # print(f"  [Skip] R:{cell.RowIndex}, C:{cell.ColumnIndex} NO-EMPTY: '{current_text}' -> Discard {text}")
        pass

def process_single_record(word_app, template_path, output_doc, output_pdf, record, default_fields):
    # 自动计算分数
    auto_calculate_scores(record)
    
    doc = word_app.Documents.Open(template_path)
    try:
        table = doc.Tables(1)
        cells = table.Range.Cells
        
        write_plan = {}
        
        fields = record.get("fields", {})
        
        # 1. 优先加载默认值，如果 fields 里没有则使用默认值
        merged_fields = {}
        merged_fields.update(default_fields)
        for k, v in fields.items():
            if v is not None and str(v).strip() != "":
                merged_fields[k] = v
                
        for key, (r, c) in CELL_MAPPING["fields"].items():
            if key in merged_fields and merged_fields[key] is not None:
                write_plan[(r, c)] = merged_fields[key]
                
        # 2. 解析 针对性嵌套字典
        basic_elements = record.get("基本要素", {})
        for dict_key, d in basic_elements.items():
            idx_str = dict_key.split('_')[0]
            if idx_str.isdigit():
                idx = int(idx_str)
                if 1 <= idx <= 4:
                    r = 7 + idx
                    if "评查结果" in d and str(d["评查结果"]).strip(): write_plan[(r, 3)] = d["评查结果"]
                    if "复核情况" in d and str(d["复核情况"]).strip(): write_plan[(r, 4)] = d["复核情况"]
                    if "情况说明" in d and str(d["情况说明"]).strip(): write_plan[(r, 5)] = d["情况说明"]
                    
        general_elements = record.get("一般要素", {})
        for dict_key, d in general_elements.items():
            idx_str = dict_key.split('_')[0]
            if idx_str.isdigit():
                idx = int(idx_str)
                if 1 <= idx <= 15:
                    r = 12 + idx
                    if "扣分情况" in d and str(d["扣分情况"]).strip(): write_plan[(r, 3)] = d["扣分情况"]
                    if "复核情况" in d and str(d["复核情况"]).strip(): write_plan[(r, 4)] = d["复核情况"]
                    if "扣分说明" in d and str(d["扣分说明"]).strip(): write_plan[(r, 5)] = d["扣分说明"]

        # 为了兼容以往如果存在 "deductions" 单独数组的模式（可兼容）
        deductions = record.get("deductions", [])
        for d in deductions:
            d_type = d.get("type", "")
            idx = int(d.get("序号", 0))
            if d_type == "基本要素" and 1 <= idx <= 4:
                r = 7 + idx
                if "评查结果" in d and str(d["评查结果"]).strip(): write_plan[(r, 3)] = d["评查结果"]
                if "复核情况" in d and str(d["复核情况"]).strip(): write_plan[(r, 4)] = d["复核情况"]
                if "情况说明" in d and str(d["情况说明"]).strip(): write_plan[(r, 5)] = d["情况说明"]
            elif d_type == "一般要素" and 1 <= idx <= 15:
                r = 12 + idx
                if "扣分情况" in d and str(d["扣分情况"]).strip(): write_plan[(r, 3)] = d["扣分情况"]
                if "复核情况" in d and str(d["复核情况"]).strip(): write_plan[(r, 4)] = d["复核情况"]
                if "扣分说明" in d and str(d["扣分说明"]).strip(): write_plan[(r, 5)] = d["扣分说明"]

        # 3. 执行安全写入
        for i in range(1, cells.Count + 1):
            cell = cells(i)
            r = cell.RowIndex
            c = cell.ColumnIndex
            if (r, c) in write_plan:
                safe_write(cell, write_plan[(r, c)])
                
        # 另存为 Doc
        doc.SaveAs(output_doc)
        print(f"Generated DOC: {os.path.basename(output_doc)}")
        
        # 另存为 PDF (只为了给 AI 验证用)
        if output_pdf:
            doc.ExportAsFixedFormat(output_pdf, 17)
            print(f"Generated PDF: {os.path.basename(output_pdf)}")
            
    except Exception as e:
        print(f"Error processing record: {e}")
    finally:
        doc.Close(False)

def batch_process(template_doc, batch_json_path, output_dir, month=None, export_pdf=True):
    with open(batch_json_path, 'r', encoding='utf-8') as f:
        batch_data = json.load(f)

    # 加载外部默认值配置 (如果存在的话)
    default_fields = {}
    skill_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    default_cfg_path = os.path.join(skill_dir, "resources", "default_fields.json")
    if os.path.exists(default_cfg_path):
        try:
            with open(default_cfg_path, 'r', encoding='utf-8') as df:
                default_fields = json.load(df)
        except Exception as e:
            print(f"Warning: Failed to load default_fields.json ({e})")

    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    import win32com.client
    word = win32com.client.Dispatch('Word.Application')
    word.Visible = False
    
    try:
        if month is None:
            # 兼容旧行为：未显式传入月份时，仍按系统上一个月份生成文件名。
            current_month = datetime.now().month
            last_month = 12 if current_month == 1 else current_month - 1
        else:
            last_month = int(month)

        for idx, record in enumerate(batch_data):
            # 获取用户填入的被检查单位，比如 "滨湖大队" -> 提取 "滨湖大队"
            # 无论 json 里写了什么，我们都强制按照标准规范重新推导或覆盖文件名
            unit_name = record.get("fields", {}).get("被检查单位", f"未知大队_{idx}")
            
            # 最终期望的文件名格式：滨湖大队（X月产品监督档案）
            formatted_name = f"{unit_name}（{last_month}月产品监督档案）"
            
            # 为了系统安全，依然保留一次去除非法字符的操作
            safe_name = "".join([c for c in formatted_name if c not in '\\/:*?"<>|']).rstrip()
            
            out_doc = os.path.abspath(os.path.join(output_dir, f"{safe_name}.doc"))
            out_pdf = os.path.abspath(os.path.join(output_dir, f"{safe_name}.pdf")) if export_pdf else None
            
            process_single_record(word, os.path.abspath(template_doc), out_doc, out_pdf, record, default_fields)
    finally:
        word.Quit()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="批量生成消防产品档案质量明细表。")
    parser.add_argument("template_doc", help="空白 .doc 模板路径")
    parser.add_argument("batch_data_json", help="批量 JSON 数据路径")
    parser.add_argument("output_dir", help="输出目录")
    parser.add_argument("--month", type=int, help="显式指定文件名中的月份，例如 5")
    parser.add_argument("--no-pdf", action="store_true", help="不额外导出 PDF")
    args = parser.parse_args()

    batch_process(
        args.template_doc,
        args.batch_data_json,
        args.output_dir,
        month=args.month,
        export_pdf=not args.no_pdf,
    )
