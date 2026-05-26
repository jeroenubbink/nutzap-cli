import asyncio
import hashlib
import json
import time

import websockets
from bech32 import bech32_decode, convertbits
from secp256k1 import PrivateKey


def _decode_bech32(key_str: str) -> bytes:
    hrp, data = bech32_decode(key_str)
    if data is None:
        raise ValueError(f"Invalid bech32 string: {key_str[:20]}")
    return bytes(convertbits(data, 5, 8, False))


def npub_to_hex(npub: str) -> str:
    if npub.startswith("npub1"):
        return _decode_bech32(npub).hex()
    return npub


def nsec_to_hex(nsec: str) -> str:
    if nsec.startswith("nsec1"):
        return _decode_bech32(nsec).hex()
    return nsec


def privkey_to_pubkey_hex(privkey_hex: str) -> str:
    """Returns x-only (32-byte) pubkey as hex — what Nostr uses for event.pubkey."""
    sk = PrivateKey(bytes.fromhex(privkey_hex))
    return sk.pubkey.serialize(compressed=True)[1:].hex()


def compute_event_id(event: dict) -> str:
    serialized = json.dumps(
        [0, event["pubkey"], event["created_at"], event["kind"], event["tags"], event["content"]],
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(serialized.encode()).hexdigest()


def build_nutzap_event(
    sender_pubkey_hex: str,
    recipient_pubkey_hex: str,
    locked_proofs: list,
    mint_url: str,
    amount_total: int,
    unit: str = "sat",
    message: str = "",
) -> dict:
    tags = [
        ["amount", str(amount_total)],
        ["unit", unit],
        ["u", mint_url],
        ["p", recipient_pubkey_hex],
    ]
    for proof in locked_proofs:
        tags.append(["proof", json.dumps(proof, separators=(",", ":"))])
    event = {
        "kind": 9321,
        "pubkey": sender_pubkey_hex,
        "created_at": int(time.time()),
        "tags": tags,
        "content": message,
    }
    event["id"] = compute_event_id(event)
    return event


def sign_event(event: dict, privkey_hex: str) -> dict:
    """Schnorr-sign event (BIP-340). event["id"] is already the sha256 hash."""
    sk = PrivateKey(bytes.fromhex(privkey_hex))
    sig = sk.schnorr_sign(bytes.fromhex(event["id"]), None, raw=True)
    event["sig"] = sig.hex()
    return event


def _build_auth_event(challenge: str, relay_url: str, pubkey_hex: str, privkey_hex: str) -> dict:
    """Build and sign a NIP-42 kind:22242 auth response event."""
    event = {
        "kind": 22242,
        "pubkey": pubkey_hex,
        "created_at": int(time.time()),
        "tags": [["relay", relay_url], ["challenge", challenge]],
        "content": "Nostr authentication",
    }
    event["id"] = compute_event_id(event)
    return sign_event(event, privkey_hex)


async def publish_event(event: dict, relays: list, privkey_hex: str | None = None) -> list:
    """Publish event to relays. If privkey_hex is provided, handles NIP-42 AUTH challenges."""
    msg = json.dumps(["EVENT", event])
    pubkey_hex = event.get("pubkey", "")
    results = []
    for relay_url in relays:
        try:
            async with websockets.connect(relay_url, open_timeout=5) as ws:
                await ws.send(msg)
                authed = False
                while True:
                    raw = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                    if raw[0] == "AUTH" and privkey_hex and not authed:
                        # NIP-42: relay sent challenge — sign and respond, then resend EVENT
                        auth_event = _build_auth_event(raw[1], relay_url, pubkey_hex, privkey_hex)
                        await ws.send(json.dumps(["AUTH", auth_event]))
                        await ws.send(msg)
                        authed = True
                    elif raw[0] == "OK":
                        success = raw[2] if len(raw) > 2 else False
                        note = raw[3] if len(raw) > 3 else ""
                        # This OK is the rejection of our pre-auth EVENT — skip it and wait
                        # for the OK from the EVENT we resent after authenticating
                        if authed and not success and "auth-required" in note:
                            continue
                        # Haven sends AUTH then OK(rejected) — keep listening for AUTH
                        if not authed and not success and "auth-required" in note and privkey_hex:
                            continue
                        results.append((relay_url, success, note))
                        break
                    # NOTICE messages are informational; keep waiting for OK
        except Exception as e:
            results.append((relay_url, False, str(e)))
    return results
