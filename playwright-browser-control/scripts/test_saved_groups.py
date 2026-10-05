import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import saved_groups as saved


def varint(value):
    result=bytearray()
    while value>=128:result.append((value&127)|128);value>>=7
    result.append(value);return bytes(result)


def field(number,value):
    return varint(number*8+2)+varint(len(value))+value


def group(guid,title):
    specifics=field(1,guid.encode())+field(4,field(2,title.encode()))
    return field(2,specifics)


class SavedGroupsTests(unittest.TestCase):
    def test_current_log_tombstone_removes_old_table_group(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'CURRENT').write_text('MANIFEST-1\n')
            table=[(b'saved_tab_group-dt-a',10,1,group('a','own')),
                   (b'saved_tab_group-dt-b',11,1,group('b','other'))]
            with patch.object(saved,'active_files',return_value=('MANIFEST-1',{1:0},{2})), \
                 patch.object(saved,'table_rows',return_value=table), \
                 patch.object(saved,'batch_rows',return_value=[(b'saved_tab_group-dt-a',12,0,b'')]):
                result=saved.snapshot(root)
            self.assertNotIn('a',result);self.assertEqual(result['b']['title'],'other')

    def test_inactive_tables_are_not_used_as_live_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'CURRENT').write_text('MANIFEST-1\n');(root/'000099.ldb').touch()
            with patch.object(saved,'active_files',return_value=('MANIFEST-1',{1:0},set())), \
                 patch.object(saved,'table_rows',return_value=[]) as table, \
                 patch.object(saved,'batch_rows',return_value=[]):
                self.assertEqual(saved.snapshot(root),{})
            self.assertEqual(table.call_args.args[0].name,'000001.sst')

    def test_manifest_change_prevents_claiming_false_absence(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'CURRENT').write_text('MANIFEST-2\n')
            with patch.object(saved,'active_files',return_value=('MANIFEST-1',{},set())), \
                 patch.object(saved,'batch_rows',return_value=[]):
                with self.assertRaisesRegex(ValueError,'manifest_changed'):saved.snapshot(root)


if __name__=='__main__':unittest.main()
