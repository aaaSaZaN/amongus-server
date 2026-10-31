"""Read-only status page: rooms, players and their ping.

Costs nothing while nobody is looking: the data is collected per request from
state the server already keeps.
"""
import asyncio
import json
import logging
import os
import time

log = logging.getLogger("dashboard")

PAGE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard.html")
RETRY_AFTER = 5
READ_TIMEOUT = 5
REQUEST_LIMIT = 8192
STARTED = time.monotonic()


def player(actor, conn, host, voice_ips, now):
    return {
        "actor": actor,
        "ip": conn.addr[0],
        "ping": None if conn.rtt is None else round(conn.rtt),
        "silent": int(now - conn.last_seen),
        "host": actor == host,
        "voice": conn.addr[0] in voice_ips,
        "seconds": int(now - conn.state["joined"]),
    }


def room_status(room, voice_room, now):
    voice_ips = {c.addr[0] for c in voice_room.actors.values()} if voice_room else set()
    host = room.plugin.server_actor or room.master
    props = room.properties
    return {
        "code": room.name,
        "map": props.get("mapname"),
        "max_players": props.get("max_players"),
        "in_game": bool(props.get("game_in_progress")),
        "players": [player(n, c, host, voice_ips, now) for n, c in sorted(room.actors.items())],
    }


def status(rooms, voice_rooms):
    """The game does not send player names, so players are told apart by address."""
    now = time.monotonic()
    listed = [room_status(r, voice_rooms.get(f"{r.name}_voice"), now) for r in rooms.values()]
    return {
        "uptime": int(now - STARTED),
        "players": sum(len(r["players"]) for r in listed),
        "rooms": listed,
    }


def response(code, kind, body):
    head = f"HTTP/1.1 {code}\r\nContent-Type: {kind}\r\nContent-Length: {len(body)}\r\n"
    return (head + "Cache-Control: no-store\r\nConnection: close\r\n\r\n").encode() + body


def route(path, page, rooms, voice_rooms):
    if path == "/":
        return response("200 OK", "text/html; charset=utf-8", page)
    if path == "/api/status":
        body = json.dumps(status(rooms, voice_rooms), ensure_ascii=False, default=str).encode()
        return response("200 OK", "application/json", body)
    return response("404 Not Found", "text/plain", b"not found\n")


def parse_address(value):
    host, _, port = value.rpartition(":")
    return host, int(port)


async def serve(address, rooms, voice_rooms):
    host, port = address
    with open(PAGE_PATH, "rb") as file:
        page = file.read()

    async def handle(reader, writer):
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), READ_TIMEOUT)
            parts = head.split(b" ", 2)
            if len(parts) == 3 and parts[0] == b"GET":
                writer.write(route(parts[1].decode("latin-1").split("?")[0], page, rooms, voice_rooms))
                await writer.drain()
        except (asyncio.TimeoutError, asyncio.LimitOverrunError, asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()

    # The NetBird address may appear after the service starts, and the game must not depend on this page.
    while True:
        try:
            server = await asyncio.start_server(handle, host, port, limit=REQUEST_LIMIT)
            break
        except OSError as error:
            log.warning("dashboard on %s:%s not available yet: %s", host, port, error)
            await asyncio.sleep(RETRY_AFTER)
    log.info("dashboard on http://%s:%s", host, port)
    async with server:
        await server.serve_forever()
