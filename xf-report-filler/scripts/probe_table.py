import os
import sys

# 编写一个小型的探针脚本查看实际 Cell 坐标
try:
    import win32com.client
    word = win32com.client.Dispatch('Word.Application')
    doc = word.Documents.Open(r'c:\Users\12070\Desktop\项目开发\产品监督信息填报自动化\宜兴（1月档案）.doc')
    table = doc.Tables(1)
    
    print("--- Probing Rows ---")
    cells = table.Range.Cells
    print("Total cells in table 1:", cells.Count)
    
    # 让我们记录前 300 个 cell 对应的 Row 和 Column 并在行开始时打印内容
    current_row = -1
    for i in range(1, min(100, cells.Count + 1)):
        cell = cells(i)
        r = cell.RowIndex
        c = cell.ColumnIndex
        text = cell.Range.Text.replace('\r', '').replace('\x07', '').strip()
        print(f"Cell {i} (R:{r}, C:{c}): {repr(text)[:30]}")
            
    doc.Close(False)
    word.Quit()
except Exception as e:
    print('Error:', e)
    try: word.Quit()
    except: pass
