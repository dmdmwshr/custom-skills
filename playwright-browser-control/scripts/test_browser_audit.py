import tempfile
import unittest
from pathlib import Path

import browser_audit as audit


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.current={'execution_id':'now','profile_key':'a'*64,'group_id':7,'connection_id':1,
            'owned_tab_ids':[11],'project_name':'current'}
        self.epoch={'pid':123,'start':'current'}
        self.native={'schema':'NativeBrowserInventoryV1','captured_at':'now',
            'groups':[{'id':7,'title':'own'},{'id':8,'title':'same name'},{'id':9,'title':'open'}],
            'tabs':[{'id':11,'group_id':7,'kind':'web_page'},{'id':12,'group_id':8,'kind':'web_page'},
                    {'id':13,'group_id':9,'kind':'web_page'},{'id':14,'group_id':-1,'kind':'web_page'},
                    {'id':15,'group_id':-1,'kind':'automation_control'}],
            'connections':[{'id':1,'tab_ids':[11]},{'id':2,'tab_ids':[12]}]}

    def report(self,records=None,saved=None):
        return audit.classify(self.native,saved or {},self.current,{'executions':records or {}},self.epoch)

    def test_live_connection_group_and_normal_pages_are_distinguished(self):
        result=self.report()
        self.assertEqual([g['category'] for g in result['groups']],
            ['own_execution','other_connected_unregistered','open_group_without_connection'])
        categories={p['id']:p['category'] for p in result['pages']}
        self.assertEqual(categories[14],'ordinary_ungrouped_page')
        self.assertEqual(categories[15],'unconnected_control_page_needs_reconciliation')
        self.assertIn('classification_only',result['cleanup_authority'])

    def test_title_collision_or_old_browser_cannot_claim_other_owner(self):
        old={'execution_id':'old','profile_key':'a'*64,'group_id':8,'connection_id':2,
            'owned_tab_ids':[12],'browser_epoch':{'pid':99},'project_name':'current'}
        result=self.report({'old':old})
        self.assertEqual(result['groups'][1]['category'],'other_connected_unregistered')
        self.assertEqual(result['previous_executions'][0]['category'],'history_other_or_unverified_browser')
        old['browser_epoch']=self.epoch
        self.assertEqual(self.report({'old':old})['groups'][1]['category'],'other_connected_registered')

    def test_exact_historical_native_page_remains_even_after_ungrouping(self):
        old={'execution_id':'old','profile_key':'a'*64,'group_id':10,'connection_id':9,
            'owned_tab_ids':[15],'browser_epoch':self.epoch,'state':'cleanup_pending',
            'cleanup':{'tab_ids':[15]}}
        result=self.report({'old':old})
        self.assertEqual(result['previous_executions'][0]['remaining_tab_ids'],[15])
        self.assertEqual(result['pages'][-1]['category'],'previous_execution_page_remains')

    def test_saved_history_is_not_a_live_native_group(self):
        result=self.report(saved={'closed':{'title':'same name','hasLocalGroupId':False},
            'marker':{'title':'open','hasLocalGroupId':True}})
        self.assertEqual(result['summary']['native_groups'],3)
        self.assertEqual([s['category'] for s in result['saved_records']],
            ['saved_history_without_native_binding','saved_record_with_local_marker'])

    def test_a_live_registered_group_is_open_even_without_a_saved_local_marker(self):
        peer={'execution_id':'peer','profile_key':'a'*64,'group_id':8,'connection_id':2,
            'owned_tab_ids':[12],'browser_epoch':self.epoch,'project_name':'peer','saved_group_ids':['peer-saved']}
        result=self.report({'peer':peer},{'peer-saved':{'title':'same name','hasLocalGroupId':False}})
        self.assertEqual(result['saved_records'][0]['category'],'other_saved_record_verified_open')
        self.assertEqual(result['saved_records'][0]['native_binding']['group_id'],8)

    def test_registry_keeps_distinct_executions_and_rejects_escaping_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            first={**self.current,'conversation_id':'real','state':'active'}
            second={**first,'execution_id':'next'}
            audit.register(Path(directory),first,Path(directory)/'receipt.json')
            audit.register(Path(directory),second,Path(directory)/'receipt.json')
            self.assertEqual(set(audit.registry(Path(directory),'a'*64)['executions']),{'now','next'})
            with self.assertRaises(ValueError):audit.registry(Path(directory),'../escape')

    def test_unavailable_native_snapshot_never_certifies_a_sweep(self):
        with self.assertRaises(ValueError):audit.classify({}, {}, self.current, {}, self.epoch)

    def pending_history(self, root, **changes):
        path=root/'project/.playwright/groups/chrome-old.json'
        value={**self.current,'lifecycle_schema':'BrowserExecutionV1','execution_id':'old',
            'project_root':str(path.parent.parent.parent.resolve()),'conversation_id':'real',
            'state':'cleanup_pending','browser_epoch':self.epoch,'group_id':99,'owned_tab_ids':[99],
            'cleanup':{'scheduled':True,'tab_ids':[99,100],'saved_group_status':'verified_absent',
                'connection_release':'detached'},'end_audit':{'end_duties':{}},**changes}
        audit.save_json(path,value);audit.register(root,value,path)
        current={**self.current,'conversation_id':'real'}
        report=audit.classify(self.native,{},current,audit.registry(root,current['profile_key']),self.epoch)
        return path,current,report

    def test_fresh_sweep_settles_known_own_cleanup_and_keeps_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);path,current,report=self.pending_history(root)
            self.assertEqual(audit.complete_verified_own_history(root,current,report,self.epoch),['old'])
            value=audit.read_json(path)
            self.assertEqual(value['state'],'completed')
            self.assertEqual(value['cleanup']['native_pages_status'],'verified_absent')
            self.assertEqual(audit.read_json(path.parent.parent/'history/chrome-old/old.json'),value)
            self.assertEqual(audit.complete_verified_own_history(root,current,report,self.epoch),[])

    def test_sweep_does_not_settle_unknown_work_another_conversation_or_unverified_epoch(self):
        for changes in ({'state':'unknown'},{'conversation_id':'someone-else'},{'browser_epoch':None}):
            with self.subTest(changes=changes),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);path,current,report=self.pending_history(root,**changes)
                before=path.read_bytes()
                self.assertEqual(audit.complete_verified_own_history(root,current,report,self.epoch),[])
                self.assertEqual(path.read_bytes(),before)
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);path,current,report=self.pending_history(root)
            self.assertEqual(audit.complete_verified_own_history(root,current,report,None),[])

    def test_live_residual_or_changed_original_receipt_cannot_be_settled(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);path,current,report=self.pending_history(root,owned_tab_ids=[15])
            self.assertEqual(audit.complete_verified_own_history(root,current,report,self.epoch),[])
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);path,current,report=self.pending_history(root)
            original=audit.read_json(path);original['owned_tab_ids'].append(101);audit.save_json(path,original)
            self.assertEqual(audit.complete_verified_own_history(root,current,report,self.epoch),[])
            self.assertEqual(audit.read_json(path)['state'],'cleanup_pending')


if __name__=='__main__':unittest.main()
