"""显式执行的旧版Word表格诊断；unittest发现测试时不打开Office或历史业务文件。"""
import argparse
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description='只读检查明确指定的Word表格单元格数量。')
    parser.add_argument('document')
    args = parser.parse_args()
    path = Path(args.document).resolve(strict=True)
    import win32com.client
    word = win32com.client.DispatchEx('Word.Application')
    word.Visible = False
    doc = None
    try:
        doc = word.Documents.Open(str(path), ReadOnly=True, AddToRecentFiles=False, Visible=False)
        print('Table cells:', doc.Tables(1).Range.Cells.Count)
    finally:
        if doc is not None:
            doc.Close(False)
        word.Quit()


if __name__ == '__main__':
    main()
