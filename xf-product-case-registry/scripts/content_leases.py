"""Persist the owned export lease separately from immutable body evidence."""

from __future__ import annotations


class Checkpoint:
    def __init__(self, api, path, binding):
        self.api, self.path, self.binding = api, path, binding
        self.value = api.read_json(path) if path and path.exists() else {}
        if self.value and (
            self.value.get("schemaVersion") != "ContentLeaseV1"
            or self.value.get("binding", {}).get("projectNo") != binding["projectNo"]
            or self.value.get("binding", {}).get("caseId") != binding["caseId"]
            or self.value.get("binding", {}).get("origin") != binding["origin"]
            or self.value.get("status") not in {"ACTIVE", "RELEASED", "EXPIRED"}
            or not isinstance(self.value.get("lease"), dict)
            or not isinstance(self.value.get("lease", {}).get("leaseId"), str)
            or not self.value.get("lease", {}).get("leaseId")
        ):
            raise api.RegistryError("正文保留断点身份不一致")
        if self.value.get("status") == "ACTIVE" and self.value.get("binding") != binding:
            raise api.RegistryError("正文保留期间清单或内容代际变化，先核对原保留任务")

    def save(self, projection):
        if not projection.get("leaseId"):
            return
        if (
            projection.get("caseId") != self.binding["caseId"]
            or not isinstance(projection["leaseId"], str)
            or not projection["leaseId"]
        ):
            raise self.api.RegistryError("正文保留回执身份不一致")
        self.value = {
            "schemaVersion": "ContentLeaseV1",
            "binding": self.binding,
            "status": "ACTIVE",
            "lease": {
                key: projection.get(key)
                for key in ("leaseId", "snapshotDigest", "idleExpiresAt", "hardExpiresAt")
            },
        }
        self._write()

    def _write(self):
        if self.path:
            self.api.write_json(self.path, self.value)

    def restore(self, client, api_base):
        if self.value.get("status") != "ACTIVE":
            return None
        response = self.api.api_request(
            client,
            "GET",
            f"{api_base}/api/v2/case-export-preparations/{self.binding['caseId']}",
            retry_on_429=False,
        )
        if response.status_code == 410:
            self.value["status"] = "EXPIRED"
            self._write()
            return None
        projection = self.api.response_json(response, "恢复原整卷正文保留任务")
        if projection.get("leaseId") != self.value["lease"].get("leaseId"):
            raise self.api.RegistryError("服务器正文保留身份已变化，未创建替代任务")
        self.save(projection)
        return projection

    def release(self, client, api_base, headers):
        if self.value.get("status") != "ACTIVE":
            return
        result = self.api.response_json(
            self.api.api_request(
                client,
                "DELETE",
                f"{api_base}/api/v2/case-export-leases/{self.value['lease']['leaseId']}",
                headers=headers,
                retry_on_429=False,
            ),
            "释放已核验正文保留任务",
        )
        if result.get("released") is not True:
            raise self.api.RegistryError("正文已核验，但原保留任务释放尚未确认")
        self.value["status"] = "RELEASED"
        self._write()
