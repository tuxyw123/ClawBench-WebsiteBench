"""Small allowlist proxy that gives the Agent access only to its model API."""

from __future__ import annotations

import asyncio
import os
from urllib.parse import urlsplit


MAX_HEADER_BYTES = 65_536


class ModelProxy:
    def __init__(self, allowed_host: str, allowed_port: int) -> None:
        self.allowed_host = allowed_host.casefold().rstrip(".")
        self.allowed_port = allowed_port

    def allowed(self, host: str, port: int) -> bool:
        return host.casefold().rstrip(".") == self.allowed_host and port == self.allowed_port

    @staticmethod
    async def relay(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while data := await reader.read(64 * 1024):
                writer.write(data)
                await writer.drain()
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            writer.close()

    async def tunnel(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
        host: str,
        port: int,
    ) -> None:
        if not self.allowed(host, port):
            client_writer.write(b"HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n")
            await client_writer.drain()
            client_writer.close()
            return
        try:
            remote_reader, remote_writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=15
            )
        except (OSError, TimeoutError):
            client_writer.write(b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n")
            await client_writer.drain()
            client_writer.close()
            return
        client_writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await client_writer.drain()
        await asyncio.gather(
            self.relay(client_reader, remote_writer),
            self.relay(remote_reader, client_writer),
            return_exceptions=True,
        )

    async def forward_http(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
        request_line: str,
        headers: list[str],
        initial_body: bytes,
    ) -> None:
        method, target, version = request_line.split(" ", 2)
        parsed = urlsplit(target)
        port = parsed.port or 80
        if parsed.scheme != "http" or not parsed.hostname or not self.allowed(parsed.hostname, port):
            client_writer.write(b"HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n")
            await client_writer.drain()
            client_writer.close()
            return
        try:
            remote_reader, remote_writer = await asyncio.wait_for(
                asyncio.open_connection(parsed.hostname, port), timeout=15
            )
        except (OSError, TimeoutError):
            client_writer.write(b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n")
            await client_writer.drain()
            client_writer.close()
            return
        path = parsed.path or "/"
        if parsed.query:
            path += f"?{parsed.query}"
        filtered = [line for line in headers if not line.casefold().startswith("proxy-")]
        outbound = f"{method} {path} {version}\r\n" + "\r\n".join(filtered) + "\r\n\r\n"
        content_length = 0
        for line in headers:
            name, separator, value = line.partition(":")
            if separator and name.casefold() == "content-length":
                try:
                    content_length = int(value.strip())
                except ValueError:
                    client_writer.close()
                    remote_writer.close()
                    return
        if not 0 <= content_length <= 32 * 1024 * 1024:
            client_writer.close()
            remote_writer.close()
            return
        request_body = initial_body
        if len(request_body) < content_length:
            request_body += await client_reader.readexactly(content_length - len(request_body))
        remote_writer.write(outbound.encode("latin-1") + request_body)
        await remote_writer.drain()
        await self.relay(remote_reader, client_writer)
        remote_writer.close()

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=15)
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, TimeoutError):
            writer.close()
            return
        if len(raw) > MAX_HEADER_BYTES:
            writer.close()
            return
        head, _, body = raw.partition(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")
        try:
            method, target, _version = lines[0].split(" ", 2)
        except ValueError:
            writer.close()
            return
        if method.upper() == "CONNECT":
            host, separator, port_text = target.rpartition(":")
            try:
                port = int(port_text) if separator else 443
            except ValueError:
                writer.close()
                return
            await self.tunnel(reader, writer, host if separator else target, port)
            return
        await self.forward_http(reader, writer, lines[0], lines[1:], body)


async def serve() -> None:
    host = os.environ.get("MODEL_API_HOST", "api.openai.com")
    port = int(os.environ.get("MODEL_API_PORT", "443"))
    listen_port = int(os.environ.get("MODEL_PROXY_PORT", "7200"))
    proxy = ModelProxy(host, port)
    server = await asyncio.start_server(
        proxy.handle,
        host="0.0.0.0",
        port=listen_port,
        limit=MAX_HEADER_BYTES,
    )
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(serve())
