import os
import win32com.client
word = win32com.client.Dispatch('Word.Application')
try:
    doc = word.Documents.Open(r'c:\Users\12070\Desktop\项目开发\产品监督信息填报自动化\宜兴（1月档案）.doc')
    table = doc.Tables(1)
    cells = table.Range.Cells
    print('Testing table.Range.Cells Count:', cells.Count)
    doc.Close(False)
except Exception as e:
    print('Error:', e)
finally:
    word.Quit()
