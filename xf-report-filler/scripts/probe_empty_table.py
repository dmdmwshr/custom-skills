import os
import sys
import json

try:
    import win32com.client
    word = win32com.client.Dispatch('Word.Application')
    doc = word.Documents.Open(r'c:\Users\12070\Desktop\项目开发\产品监督信息填报自动化\空表.doc')
    table = doc.Tables(1)
    
    cells_data = []
    cells = table.Range.Cells
    
    for i in range(1, cells.Count + 1):
        cell = cells(i)
        r = cell.RowIndex
        c = cell.ColumnIndex
        text = cell.Range.Text.replace('\r', '').replace('\x07', '').replace('\x0b', '').strip()
        
        cells_data.append({
            "idx": i,
            "row": r,
            "col": c,
            "text": text,
            "is_empty": text == ""
        })
            
    doc.Close(False)
    word.Quit()
    
    with open('empty_table_structure.json', 'w', encoding='utf-8') as f:
        json.dump(cells_data, f, ensure_ascii=False, indent=2)
        
    print("Exported to empty_table_structure.json")
except Exception as e:
    print('Error:', e)
    try: word.Quit()
    except: pass
