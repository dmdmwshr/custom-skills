import os
import sys
import json

# 由于旧版doc以及合并单元格的特殊性，我们采用遍历所有Cell的方式，
# 通过Cell的 RowIndex 和 ColumnIndex 来进行判断和覆盖。
# 探针数据表明:
# [基础信息] 评查组织单位 (R:2, C:2) | 评查人 (R:2, C:4) | 复核人 (R:2, C:6)
# [基础信息] 被检查单位 (R:3, C:2) | 评查日期 (R:3, C:4) | 评查形式 (R:3, C:6)
# [卷宗信息] 题名 (R:6, C:1) | 编号 (R:6, C:2) | 立卷人 (R:6, C:3) | 检查人 (R:6, C:4)
# [总计得分] 合计扣分 (R:28, C:3) | 评级判定 (R:28, C:5)
# [总计得分] 实际得分 (R:29, C:2)

# [基本要素 1-4] RowIndex: 8, 9, 10, 11
# 列分布: 评查结果 (C:3) | 复核情况 (C:4) | 情况说明 (C:5)

# [一般要素 1-15] RowIndex: 13 到 27
# 列分布: 扣分情况 (C:3) | 复核情况 (C:4) | 扣分说明 (C:5)

CELL_MAPPING = {
    # basic_info
    "basic_info": {
        "评查组织单位": (2, 2),
        "评查人": (2, 4),
        "复核人": (2, 6),
        "被检查单位": (3, 2),
        "评查日期": (3, 4),
        "评查形式": (3, 6)
    },
    # juanzong
    "卷宗信息": {
        "题名": (6, 1),
        "编号": (6, 2),
        "立卷人": (6, 3),
        "检查人": (6, 4)
    },
    # summary
    "summary": {
        "合计扣分": (28, 3),
        "评级判定": (28, 5),
        "实际得分": (29, 2)
    }
}

def update_nested_dict(d, u):
    for k, v in u.items():
        if isinstance(v, dict):
            d[k] = update_nested_dict(d.get(k, {}), v)
        else:
            d[k] = v
    return d

def write_to_cells(cells, final_data):
    # 构建一个快速反查字典 (r, c) -> text_to_write
    write_plan = {}
    
    # 填充普通字段映射
    for category in ["basic_info", "summary"]:
        for key, (r, c) in CELL_MAPPING[category].items():
            val = final_data.get(category, {}).get(key)
            if val is not None:
                write_plan[(r, c)] = str(val)
                
    # 填充卷宗信息
    for key, (r, c) in CELL_MAPPING["卷宗信息"].items():
        val = final_data.get("basic_info", {}).get("卷宗信息", {}).get(key)
        if val is not None:
            write_plan[(r, c)] = str(val)

    # 填充动态扣分项
    eval_records = final_data.get("evaluation_records", {})
    eval_defaults = final_data.get("evaluation_records_defaults", {})
    
    # 基本要素 (Row 8-11)
    for el in eval_records.get("基本要素", []):
        idx = int(el.get("序号", 0))
        if 1 <= idx <= 4:
            r = 7 + idx
            write_plan[(r, 3)] = el.get("评查结果", eval_defaults.get("评查结果", ""))
            write_plan[(r, 4)] = el.get("复核情况", eval_defaults.get("复核情况", ""))
            write_plan[(r, 5)] = el.get("情况说明", eval_defaults.get("情况说明", ""))

    # 一般要素 (Row 13-27)
    for el in eval_records.get("一般要素", []):
        idx = int(el.get("序号", 0))
        if 1 <= idx <= 15:
            r = 12 + idx
            write_plan[(r, 3)] = el.get("扣分情况", eval_defaults.get("扣分情况", ""))
            write_plan[(r, 4)] = el.get("复核情况", eval_defaults.get("复核情况", ""))
            write_plan[(r, 5)] = el.get("扣分说明", eval_defaults.get("扣分说明", ""))
            
    # 执行写入
    cell_count = cells.Count
    for i in range(1, cell_count + 1):
        try:
            cell = cells(i)
            r = cell.RowIndex
            c = cell.ColumnIndex
            if (r, c) in write_plan:
                cell.Range.Text = write_plan[(r, c)]
        except Exception as e:
            # 忽略一些无法写入或报错的合并死角
            pass

def process_single_record(word_app, template_path, output_doc, output_pdf, default_data, user_data):
    final_data = update_nested_dict(default_data.copy(), user_data)
    
    doc = word_app.Documents.Open(template_path)
    try:
        table = doc.Tables(1)
        write_to_cells(table.Range.Cells, final_data)
        
        # 另存为 Doc
        doc.SaveAs(output_doc)
        print(f"✅ Generated DOC: {os.path.basename(output_doc)}")
        
        # 另存为 PDF (17 = wdExportFormatPDF)
        if output_pdf:
            doc.ExportAsFixedFormat(output_pdf, 17)
            print(f"✅ Generated PDF: {os.path.basename(output_pdf)}")
            
    except Exception as e:
        print(f"❌ Error processing record: {e}")
    finally:
        doc.Close(False)

def batch_process(template_doc, batch_json_path, default_json_path, output_dir):
    with open(default_json_path, 'r', encoding='utf-8') as f:
        default_data = json.load(f)
    with open(batch_json_path, 'r', encoding='utf-8') as f:
        batch_data = json.load(f)

    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    import win32com.client
    word = win32com.client.Dispatch('Word.Application')
    word.Visible = False
    
    try:
        for idx, record in enumerate(batch_data):
            unit_name = record.get("basic_info", {}).get("被检查单位", f"未知单位_{idx}")
            doc_name = record.get("basic_info", {}).get("卷宗信息", {}).get("题名", "未知案卷")
            safe_name = "".join([c for c in f"{unit_name}_{doc_name}" if c.isalpha() or c.isdigit() or c in ['_', '-']]).rstrip()
            if not safe_name: safe_name = f"record_{idx}"
            
            out_doc = os.path.abspath(os.path.join(output_dir, f"{safe_name}.doc"))
            out_pdf = os.path.abspath(os.path.join(output_dir, f"{safe_name}.pdf"))
            
            process_single_record(word, os.path.abspath(template_doc), out_doc, out_pdf, default_data, record)
    finally:
        word.Quit()

if __name__ == "__main__":
    if len(sys.argv) < 5:
        print("Usage: python batch_generate.py <template.doc> <batch_data.json> <default.json> <output_dir>")
        sys.exit(1)
        
    batch_process(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4])
