"""Cancellable in-memory transcription on Qt's event loop."""

import json
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QObject, QTimer, QUrl, Signal
from PySide6.QtNetwork import QHttpMultiPart, QHttpPart, QNetworkAccessManager, QNetworkRequest

from .auth import AuthRefresh, codex_home, read_credentials
from .constants import (
    ACCOUNT_HEADER,
    AUTHORIZATION_HEADER,
    ENDPOINT,
    FILE_FIELD,
    MAX_AUDIO_BYTES,
    MAX_RESPONSE_BYTES,
    ORIGINATOR,
    ORIGINATOR_HEADER,
    REQUEST_DEADLINE_MS,
    TEXT_FIELD,
)
from .models import AppError, AudioClip, ErrorCode, Transcript


def parse_response(data: bytes) -> Transcript:
    try:
        payload = json.loads(data)
        text = payload[TEXT_FIELD]
        if not isinstance(text, str) or not text.strip():
            raise ValueError
        text.encode("utf-8")
        return Transcript(text.strip())
    except (ValueError, KeyError, TypeError, RecursionError):
        raise AppError(
            ErrorCode.RESPONSE_INVALID,
            "The server returned an invalid or empty transcript.",
            "transcription",
        ) from None


class TranscriptionJob(QObject):
    succeeded = Signal(object)
    failed = Signal(object)
    cancelled = Signal()
    progress = Signal(int, int)
    settled = Signal()

    def __init__(
        self,
        manager: QNetworkAccessManager,
        clip: AudioClip,
        home: Path,
        parent: QObject | None = None,
        *,
        deadline_ms: int = REQUEST_DEADLINE_MS,
    ):
        super().__init__(parent)
        self.manager, self.clip, self.home = manager, clip, home
        self.deadline_ms = deadline_ms
        self.done = False
        self.started = False
        self.reply = None
        self.refresh = None
        self.retried = False
        self.body = bytearray()
        self._settled = False
        self.timer = QTimer(self, singleShot=True)
        self.timer.timeout.connect(self._timeout)

    def start(self):
        if self.started or self.done:
            return
        self.started = True
        if not self.clip.data or len(self.clip.data) > MAX_AUDIO_BYTES:
            self._finish(
                error=AppError(
                    ErrorCode.AUDIO_LIMIT,
                    "Recording is empty or exceeds the upload limit.",
                    "audio",
                )
            )
            return
        if self.clip.mime_type != "audio/wav" or self.clip.filename != "recording.wav":
            self._finish(
                error=AppError(
                    ErrorCode.AUDIO_FORMAT,
                    "Only application-generated WAV recordings are supported.",
                    "audio",
                )
            )
            return
        self.timer.start(self.deadline_ms)
        self._request()

    def _request(self):
        if self.done:
            return
        try:
            credentials = read_credentials(self.home)
        except AppError as error:
            self._finish(error=error)
            return
        request = QNetworkRequest(QUrl(ENDPOINT))
        request.setAttribute(
            QNetworkRequest.Attribute.RedirectPolicyAttribute,
            QNetworkRequest.RedirectPolicy.ManualRedirectPolicy,
        )
        request.setAttribute(
            QNetworkRequest.Attribute.CookieLoadControlAttribute,
            QNetworkRequest.LoadControl.Manual,
        )
        request.setAttribute(
            QNetworkRequest.Attribute.CookieSaveControlAttribute,
            QNetworkRequest.LoadControl.Manual,
        )
        request.setAttribute(QNetworkRequest.Attribute.CacheSaveControlAttribute, False)
        request.setRawHeader(AUTHORIZATION_HEADER, f"Bearer {credentials.access_token}".encode())
        request.setRawHeader(ACCOUNT_HEADER, credentials.account_id.encode())
        request.setRawHeader(ORIGINATOR_HEADER, ORIGINATOR)
        request.setRawHeader(b"User-Agent", b"Codex Desktop/unknown (Linux)")
        request.setRawHeader(b"Accept", b"application/json")

        multipart = QHttpMultiPart(QHttpMultiPart.ContentType.FormDataType)
        part = QHttpPart()
        part.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "audio/wav")
        part.setHeader(
            QNetworkRequest.KnownHeaders.ContentDispositionHeader,
            f'form-data; name="{FILE_FIELD}"; filename="recording.wav"',
        )
        buffer = QBuffer(multipart)
        buffer.setData(QByteArray(self.clip.data))
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        part.setBodyDevice(buffer)
        multipart.append(part)
        self.body.clear()
        reply = self.manager.post(request, multipart)
        multipart.setParent(reply)
        # Keep Python wrappers alive as long as Qt consumes their devices.
        reply._upload = (multipart, buffer, part)
        self.reply = reply
        reply.setReadBufferSize(65536)
        reply.readyRead.connect(lambda: self._read(reply))
        reply.uploadProgress.connect(self.progress.emit)
        reply.finished.connect(lambda: self._received(reply))

    def _read(self, reply):
        if self.done or reply is not self.reply:
            return
        while reply.bytesAvailable():
            remaining = MAX_RESPONSE_BYTES + 1 - len(self.body)
            chunk = bytes(reply.read(min(65536, remaining)))
            if not chunk:
                break
            self.body.extend(chunk)
            if len(self.body) > MAX_RESPONSE_BYTES:
                self._finish(
                    error=AppError(
                        ErrorCode.RESPONSE_LIMIT,
                        "The server response exceeded its size limit.",
                        "transcription",
                    )
                )
                return

    def _received(self, reply):
        if self.done or reply is not self.reply:
            return
        self._read(reply)
        if self.done:
            return
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        network_error = reply.error()
        self.reply = None
        reply.deleteLater()
        if status == 401 and not self.retried:
            self.retried = True
            self.body.clear()
            self.refresh = AuthRefresh(self.home, self)
            self.refresh.succeeded.connect(self._request)
            self.refresh.failed.connect(lambda error: self._finish(error=error))
            self.refresh.settled.connect(self._maybe_settled)
            self.refresh.start()
            return
        if status is not None and not 200 <= status < 300:
            self._finish(
                error=AppError(
                    ErrorCode.HTTP,
                    "Codex rejected the session. Sign in again."
                    if status == 401
                    else f"Transcription failed (HTTP {status}).",
                    "transcription",
                    status == 429 or status >= 500,
                    status,
                )
            )
            return
        if network_error != reply.NetworkError.NoError or status is None:
            self._finish(
                error=AppError(
                    ErrorCode.NETWORK,
                    "Cannot reach the transcription server securely.",
                    "transcription",
                    True,
                )
            )
            return
        try:
            result = parse_response(bytes(self.body))
        except AppError as error:
            self._finish(error=error)
        else:
            self._finish(result=result)

    def _timeout(self):
        self._finish(
            error=AppError(
                ErrorCode.TIMEOUT,
                "Transcription exceeded its overall time limit.",
                "transcription",
                True,
            )
        )

    def cancel(self):
        self._finish()

    def _finish(self, *, result=None, error=None):
        if self.done:
            return
        self.done = True  # Set before abort(), which can emit finished synchronously.
        self.timer.stop()
        if self.reply is not None:
            reply, self.reply = self.reply, None
            reply.abort()
            reply.deleteLater()
        if self.refresh is not None:
            self.refresh.cancel()
        self.clip = None
        self.body.clear()
        if result is not None:
            self.succeeded.emit(result)
        elif error is not None:
            self.failed.emit(error)
        else:
            self.cancelled.emit()
        self._maybe_settled()

    def _maybe_settled(self):
        if self.done and not self._settled and (self.refresh is None or self.refresh._settled):
            self._settled = True
            self.settled.emit()


class Transcriber(QObject):
    idle = Signal()

    def __init__(self, parent=None, *, home_provider=codex_home):
        super().__init__(parent)
        self.home_provider = home_provider
        self.manager = QNetworkAccessManager(self)
        self.jobs = set()

    def create_job(self, clip: AudioClip) -> TranscriptionJob:
        job = TranscriptionJob(self.manager, clip, self.home_provider(), self)
        self.jobs.add(job)
        job.settled.connect(lambda: self._release(job))
        return job

    def _release(self, job):
        self.jobs.discard(job)
        job.deleteLater()
        if not self.jobs:
            self.idle.emit()

    def shutdown(self):
        for job in tuple(self.jobs):
            job.cancel()
        if not self.jobs:
            self.idle.emit()
