from __future__ import annotations

import hashlib
import io
import json
import stat
import zipfile

import httpx
import pytest

from scripts import mineru_v4 as api
from scripts.test_ocr_runtime import make_pdf


def archive_bytes(names=("one",)):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        if len(names) > 1:
            bundle.writestr(
                "manifest.json",
                json.dumps(
                    {
                        "task_id": "job_original",
                        "status": "completed",
                        "files": [{"name": name + ".pdf", "status": "completed"} for name in names],
                    }
                ),
            )
        for index, _name in enumerate(names, 1):
            prefix = f"{index:04}/" if len(names) > 1 else ""
            middle = {
                "schema": "docvortex.middle",
                "schema_version": "2.0",
                "is_full_document": True,
                "pages": [{"page_idx": 0, "blocks": []}],
            }
            bundle.writestr(prefix + "middle_json.json", json.dumps(middle))
            bundle.writestr(
                prefix + "structured_content.json",
                json.dumps({"is_full_document": True, "pages": middle["pages"]}),
            )
            bundle.writestr(prefix + "markdown.md", "中文测试 ![](images/test.jpg)")
            bundle.writestr(prefix + "images/test.jpg", b"image-fixture")
    return buffer.getvalue()


class Service:
    def __init__(self, tmp_path, monkeypatch, names=("one",)):
        self.inputs = []
        for name in names:
            path = tmp_path / (name + ".pdf")
            make_pdf(path, 1)
            self.inputs.append(path)
        self.profile = {"apiBase": api.API_BASE, "transport": api.TASK_SCHEMA}
        self.attempt = tmp_path / "attempt.json"
        api._write(
            self.attempt,
            {
                "engineProfile": self.profile,
                "inputs": [
                    {
                        "inputName": path.name,
                        "sha256": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
                    }
                    for path in self.inputs
                ],
            },
        )
        self.state = "completed"
        self.submission_error = False
        self.invalid_id = False
        self.calls = []
        self.zip = archive_bytes(names)
        client = httpx.Client
        monkeypatch.setattr(
            api.httpx,
            "Client",
            lambda **kwargs: client(transport=httpx.MockTransport(self.request), **kwargs),
        )

    def request(self, request):
        self.calls.append((request.method, request.url.path))
        if request.method == "POST":
            assert request.url.params["tier"] == "standard"
            if all(path.suffix == ".pdf" for path in self.inputs):
                assert request.url.params["page_range"] == "all"
            else:
                assert "page_range" not in request.url.params
            assert request.url.params["output_formats"] == "zip"
            assert "lang" not in request.url.params and "backend" not in request.url.params
            if self.submission_error:
                raise httpx.ReadTimeout("submission interrupted", request=request)
        if request.url.path.endswith("/result"):
            return httpx.Response(200, content=self.zip)
        return httpx.Response(
            202 if request.method == "POST" else 200,
            json={
                "task_id": "job_other"
                if self.invalid_id and request.method == "GET"
                else "job_original",
                "status": "queued" if request.method == "POST" else self.state,
                "files": [{"name": path.name, "status": self.state} for path in self.inputs],
            },
        )

    def run(self, directory, poll=lambda: None):
        directory.mkdir()
        return api.run_task(self.inputs, 5, poll, directory, self.attempt, self.profile)


def test_native_zip_binds_multiple_files_and_keeps_images_and_source(tmp_path, monkeypatch):
    service = Service(tmp_path, monkeypatch, ("one", "two"))
    out = tmp_path / "result"
    assert service.run(out) == 0
    assert service.calls.count(("POST", "/v1/tasks")) == 1
    for source in service.inputs:
        target = out / source.stem
        assert (target / "images/test.jpg").read_bytes() == b"image-fixture"
        assert (target / (source.stem + "_origin.pdf")).read_bytes() == source.read_bytes()
        middle = json.loads((target / (source.stem + "_middle.json")).read_text())
        assert middle["schema"] == "docvortex.middle" and "pdf_info" not in middle


def test_known_task_survives_interruption_and_resumes_without_post_or_cancel(tmp_path, monkeypatch):
    service = Service(tmp_path, monkeypatch)

    def interrupt():
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        service.run(tmp_path / "first", poll=interrupt)
    assert json.loads(service.attempt.read_text())["taskId"] == "job_original"
    assert service.run(tmp_path / "resumed") == 0
    assert service.calls.count(("POST", "/v1/tasks")) == 1
    assert all(method != "DELETE" for method, _ in service.calls)


def test_unknown_submission_is_recorded_and_never_blindly_reissued(tmp_path, monkeypatch):
    service = Service(tmp_path, monkeypatch)
    service.submission_error = True
    with pytest.raises(api.MinerUV4Error, match="中断"):
        service.run(tmp_path / "first")
    with pytest.raises(api.MinerUV4Error, match="结果未知"):
        service.run(tmp_path / "second")
    assert service.calls == [("POST", "/v1/tasks")]
    assert json.loads(service.attempt.read_text())["submissionState"] == "SUBMITTING"


def test_failed_task_is_retained_and_not_replaced(tmp_path, monkeypatch):
    service = Service(tmp_path, monkeypatch)
    service.state = "failed"
    for number in (1, 2):
        with pytest.raises(api.MinerUV4Error, match="已失败"):
            service.run(tmp_path / str(number))
    assert service.calls.count(("POST", "/v1/tasks")) == 1
    assert json.loads(service.attempt.read_text())["taskStatus"] == "failed"


def test_response_task_identity_cannot_change(tmp_path, monkeypatch):
    service = Service(tmp_path, monkeypatch)
    service.invalid_id = True
    with pytest.raises(api.MinerUV4Error, match="身份"):
        service.run(tmp_path / "out")
    assert not any(path.endswith("/result") for _, path in service.calls)


@pytest.mark.parametrize("suffix", [".png", ".jpg"])
def test_native_images_do_not_receive_pdf_page_range(tmp_path, monkeypatch, suffix):
    service = Service(tmp_path, monkeypatch)
    old = service.inputs[0]
    image = old.with_suffix(suffix)
    old.rename(image)
    service.inputs = [image]
    attempt = json.loads(service.attempt.read_text())
    attempt["inputs"][0]["inputName"] = image.name
    api._write(service.attempt, attempt)
    assert service.run(tmp_path / "out") == 0
    assert (tmp_path / "out" / image.stem / (image.stem + "_origin" + suffix)).is_file()


def test_batch_zip_manifest_cannot_bind_another_task(tmp_path, monkeypatch):
    service = Service(tmp_path, monkeypatch, ("one", "two"))
    buffer = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(service.zip)) as source, zipfile.ZipFile(buffer, "w") as output:
        for name in source.namelist():
            data = source.read(name)
            if name == "manifest.json":
                manifest = json.loads(data)
                manifest["task_id"] = "job_another_task"
                data = json.dumps(manifest)
            output.writestr(name, data)
    service.zip = buffer.getvalue()
    with pytest.raises(api.MinerUV4Error, match="身份"):
        service.run(tmp_path / "out")
    assert not (tmp_path / "out/one").exists()


@pytest.mark.parametrize(
    "name",
    [
        "../outside.txt",
        "/outside.txt",
        "C:/outside.txt",
        "a\\outside.txt",
        "a/../outside.txt",
        "a:stream",
    ],
)
def test_zip_unsafe_paths_are_rejected(tmp_path, name):
    if "\\" in name:
        # ZipInfo normalizes separators when a fixture is written on Windows.
        with pytest.raises(api.MinerUV4Error, match="不安全"):
            api._member(name)
        return
    archive = tmp_path / "result.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(name, "unsafe")
    with pytest.raises(api.MinerUV4Error, match="不安全"):
        api._unpack(archive, tmp_path / "out")
    assert not (tmp_path / "outside.txt").exists()


def test_zip_links_and_case_collisions_are_rejected(tmp_path):
    for kind in ("link", "collision"):
        archive = tmp_path / (kind + ".zip")
        with zipfile.ZipFile(archive, "w") as bundle:
            if kind == "link":
                item = zipfile.ZipInfo("link")
                item.create_system = 3
                item.external_attr = (stat.S_IFLNK | 0o777) << 16
                bundle.writestr(item, "../outside")
            else:
                bundle.writestr("A.txt", "first")
                bundle.writestr("a.txt", "second")
        with pytest.raises(api.MinerUV4Error, match="重复路径或符号链接"):
            api._unpack(archive, tmp_path / kind)
