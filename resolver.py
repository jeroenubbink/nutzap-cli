import asyncio
import json

import websockets

DEFAULT_RELAYS = [
    "wss://relay1.blackbyte.nl",
    "wss://relay2.blackbyte.nl",
    "wss://relay.damus.io",
]


async def fetch_kind_10019(pubkey_hex: str, relays: list) -> dict | None:
    """
    Fetch recipient's kind:10019 nutzap info event.

    Returns dict with keys: mints, p2pk_pubkey, relays.
    Returns None if not found on any relay.
    """
    req = json.dumps(["REQ", "sub1", {"kinds": [10019], "authors": [pubkey_hex], "limit": 1}])
    for relay_url in relays:
        try:
            async with websockets.connect(relay_url, open_timeout=5) as ws:
                await ws.send(req)
                while True:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                    if msg[0] == "EVENT":
                        tags = msg[2].get("tags", [])
                        mints = [t[1].rstrip("/") for t in tags if t[0] == "mint"]
                        relays_out = [t[1] for t in tags if t[0] == "relay"]
                        p2pk_tags = [t[1] for t in tags if t[0] == "pubkey"]
                        # If no pubkey tag, derive from recipient's x-only Nostr pubkey
                        p2pk = p2pk_tags[0] if p2pk_tags else "02" + pubkey_hex
                        return {"mints": mints, "p2pk_pubkey": p2pk, "relays": relays_out}
                    elif msg[0] == "EOSE":
                        break
        except Exception:
            continue
    return None
