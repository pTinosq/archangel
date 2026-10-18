"""Private links to screenshots, for channels that can't carry pictures (texts from a UK number).

Each link is signed and expires, so it opens without signing in but can't be guessed or kept. The key is derived
from the relay token, so there's no extra secret to keep.
"""

import hashlib
import hmac
import time

from fastapi import HTTPException, Response

from agent.messages import Image
from agent.store import Store
from agent.web import Web

SECONDS = 24 * 3600  # long enough to answer an approval the next morning


class Links:
    def __init__(self, store: Store, web: Web, secret: str, public_url: str):
        self._store = store
        self._key = hmac.new(secret.encode(), b"screenshots", hashlib.sha256).digest()
        self._url = public_url

        @web.app.get("/screenshots/{name}")
        async def screenshot(name: str, expires: int = 0, sig: str = "") -> Response:
            if not (hmac.compare_digest(sig, self._sign(name, expires)) and expires > time.time()):
                raise HTTPException(404, "This link has expired")
            if not (found := store.image(name)):
                raise HTTPException(404, "This screenshot has expired")
            return Response(found.data, media_type=found.media_type, headers={"Cache-Control": "private"})

    def to(self, image: Image) -> str:
        name = self._store.save_image(image)
        expires = int(time.time()) + SECONDS
        return f"{self._url}/screenshots/{name}?expires={expires}&sig={self._sign(name, expires)}"

    def _sign(self, name: str, expires: int) -> str:
        return hmac.new(self._key, f"{name}.{expires}".encode(), hashlib.sha256).hexdigest()
