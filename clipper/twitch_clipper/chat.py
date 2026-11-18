from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass

log = logging.getLogger(__name__)

HOST = "irc.chat.twitch.tv"
PORT = 6697
# Twitch pings roughly every 5 minutes; silence much longer than that means a dead socket.
READ_TIMEOUT = 400
# Anonymous connections may join 20 channels per 10 seconds.
JOIN_BATCH = 15
JOIN_PAUSE = 11


@dataclass(frozen=True)
class ChatMessage:
    channel: str
    user: str
    text: str
    ts: float


def parse_privmsg(line: str, ts: float | None = None) -> ChatMessage | None:
    """Parse ':nick!nick@nick.tmi.twitch.tv PRIVMSG #channel :text'."""
    if line.startswith("@"):  # IRCv3 tags, if ever enabled
        _, _, line = line.partition(" ")
    if not line.startswith(":"):
        return None
    prefix, _, rest = line[1:].partition(" ")
    command, _, params = rest.partition(" ")
    if command != "PRIVMSG":
        return None
    target, sep, text = params.partition(" :")
    if not sep or not target.startswith("#"):
        return None
    return ChatMessage(
        channel=target[1:].lower(),
        user=prefix.partition("!")[0],
        text=text.strip(),
        ts=time.time() if ts is None else ts,
    )


class TwitchChat:
    """Read-only anonymous Twitch chat client with automatic reconnects."""

    def __init__(self, on_message: Callable[[ChatMessage], None]) -> None:
        self._on_message = on_message
        self._wanted: set[str] = set()
        self._joined: set[str] = set()
        self._writer: asyncio.StreamWriter | None = None
        self._sync = asyncio.Event()

    def set_channels(self, channels: set[str]) -> None:
        self._wanted = {c.lower() for c in channels}
        self._sync.set()

    async def run(self) -> None:
        delay = 2.0
        while True:
            started = time.monotonic()
            try:
                await self._session()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - any network failure means reconnect
                log.warning("chat disconnected: %s", e)
            finally:
                self._writer = None
                self._joined = set()
            delay = 2.0 if time.monotonic() - started > 60 else min(delay * 2, 60)
            await asyncio.sleep(delay)

    async def _session(self) -> None:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(HOST, PORT, ssl=True), timeout=20
        )
        self._writer = writer
        self._send(f"NICK justinfan{random.randint(10000, 99999)}")
        log.info("chat connected")
        syncer = asyncio.create_task(self._sync_loop())
        try:
            while True:
                raw = await asyncio.wait_for(reader.readline(), timeout=READ_TIMEOUT)
                if not raw:
                    raise ConnectionError("connection closed by server")
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                if line.startswith("PING"):
                    self._send("PONG" + line[4:])
                elif " RECONNECT" in line[:60]:
                    raise ConnectionError("server asked to reconnect")
                else:
                    msg = parse_privmsg(line)
                    if msg is not None:
                        self._on_message(msg)
        finally:
            syncer.cancel()
            writer.close()

    def _send(self, line: str) -> None:
        if self._writer is not None:
            self._writer.write((line + "\r\n").encode())

    async def _sync_loop(self) -> None:
        """Keep the set of joined channels equal to the wanted one."""
        self._sync.set()
        while True:
            await self._sync.wait()
            self._sync.clear()
            for name in sorted(self._joined - self._wanted):
                self._send(f"PART #{name}")
                self._joined.discard(name)
            pending = sorted(self._wanted - self._joined)
            for i in range(0, len(pending), JOIN_BATCH):
                batch = pending[i : i + JOIN_BATCH]
                self._send("JOIN " + ",".join(f"#{n}" for n in batch))
                self._joined.update(batch)
                if i + JOIN_BATCH < len(pending):
                    await asyncio.sleep(JOIN_PAUSE)
