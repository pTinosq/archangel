import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket

Message = dict[str, Any]


class Offline(Exception):
    """The device is not connected; nothing was sent."""


class Disconnected(Exception):
    """The device dropped before answering; the outcome is unknown."""


@dataclass(eq=False)
class Device:
    hello: Message
    socket: WebSocket
    pending: dict[str, asyncio.Future[Message]] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.hello["device"]


class Hub:
    """Connected devices and the commands awaiting their answers."""

    def __init__(self, timeout: float):
        self.timeout = timeout
        self.devices: dict[str, Device] = {}

    async def attach(self, hello: Message, socket: WebSocket) -> Device:
        device = Device(hello, socket)
        previous = self.devices.get(device.name)
        self.devices[device.name] = device
        if previous:
            self.detach(previous)
            await previous.socket.close()
        return device

    def detach(self, device: Device) -> None:
        if self.devices.get(device.name) is device:
            del self.devices[device.name]
        for future in device.pending.values():
            if not future.done():
                future.set_exception(Disconnected())
        device.pending.clear()

    def resolve(self, device: Device, result: Message) -> None:
        future = device.pending.get(result.get("id"))
        if future and not future.done():
            future.set_result(result)

    async def send(self, name: str, command: Message) -> Message:
        device = self.devices.get(name)
        if not device:
            raise Offline()

        command = {**command, "id": command.get("id") or uuid.uuid4().hex}
        future = asyncio.get_running_loop().create_future()
        device.pending[command["id"]] = future
        try:
            await device.socket.send_json(command)
            return await asyncio.wait_for(future, self.timeout)
        finally:
            device.pending.pop(command["id"], None)
