#!/usr/bin/env python3
"""Loopback-only Responses router for the Codex model picker.

The local router token authenticates Codex to this loopback service. Each
upstream route then uses its own credential. Mindlogic reads its key from the
user's existing .env file at request time.
"""

from __future__ import annotations

import argparse
from http import HTTPStatus
from http.client import HTTPSConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import socket
import ssl
import sys
import threading
import uuid


OPENAI_HOST = "chatgpt.com"
OPENAI_PREFIX = "/backend-api/codex"
MINDLOGIC_HOST = "factchat-cloud.mindlogic.ai"
MINDLOGIC_PREFIX = "/v1/gateway"
MAX_REQUEST_BYTES = 32 * 1024 * 1024
LOCAL_TOKEN_HEADER = "X-Mindlogic-Router-Token"
HOP_HEADERS = {"host", "connection", "content-length", "accept-encoding", "transfer-encoding", "keep-alive", "proxy-authorization", "proxy-authenticate", "te", "trailer", "upgrade", LOCAL_TOKEN_HEADER.lower()}


def read_mindlogic_key(path: Path) -> str:
    if not path.is_file():
        raise ValueError("FACTCHAT_API_KEY is not registered")
    for line in path.read_text().splitlines():
        match = re.match(r"^\s*(?:export\s+)?FACTCHAT_API_KEY\s*=\s*(.*?)\s*$", line)
        if match:
            value = match.group(1).strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            if value and "\r" not in value and "\n" not in value:
                return value
    raise ValueError("FACTCHAT_API_KEY is not registered")


def remove_opaque_reasoning(value):
    """Keep visible history and tool calls; never replay encrypted reasoning across providers."""
    if isinstance(value, list):
        return [remove_opaque_reasoning(item) for item in value
                if not (isinstance(item, dict) and item.get("type") == "reasoning")]
    if isinstance(value, dict):
        return {key: remove_opaque_reasoning(item) for key, item in value.items()}
    return value


class Router(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, *, manifest: dict, env_file: Path):
        super().__init__(address, Handler)
        self.manifest = manifest
        self.env_file = env_file
        self.thread_routes = {}
        self.thread_lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        pass

    def reply(self, status: HTTPStatus, code: str, detail: str) -> None:
        data = json.dumps({"error": {"type": code, "message": detail}}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/health":
            self.reply(HTTPStatus.OK, "ok", "ready")
        else:
            self.reply(HTTPStatus.NOT_FOUND, "unknown_path", "Only /responses is supported")

    def do_POST(self):
        request_id = str(uuid.uuid4())
        headers_sent = False
        if self.path != "/responses":
            self.reply(HTTPStatus.NOT_IMPLEMENTED, "unsupported_path", "Only HTTP Responses is supported")
            return
        try:
            local_token = self.server.manifest.get("local_token")
            if not local_token or self.headers.get(LOCAL_TOKEN_HEADER) != local_token:
                self.reply(HTTPStatus.UNAUTHORIZED, "router_auth_missing", "Local router authentication failed")
                return
            size = int(self.headers.get("Content-Length", "-1"))
            if not 0 <= size <= MAX_REQUEST_BYTES:
                raise ValueError("Invalid request size")
            payload = json.loads(self.rfile.read(size))
            if not isinstance(payload, dict):
                raise ValueError("Responses body must be an object")
            alias = payload.get("model")
            mapping = self.server.manifest["routes"].get(alias)
            if mapping is None:
                self.reply(HTTPStatus.BAD_REQUEST, "unknown_model", "The selected model is not in the installed router catalog")
                return
            if payload.get("previous_response_id") or payload.get("conversation"):
                self.reply(HTTPStatus.CONFLICT, "server_reference", "Server response references cannot cross provider routes")
                return
            route, upstream_model = mapping["provider"], mapping["model"]
            if route == "openai" and not self.headers.get("Authorization", "").startswith("Bearer "):
                self.reply(HTTPStatus.SERVICE_UNAVAILABLE, "openai_auth_unavailable",
                           "OpenAI subscription authentication is unavailable for this local provider")
                return
            payload["model"] = upstream_model
            thread_id = self.headers.get("thread-id")
            with self.server.thread_lock:
                previous_route = self.server.thread_routes.get(thread_id) if thread_id else None
                if thread_id:
                    self.server.thread_routes[thread_id] = route
            if previous_route != route:
                payload["input"] = remove_opaque_reasoning(payload.get("input", []))
            payload["store"] = False
            body = json.dumps(payload, separators=(",", ":")).encode()
            if route == "openai":
                host, prefix = OPENAI_HOST, OPENAI_PREFIX
                headers = {name: value for name, value in self.headers.items()
                           if name.lower() not in HOP_HEADERS}
            elif route == "mindlogic":
                host, prefix = MINDLOGIC_HOST, MINDLOGIC_PREFIX
                payload.pop("prompt_cache_key", None)
                payload.pop("client_metadata", None)
                body = json.dumps(payload, separators=(",", ":")).encode()
                headers = {"Authorization": "Bearer " + read_mindlogic_key(self.server.env_file),
                           "Content-Type": "application/json", "Accept": "text/event-stream"}
            else:
                raise ValueError("Invalid installed route")
            headers["Content-Length"] = str(len(body))
            metadata = {"request_id": request_id, "alias": alias, "provider": route,
                        "model": upstream_model, "destination": f"{host}{prefix}/responses",
                        "auth_type": "chatgpt_bearer" if route == "openai" else "mindlogic_key"}
            system_ca = Path("/etc/ssl/cert.pem")
            context = ssl.create_default_context(cafile=str(system_ca) if system_ca.is_file() else None)
            connection = HTTPSConnection(host, timeout=120, context=context)
            try:
                connection.request("POST", prefix + "/responses", body=body, headers=headers)
                response = connection.getresponse()
                self.send_response(response.status)
                self.send_header("Content-Type", response.getheader("Content-Type", "application/json"))
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()
                headers_sent = True
                metadata["http_status"] = response.status
                print(json.dumps(metadata, ensure_ascii=False), flush=True)
                while chunk := response.read1(8192):
                    self.wfile.write(chunk)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                metadata["client_cancelled"] = True
                print(json.dumps(metadata, ensure_ascii=False), flush=True)
            finally:
                connection.close()
        except (ValueError, KeyError, json.JSONDecodeError) as error:
            if not headers_sent:
                self.reply(HTTPStatus.BAD_REQUEST, "invalid_request", str(error))
        except (OSError, socket.timeout) as error:
            if not headers_sent:
                self.reply(HTTPStatus.BAD_GATEWAY, "upstream_error", type(error).__name__)
            else:
                print(json.dumps({"request_id": request_id, "stream_error": type(error).__name__}), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18762)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    if not isinstance(manifest.get("routes"), dict) or not manifest.get("local_token"):
        raise SystemExit("Invalid router manifest")
    server = Router(("127.0.0.1", args.port), manifest=manifest, env_file=args.env_file)
    print(json.dumps({"event": "listening", "host": "127.0.0.1", "port": args.port}), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
