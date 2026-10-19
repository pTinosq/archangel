"""Private links that open without signing in: screenshots, for channels that can't carry pictures (texts from a UK
number), and the page where the owner shares their location.

Each link is signed and expires, so it can't be guessed or kept. The key is derived from the relay token, so there's
no extra secret to keep.
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
        self.url = public_url

        @web.app.get("/screenshots/{name}")
        async def screenshot(name: str, expires: int = 0, sig: str = "") -> Response:
            self.check(f"/screenshots/{name}", expires, sig)
            if not (found := store.image(name)):
                raise HTTPException(404, "This screenshot has expired")
            return Response(found.data, media_type=found.media_type, headers={"Cache-Control": "private"})

    def to(self, image: Image) -> str:
        return self.signed(f"/screenshots/{self._store.save_image(image)}")

    def signed(self, path: str) -> str:
        """A link to `path` (URL-encoded) that `check` lets through until it expires."""
        expires = int(time.time()) + SECONDS
        return f"{self.url}{path}?expires={expires}&sig={self._sign(path, expires)}"

    def check(self, path: str, expires: int, sig: str) -> None:
        """Refuse unless `sig` is this link's, unexpired."""
        if not (hmac.compare_digest(sig, self._sign(path, expires)) and expires > time.time()):
            raise HTTPException(404, "This link has expired")

    def _sign(self, path: str, expires: int) -> str:
        return hmac.new(self._key, f"{path}.{expires}".encode(), hashlib.sha256).hexdigest()
