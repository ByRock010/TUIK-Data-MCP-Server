import json
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest

from tuik_mcp import doctor


@pytest.fixture(autouse=True)
def clean_settings(monkeypatch):
    import os

    for key in os.environ:
        if key.startswith("TUIK_"):
            monkeypatch.delenv(key)


def check(report, name):
    return next(c for c in report["checks"] if c["name"] == name)


def test_offline_doctor_does_not_probe_or_load_optional_runtimes(monkeypatch, tmp_path):
    import sys

    monkeypatch.setattr(doctor, "probe", lambda _: pytest.fail("offline network request"))
    before = set(sys.modules)
    target = tmp_path / "not-created" / "reports"
    report = doctor.diagnose(storage_dir=target)
    assert report["ok"]
    assert not target.parent.exists()
    assert check(report, "storage_permissions")["status"] == "ok"
    assert check(report, "network")["status"] == "skipped"
    assert check(report, "search_backend")["status"] == "ok"
    assert not {"matplotlib", "fastembed", "opensearchpy"} & (set(sys.modules) - before)


def test_invalid_config_never_echoes_sensitive_inputs(monkeypatch):
    secret = "private-api-token-MUST-NOT-PRINT"
    monkeypatch.setenv("TUIK_OPENSEARCH_URL", "https://user:" + secret + "@example.test/")
    monkeypatch.setenv("TUIK_TIMEOUT_SECONDS", secret)
    report = doctor.diagnose()
    text = json.dumps(report)
    assert not report["ok"]
    assert "TUIK_TIMEOUT_SECONDS" in check(report, "settings")["message"]
    assert secret not in text and "example.test" not in text


def test_model_cache_is_not_declared_ready_from_nonempty_directory(monkeypatch, tmp_path):
    monkeypatch.setenv("TUIK_OPENSEARCH_URL", "http://127.0.0.1:9200")
    monkeypatch.setenv("TUIK_EMBEDDING_CACHE_DIR", str(tmp_path))
    (tmp_path / "incomplete-download").write_text("not a model")
    report = doctor.diagnose()
    assert report["ok"]  # Optional index/model is not required for core startup.
    assert check(report, "embedding_cache")["status"] == "warning"
    assert "unverified" in check(report, "embedding_cache")["message"]
    assert str(tmp_path) not in json.dumps(report)


def test_unresolvable_model_cache_does_not_echo_path_or_crash(monkeypatch):
    monkeypatch.setenv("TUIK_OPENSEARCH_URL", "http://localhost:9200")
    monkeypatch.setenv("TUIK_EMBEDDING_CACHE_DIR", "~private-path-must-not-print/cache")
    original = Path.expanduser

    def expand(path):
        if str(path).startswith("~private"):
            raise RuntimeError("secret")
        return original(path)

    monkeypatch.setattr(Path, "expanduser", expand)
    report = doctor.diagnose()
    assert report["ok"]
    assert check(report, "embedding_cache")["status"] == "warning"
    assert "secret" not in json.dumps(report)
    assert "private-path" not in json.dumps(report)


def test_missing_runtime_sdk_mismatch_and_unwritable_storage(monkeypatch, tmp_path):
    real = doctor.version
    monkeypatch.setattr(
        doctor, "version", lambda n: {"mcp": "2.0.0", "httpx": None}.get(n, real(n))
    )
    monkeypatch.setattr(doctor.os, "access", lambda *a: False)
    report = doctor.diagnose(storage_dir=tmp_path)
    assert not report["ok"]
    for name in ("dependency:mcp", "dependency:httpx", "storage_permissions"):
        assert check(report, name)["status"] == "error"


def test_missing_storage_parent_and_file_are_reported_without_writes(tmp_path):
    file = tmp_path / "file"
    file.write_text("preserved")
    assert doctor.storage_check(file)[0] == "error"
    assert file.read_text() == "preserved"
    assert doctor.storage_check(tmp_path / "new" / "nested")[0] == "ok"
    assert not (tmp_path / "new").exists()


def test_online_probes_are_explicit_bounded_and_redacted(monkeypatch):
    calls = []

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    class Opener:
        def open(self, request, timeout):
            calls.append((request.get_method(), request.full_url, timeout))
            return Response()

    monkeypatch.setattr(doctor, "build_opener", lambda *_: Opener())
    monkeypatch.setenv("TUIK_OPENSEARCH_URL", "http://user:secret@localhost:9200/")
    report = doctor.diagnose(online=True)
    assert len(calls) == 2  # Credential-bearing index is not contacted.
    assert all(method == "HEAD" and timeout == 3 for method, _, timeout in calls)
    assert all("secret" not in url for _, url, _ in calls)
    assert "secret" not in json.dumps(report)
    assert check(report, "network:opensearch")["status"] == "warning"


@pytest.mark.parametrize(
    "failure", [HTTPError("https://secret", 401, "secret", {}, None), URLError("secret")]
)
def test_probe_failure_does_not_expose_exception_or_remote_body(monkeypatch, failure):
    class Opener:
        def open(self, *args, **kwargs):
            raise failure

    monkeypatch.setattr(doctor, "build_opener", lambda *_: Opener())
    status, message = doctor.probe("https://example.test/")
    assert status == "warning"
    assert "secret" not in message


def test_redirects_are_not_followed():
    assert doctor.NoRedirect().redirect_request(None, None, 302, "", {}, "https://secret") is None


def test_json_cli_exit_codes_and_paths_stay_private(monkeypatch, capsys, tmp_path):
    assert doctor.main(["--json", "--storage-dir", str(tmp_path)]) == 0
    output = capsys.readouterr().out
    assert json.loads(output)["ok"]
    assert str(tmp_path) not in output
    monkeypatch.setenv("TUIK_TIMEOUT_SECONDS", "private-value")
    assert doctor.main(["--json"]) == 1
    assert "private-value" not in capsys.readouterr().out


def test_default_storage_is_cwd_not_a_temporary_write_probe(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    doctor.diagnose()
    assert list(Path.cwd().iterdir()) == []
