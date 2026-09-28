import json

import pytest
from PySide6.QtCore import QCoreApplication, QIODevice, QObject, QTimer, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from codex_stt_tray import transcription
from codex_stt_tray.constants import ENDPOINT, MAX_RESPONSE_BYTES
from codex_stt_tray.models import AudioClip, ErrorCode
from codex_stt_tray.transcription import TranscriptionJob


class Reply(QNetworkReply):
    def __init__(self, request, payload, status, error, delay, parent):
        super().__init__(parent)
        self.setRequest(request)
        self.setUrl(request.url())
        self.setOpenMode(QIODevice.OpenModeFlag.ReadOnly | QIODevice.OpenModeFlag.Unbuffered)
        if status is not None:
            self.setAttribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute, status)
        if error:
            self.setError(error, "untrusted diagnostic must not be displayed")
        self.payload = payload
        self.offset = 0
        self.aborted = False
        if delay is not None:
            QTimer.singleShot(delay, self.deliver)

    def readData(self, size):
        data = self.payload[self.offset : self.offset + size]
        self.offset += len(data)
        return data

    def bytesAvailable(self):
        return len(self.payload) - self.offset + super().bytesAvailable()

    def deliver(self):
        if self.aborted or self.isFinished():
            return
        self.readyRead.emit()
        if not self.aborted:
            self.setFinished(True)
            self.finished.emit()

    def abort(self):
        self.aborted = True
        self.setFinished(True)
        self.finished.emit()


class Manager(QNetworkAccessManager):
    """Intercept every request before any network I/O, using real Qt multipart building."""

    def __init__(self, responses):
        super().__init__(QCoreApplication.instance())
        self.responses = list(responses)
        self.requests = []
        self.uploads = []
        self.replies = []

    def createRequest(self, operation, request, outgoing=None):
        self.requests.append(QNetworkRequest(request))
        self.uploads.append(bytes(outgoing.readAll()) if outgoing else b"")
        response = {
            "payload": b'{"text":"synthetic result"}',
            "status": 200,
            "error": None,
            "delay": 0,
        } | self.responses.pop(0)
        reply = Reply(request, parent=self, **response)
        self.replies.append(reply)
        return reply


@pytest.fixture
def credentials(isolated_home):
    path = isolated_home / "auth.json"
    path.write_text(json.dumps({"tokens": {"access_token": "dummy", "account_id": "dummy"}}))
    path.chmod(0o600)
    return isolated_home


def make_job(credentials, responses, deadline=500):
    manager = Manager(responses)
    job = TranscriptionJob(
        manager, AudioClip(b"synthetic wav bytes", 1), credentials, deadline_ms=deadline
    )
    outcomes = []
    job.succeeded.connect(lambda result: outcomes.append(result))
    job.failed.connect(outcomes.append)
    job.cancelled.connect(lambda: outcomes.append("cancelled"))
    return manager, job, outcomes


def test_success_in_memory_and_contract(qtbot, credentials):
    manager, job, outcomes = make_job(credentials, [{}])
    job.start()
    qtbot.waitUntil(lambda: job.done)
    assert len(outcomes) == 1 and outcomes[0].text == "synthetic result"
    assert job.clip is None and not job.body
    request = manager.requests[0]
    assert request.url().toString() == ENDPOINT
    assert request.attribute(QNetworkRequest.Attribute.RedirectPolicyAttribute) == (
        QNetworkRequest.RedirectPolicy.ManualRedirectPolicy
    )
    assert request.attribute(QNetworkRequest.Attribute.CookieLoadControlAttribute) == (
        QNetworkRequest.LoadControl.Manual
    )
    assert b'name="file"; filename="recording.wav"' in manager.uploads[0]
    assert b"synthetic wav bytes" in manager.uploads[0]
    job.cancel()
    assert len(outcomes) == 1


@pytest.mark.parametrize("payload", [b"garbage", b"[]", b"{}", b'{"text":42}', b"\xff", b""])
def test_invalid_response(qtbot, credentials, payload):
    manager, job, outcomes = make_job(credentials, [{"payload": payload}])
    job.start()
    qtbot.waitUntil(lambda: job.done)
    assert outcomes[0].code == ErrorCode.RESPONSE_INVALID
    assert outcomes[0].message == "Unexpected server response."


@pytest.mark.parametrize("payload", [b'{"text":null}', b'{"text":""}', b'{"text":"  "}'])
def test_empty_transcript_has_a_distinct_error(qtbot, credentials, payload):
    manager, job, outcomes = make_job(credentials, [{"payload": payload}])
    job.start()
    qtbot.waitUntil(lambda: job.done)
    assert outcomes[0].code == ErrorCode.RESPONSE_EMPTY
    assert outcomes[0].message == "No transcript returned."
    assert not job.body and job.clip is None


@pytest.mark.parametrize("status,retryable", [(302, False), (403, False), (429, True), (503, True)])
def test_http_failures_are_structured(qtbot, credentials, status, retryable):
    manager, job, outcomes = make_job(credentials, [{"status": status, "payload": b"private body"}])
    job.start()
    qtbot.waitUntil(lambda: job.done)
    assert outcomes[0].http_status == status
    assert outcomes[0].retryable == retryable
    assert "private body" not in str(outcomes[0])
    assert len(manager.requests) == 1


def test_oversized_response_aborts(qtbot, credentials):
    manager, job, outcomes = make_job(credentials, [{"payload": b"x" * (MAX_RESPONSE_BYTES + 1)}])
    job.start()
    qtbot.waitUntil(lambda: job.done)
    assert outcomes[0].code == ErrorCode.RESPONSE_LIMIT
    assert not job.body


def test_cancellation_is_single_terminal_outcome(qtbot, credentials):
    manager, job, outcomes = make_job(credentials, [{"delay": None}])
    job.start()
    reply = manager.replies[0]
    job.cancel()
    assert reply.aborted
    reply.deliver()
    job.cancel()
    assert outcomes == ["cancelled"]


def test_total_deadline_aborts(qtbot, credentials):
    manager, job, outcomes = make_job(credentials, [{"delay": None}], deadline=10)
    job.start()
    qtbot.waitUntil(lambda: job.done)
    assert outcomes[0].code == ErrorCode.TIMEOUT


class Refresh(QObject):
    succeeded = Signal()
    failed = Signal(object)
    settled = Signal()
    starts = 0
    delay = 0

    def __init__(self, home, parent):
        super().__init__(parent)
        self._settled = False
        self.done = False

    def start(self):
        type(self).starts += 1
        QTimer.singleShot(self.delay, self.complete)

    def complete(self):
        if not self.done:
            self.done = self._settled = True
            self.succeeded.emit()
            self.settled.emit()

    def cancel(self):
        self.done = self._settled = True
        self.settled.emit()


def test_401_refreshes_once_then_fails(qtbot, credentials, monkeypatch):
    monkeypatch.setattr(transcription, "AuthRefresh", Refresh)
    Refresh.starts = 0
    manager, job, outcomes = make_job(credentials, [{"status": 401}, {"status": 401}])
    job.start()
    qtbot.waitUntil(lambda: job.done)
    assert Refresh.starts == 1 and len(manager.requests) == 2
    assert outcomes[0].http_status == 401


def test_refresh_and_retry_share_deadline(qtbot, credentials, monkeypatch):
    monkeypatch.setattr(transcription, "AuthRefresh", Refresh)
    monkeypatch.setattr(Refresh, "delay", 20)
    manager, job, outcomes = make_job(credentials, [{"status": 401}, {"delay": None}], deadline=60)
    job.start()
    qtbot.waitUntil(lambda: job.done)
    assert len(manager.requests) == 2
    assert outcomes[0].code == ErrorCode.TIMEOUT


def test_transport_error_is_safe(qtbot, credentials):
    manager, job, outcomes = make_job(
        credentials, [{"status": None, "error": QNetworkReply.NetworkError.SslHandshakeFailedError}]
    )
    job.start()
    qtbot.waitUntil(lambda: job.done)
    assert outcomes[0].code == ErrorCode.NETWORK
    assert "untrusted" not in str(outcomes[0])


def test_cancel_before_start_never_sends(qtbot, credentials):
    manager, job, outcomes = make_job(credentials, [])
    job.cancel()
    job.start()
    assert not manager.requests and outcomes == ["cancelled"]


def test_missing_auth_never_sends(qtbot, isolated_home):
    manager, job, outcomes = make_job(isolated_home, [])
    job.start()
    assert not manager.requests
    assert outcomes[0].code == ErrorCode.AUTH_MISSING


@pytest.mark.parametrize("payload", [b"[" * 2000 + b"]" * 2000, b'{"text":"\\ud800"}'])
def test_pathological_json_is_a_structured_failure(qtbot, credentials, payload):
    manager, job, outcomes = make_job(credentials, [{"payload": payload}])
    job.start()
    qtbot.waitUntil(lambda: job.done)
    assert outcomes[0].code == ErrorCode.RESPONSE_INVALID


def test_refresh_success_retries_upload(qtbot, credentials, monkeypatch):
    monkeypatch.setattr(transcription, "AuthRefresh", Refresh)
    manager, job, outcomes = make_job(credentials, [{"status": 401}, {}])
    job.start()
    qtbot.waitUntil(lambda: job.done)
    assert len(manager.requests) == 2
    assert len(outcomes) == 1 and outcomes[0].text == "synthetic result"


def test_cancel_during_refresh_cannot_restart_request(qtbot, credentials, monkeypatch):
    monkeypatch.setattr(transcription, "AuthRefresh", Refresh)
    monkeypatch.setattr(Refresh, "delay", 100)
    manager, job, outcomes = make_job(credentials, [{"status": 401}])
    job.start()
    qtbot.waitUntil(lambda: job.refresh is not None)
    job.cancel()
    qtbot.wait(120)
    assert len(manager.requests) == 1 and outcomes == ["cancelled"]
