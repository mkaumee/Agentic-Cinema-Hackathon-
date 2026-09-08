# google-cloud-storage ships no type information, so every call into the client
# is Unknown and a blob's content_type is Any. Scoped here rather than loosened
# repo-wide, the same way repository.py does it for Firestore.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false, reportMissingTypeStubs=false
# pyright: reportAny=false
"""Where a producer's file waits between uploading it and it being sent.

A seller asks for a reference photo. The producer uploads it now; the tick
sends it a minute or an hour later, from a different process on a different
machine. Something has to hold the bytes in between, and per Hard Rule 3 it
cannot be memory.

Not Firestore. A document caps at 1 MiB and a photograph off a phone is
routinely several times that — it would work in testing with a small file and
fail in front of a judge with a real one. So: a bucket, and the two services
reach it from opposite ends.

``cinema-api`` writes (``objectCreator``) and ``cinema-agent`` reads
(``objectViewer``). Neither holds both, which is the same shape as the token
secret: the service that stores a thing has no business using it, and the
service that uses it cannot create one.

The key is ``{project_id}/{negotiation_id}/{filename}``, so an object cannot be
read across productions by guessing, and a second upload to the same
negotiation replaces the first — which is what a producer changing their mind
means.
"""

import asyncio
from dataclasses import dataclass
from typing import Protocol, final

from google.cloud import storage

MAX_BYTES = 10 * 1024 * 1024
"""Refused above this.

Gmail's own attachment limit is 25 MB and base64 inflates by a third, so this
sits well inside it. A reference photo is a couple of megabytes; anything
much larger is a misunderstanding rather than a requirement.
"""


class TooLargeError(ValueError):
    """The file will not fit in an email. The message is written for a person."""


@dataclass(frozen=True, slots=True)
class StoredFile:
    filename: str
    content_type: str
    data: bytes


class AttachmentReader(Protocol):
    """What the tick may do with the bucket, and it is one verb.

    The IAM split above is the real boundary — objectViewer and nothing else —
    but IAM is a fact about a deployment and these are a fact about the code,
    and both should say the same thing. A tick that grew an upload would fail
    at runtime on a live project and pass every test; declared this way it does
    not compile.
    """

    async def get(self, key: str) -> StoredFile | None: ...


class AttachmentWriter(Protocol):
    """And what the api service may do. The other verb, and only that one."""

    async def put(
        self, project_id: str, negotiation_id: str, file: StoredFile
    ) -> str: ...


@final
class AttachmentStore:
    """Reads and writes one bucket. Async only because everything here is."""

    _bucket: str
    _client: storage.Client | None

    def __init__(self, bucket: str, client: storage.Client | None = None) -> None:
        self._bucket = bucket
        self._client = client

    def _blob(self, key: str) -> storage.Blob:
        client = self._client or storage.Client()
        return client.bucket(self._bucket).blob(key)

    @staticmethod
    def key_for(project_id: str, negotiation_id: str, filename: str) -> str:
        return f"{project_id}/{negotiation_id}/{filename}"

    async def put(self, project_id: str, negotiation_id: str, file: StoredFile) -> str:
        """Store one file and return its key. Refuses anything too big to send."""
        if len(file.data) > MAX_BYTES:
            raise TooLargeError(
                f"{file.filename} is {len(file.data) // (1024 * 1024)} MB, over "
                f"the {MAX_BYTES // (1024 * 1024)} MB limit an email can carry."
            )
        key = self.key_for(project_id, negotiation_id, file.filename)
        blob = self._blob(key)
        # The storage client is synchronous and does network I/O, so it goes to
        # a thread rather than blocking the event loop mid-request.
        await asyncio.to_thread(
            blob.upload_from_string, file.data, content_type=file.content_type
        )
        return key

    async def get(self, key: str) -> StoredFile | None:
        """Fetch one file, or None if it is gone.

        None rather than raising: a producer can delete a production between
        uploading and the tick that sends, and a missing attachment must not
        take a negotiation down with it.
        """
        blob = self._blob(key)
        exists = await asyncio.to_thread(blob.exists)
        if not exists:
            return None
        data = await asyncio.to_thread(blob.download_as_bytes)
        return StoredFile(
            filename=key.rsplit("/", 1)[-1],
            content_type=blob.content_type or "application/octet-stream",
            data=data,
        )
