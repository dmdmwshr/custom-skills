"""Exercise rename safety boundaries without accessing a live application."""

import copy
import json
import unittest

from verify_project_rename import VerificationError, verify


class RenameVerificationTests(unittest.TestCase):
    def setUp(self):
        self.before = {"projects": [
            {"projectId": "desktop-1", "projectKind": "local", "label": "工作台", "path": "D:\\ws\\workbench", "hostId": "local", "isGitRepository": True},
            {"projectId": "desktop-2", "projectKind": "local", "label": "保留", "path": "D:\\ws\\keep", "hostId": "local", "isGitRepository": False},
        ]}
        self.after = copy.deepcopy(self.before)
        self.after["projects"][0]["label"] = "03-11 Windows工作台（Codex优先）"
        self.plan = [{"projectId": "desktop-1", "label": "工作台", "target": self.after["projects"][0]["label"]}]

    def test_success_allows_list_order_change_but_not_metadata_change(self):
        self.after["projects"].reverse()
        result = verify(self.before, self.after, self.plan)
        self.assertEqual(result["renamed"], 1)
        self.assertIn("runtime_cwd", result["not_verified"])

    def test_mcp_text_wrapper(self):
        wrapped = {"content": [{"type": "text", "text": json.dumps(self.before)}]}
        self.assertEqual(verify(wrapped, self.after, self.plan)["projects"], 2)

    def test_wrong_record_view_rejected(self):
        self.after["projects"][0]["projectId"] = "native-1"
        with self.assertRaises(VerificationError):
            verify(self.before, self.after, self.plan)

    def test_native_shape_not_silently_converted(self):
        with self.assertRaises(VerificationError):
            verify({"data": self.before["projects"]}, self.after, self.plan)

    def test_path_or_host_change_rejected(self):
        for field, value in [("path", "D:\\wrong"), ("hostId", "different-host")]:
            after = copy.deepcopy(self.after)
            after["projects"][0][field] = value
            with self.subTest(field=field), self.assertRaises(VerificationError):
                verify(self.before, after, self.plan)

    def test_duplicate_id_rejected(self):
        self.after["projects"].append(copy.deepcopy(self.after["projects"][0]))
        with self.assertRaises(VerificationError):
            verify(self.before, self.after, self.plan)

    def test_unplanned_project_change_rejected(self):
        self.after["projects"][1]["label"] = "无授权改名"
        with self.assertRaises(VerificationError):
            verify(self.before, self.after, self.plan)

    def test_truncated_or_appended_name_rejected(self):
        for value in [self.plan[0]["target"][:-1], "工作台" + self.plan[0]["target"]]:
            after = copy.deepcopy(self.after)
            after["projects"][0]["label"] = value
            with self.subTest(value=value), self.assertRaises(VerificationError):
                verify(self.before, after, self.plan)

    def test_stale_plan_or_duplicate_plan_rejected(self):
        bad = copy.deepcopy(self.plan)
        bad[0]["label"] = "不是原名"
        for plan in [bad, self.plan + self.plan, [], [{**self.plan[0], "path": "D:\\wrong"}]]:
            with self.subTest(plan=plan), self.assertRaises(VerificationError):
                verify(self.before, self.after, plan)

    def test_new_unrelated_metadata_rejected(self):
        self.after["projects"][0]["newField"] = True
        with self.assertRaises(VerificationError):
            verify(self.before, self.after, self.plan)

    def test_error_marked_direct_snapshot_rejected(self):
        self.before["isError"] = True
        with self.assertRaises(VerificationError):
            verify(self.before, self.after, self.plan)

    def test_error_marked_unwrapped_snapshot_rejected(self):
        self.before["isError"] = True
        wrapped = {"content": [{"type": "text", "text": json.dumps(self.before)}]}
        with self.assertRaises(VerificationError):
            verify(wrapped, self.after, self.plan)

    def test_unrelated_remote_project_preserved_in_whole_snapshot(self):
        remote = {"projectId": "remote-1", "projectKind": "cloud", "label": "云项目", "path": None}
        self.before["projects"].append(copy.deepcopy(remote))
        self.after["projects"].append(copy.deepcopy(remote))
        self.assertEqual(verify(self.before, self.after, self.plan)["projects"], 3)
        self.after["projects"][-1]["label"] = "意外改动"
        with self.assertRaises(VerificationError):
            verify(self.before, self.after, self.plan)

    def test_remote_rename_target_rejected(self):
        self.before["projects"][0]["projectKind"] = "cloud"
        self.after["projects"][0]["projectKind"] = "cloud"
        with self.assertRaises(VerificationError):
            verify(self.before, self.after, self.plan)


if __name__ == "__main__":
    unittest.main()
