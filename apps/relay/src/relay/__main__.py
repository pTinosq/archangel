import uvicorn

from relay import config

uvicorn.run("relay.app:create_app", factory=True, host="0.0.0.0", port=config.port())
