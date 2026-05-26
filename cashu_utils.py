import base64
import json
import os

import coincurve
import httpx

# Try to import cashu's b_dhke; support multiple install paths across versions.
try:
    from cashu.core.crypto.b_dhke import step1_alice, step3_alice
except ImportError:
    try:
        from cashu.crypto.b_dhke import step1_alice, step3_alice
    except ImportError:
        step1_alice = step3_alice = None


def _require_dhke():
    if step1_alice is None:
        raise ImportError(
            "cashu DHKE module not found. Install with: pip install 'cashu>=0.16.0'"
        )


def decode_cashu_token(token_str: str) -> dict:
    token_str = token_str.strip()
    if token_str.startswith("cashuA"):
        data = base64.urlsafe_b64decode(token_str[6:] + "==")
        obj = json.loads(data)
        entry = obj["token"][0]
        proofs = entry["proofs"]
        mint = entry["mint"]
        unit = obj.get("unit", "sat")
    elif token_str.startswith("cashuB"):
        import cbor2
        data = base64.urlsafe_b64decode(token_str[6:] + "==")
        obj = cbor2.loads(data)
        mint = obj.get("m") or ""
        token_entries = obj.get("t", [])
        proofs = []
        if token_entries:
            entry = token_entries[0]
            keyset_id_raw = entry.get("i", b"")
            keyset_id = keyset_id_raw.hex() if isinstance(keyset_id_raw, bytes) else str(keyset_id_raw)
            for p in entry.get("p", []):
                c_raw = p.get("c", b"")
                proofs.append({
                    "amount": p.get("a", 0),
                    "secret": p.get("s", ""),
                    "C": c_raw.hex() if isinstance(c_raw, bytes) else c_raw,
                    "id": keyset_id,
                })
        unit = obj.get("u", "sat")
        if isinstance(unit, bytes):
            unit = unit.decode()
    else:
        raise ValueError(f"Unknown token format: {token_str[:10]!r}")

    amount = sum(p["amount"] for p in proofs)
    return {"mint": mint.rstrip("/"), "proofs": proofs, "unit": unit, "amount": amount}


def _get_active_keyset(mint_url: str, unit: str) -> tuple[str, dict]:
    """Returns (keyset_id, {amount_str: pubkey_hex}) for the unit's active keyset."""
    resp = httpx.get(f"{mint_url}/v1/keys", timeout=10)
    resp.raise_for_status()
    keysets = resp.json().get("keysets", [])
    for ks in keysets:
        if ks.get("unit", "sat") == unit:
            return ks["id"], ks["keys"]
    if keysets:
        ks = keysets[0]
        return ks["id"], ks["keys"]
    raise ValueError(f"No keysets returned by {mint_url}")


def _split_amount(amount: int, available: list[int]) -> list[int]:
    """Decompose amount into denominations from available list; returns ascending order."""
    result = []
    remaining = amount
    for denom in sorted(available, reverse=True):
        while remaining >= denom:
            result.append(denom)
            remaining -= denom
    if remaining != 0:
        raise ValueError(f"Cannot exactly split {amount} using denominations {sorted(available)}")
    return sorted(result)


def swap_to_p2pk(mint_url: str, proofs: list, p2pk_pubkey_hex: str, unit: str = "sat") -> list:
    """Swap proofs at the mint for new P2PK-locked proofs. Returns list of locked proof dicts."""
    _require_dhke()

    keyset_id, keyset_keys = _get_active_keyset(mint_url, unit)
    total = sum(p["amount"] for p in proofs)
    available_denoms = [int(k) for k in keyset_keys]
    output_amounts = _split_amount(total, available_denoms)

    # Build blinded outputs with P2PK secrets (NUT-11 + NUT-00)
    secrets, rs, blinded_messages = [], [], []
    for amount in output_amounts:
        nonce = os.urandom(32).hex()
        # NUT-11: secret must be this exact JSON string stored verbatim in Proof.secret
        secret = json.dumps(
            ["P2PK", {"nonce": nonce, "data": p2pk_pubkey_hex}],
            separators=(",", ":"),
        )
        B_, r = step1_alice(secret)
        secrets.append(secret)
        rs.append(r)
        blinded_messages.append({
            "amount": amount,
            "id": keyset_id,
            "B_": B_.format(compressed=True).hex(),
        })

    # POST /v1/swap (NUT-03)
    resp = httpx.post(
        f"{mint_url}/v1/swap",
        json={"inputs": proofs, "outputs": blinded_messages},
        timeout=30,
    )
    if resp.is_error:
        raise ValueError(f"Mint swap failed {resp.status_code}: {resp.text}")
    signatures = resp.json()["signatures"]

    if len(signatures) != len(blinded_messages):
        raise ValueError(
            f"Mint returned {len(signatures)} signatures for {len(blinded_messages)} outputs"
        )

    # Unblind signatures → locked proofs
    locked_proofs = []
    for i, sig in enumerate(signatures):
        amount = sig["amount"]
        C_ = coincurve.PublicKey(bytes.fromhex(sig["C_"]))
        K = coincurve.PublicKey(bytes.fromhex(keyset_keys[str(amount)]))
        C = step3_alice(C_, rs[i], K)
        proof = {
            "amount": amount,
            "id": sig["id"],
            "secret": secrets[i],
            "C": C.format(compressed=True).hex(),
        }
        # Pass through DLEQ proof if mint provided one (NIP-61 recommends including it)
        if "dleq" in sig:
            proof["dleq"] = {**sig["dleq"], "r": rs[i].secret.hex()}
        locked_proofs.append(proof)

    return locked_proofs
