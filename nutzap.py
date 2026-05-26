#!/usr/bin/env python3
import argparse
import asyncio
import json
import os
import sys

from dotenv import load_dotenv

load_dotenv()

from nostr import (
    build_nutzap_event,
    npub_to_hex,
    nsec_to_hex,
    privkey_to_pubkey_hex,
    publish_event,
    sign_event,
)
from cashu_utils import decode_cashu_token, swap_to_p2pk
from resolver import DEFAULT_RELAYS, fetch_kind_10019


def parse_args():
    p = argparse.ArgumentParser(description="Send a NIP-61 nutzap over Nostr")
    p.add_argument("token", nargs="?", help="Cashu token (cashuA... or cashuB...)")
    p.add_argument("npub", nargs="?", help="Recipient npub1... or hex pubkey")
    p.add_argument("--message", default="", help="Zap message [default: empty]")
    p.add_argument("--nsec", default=None, help="Sender private key (falls back to $NOSTR_NSEC)")
    p.add_argument("--dry-run", action="store_true", help="Print what would happen without spending or publishing")
    p.add_argument("--relay", action="append", default=[], metavar="URL",
                   help="Extra relay to publish to (repeatable)")
    p.add_argument("--publish-event", metavar="FILE",
                   help="Publish a pre-signed event JSON file (skips token/swap; use - for stdin)")
    return p.parse_args()


async def publish_only(args):
    """Publish a pre-signed event from a JSON file (recovery path)."""
    if args.publish_event == "-":
        event = json.loads(sys.stdin.read())
    else:
        with open(args.publish_event) as f:
            event = json.load(f)
    nsec_str = args.nsec or os.environ.get("NOSTR_NSEC", "")
    privkey_hex = nsec_to_hex(nsec_str) if nsec_str else None
    relays = list(DEFAULT_RELAYS)
    for r in args.relay:
        if r not in relays:
            relays.append(r)
    print(f"📡 Publishing event {event['id'][:16]}... to {len(relays)} relays")
    results = await publish_event(event, relays, privkey_hex=privkey_hex)
    for relay_url, success, note in results:
        status = "✅" if success else "❌"
        suffix = f" — {note}" if note else ""
        print(f"   {status} {relay_url}{suffix}")
    successes = sum(1 for _, ok, _ in results if ok)
    if successes == 0:
        print("❌ Failed to publish to any relay.", file=sys.stderr)
        sys.exit(1)
    print(f"🎉 Published! Event id: {event['id']}")


async def run(args):
    if args.publish_event:
        await publish_only(args)
        return
    # --- Sender key ---
    nsec_str = args.nsec or os.environ.get("NOSTR_NSEC", "")
    if not nsec_str:
        print("Error: sender key required. Set NOSTR_NSEC or pass --nsec.", file=sys.stderr)
        sys.exit(1)
    privkey_hex = nsec_to_hex(nsec_str)
    sender_pubkey_hex = privkey_to_pubkey_hex(privkey_hex)

    # --- Decode token ---
    try:
        token = decode_cashu_token(args.token)
    except Exception as e:
        print(f"Error decoding token: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"✅ Decoded token: {token['amount']} {token['unit']} from {token['mint']}")

    # --- Resolve recipient ---
    recipient_hex = npub_to_hex(args.npub)
    kind10019 = await fetch_kind_10019(recipient_hex, list(DEFAULT_RELAYS))

    if kind10019 is None:
        print("⚠️  Recipient has no kind:10019. Token will be sent without mint validation.")
        print("   The recipient may not be watching for nutzaps or may not be able to redeem.")
        p2pk_pubkey = "02" + recipient_hex
        trusted_mints = []
        extra_relays: list = []
    else:
        trusted_mints = kind10019["mints"]
        p2pk_pubkey = kind10019["p2pk_pubkey"]
        extra_relays = kind10019["relays"]
        if trusted_mints and token["mint"] not in trusted_mints:
            print(f"⚠️  Warning: mint {token['mint']} not in recipient's trusted mints.")
            print(f"   Trusted: {trusted_mints}")

    print(f"🔑 Recipient: {args.npub}")

    # --- Build relay list (defaults + kind:10019 relays + CLI extras) ---
    relays: list[str] = list(DEFAULT_RELAYS)
    for r in extra_relays + args.relay:
        if r not in relays:
            relays.append(r)

    if args.dry_run:
        print(f"\n--- DRY RUN (nothing spent or published) ---")
        print(f"  mint    : {token['mint']}")
        print(f"  amount  : {token['amount']} {token['unit']}")
        print(f"  P2PK to : {p2pk_pubkey}")
        print(f"  relays  : {relays}")
        return

    # --- P2PK swap at mint ---
    print(f"🔒 Swapping proofs at {token['mint']} ...")
    try:
        locked_proofs = swap_to_p2pk(token["mint"], token["proofs"], p2pk_pubkey, token["unit"])
    except Exception as e:
        print(f"Error during mint swap: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"🔒 Swapped proofs: P2PK-locked to {p2pk_pubkey}")

    # --- Build + sign event ---
    event = build_nutzap_event(
        sender_pubkey_hex=sender_pubkey_hex,
        recipient_pubkey_hex=recipient_hex,
        locked_proofs=locked_proofs,
        mint_url=token["mint"],
        amount_total=token["amount"],
        unit=token["unit"],
        message=args.message,
    )
    event = sign_event(event, privkey_hex)

    # --- Publish ---
    print(f"📡 Publishing to {len(relays)} relays...")
    results = await publish_event(event, relays, privkey_hex=privkey_hex)
    for relay_url, success, note in results:
        status = "✅" if success else "❌"
        suffix = f" — {note}" if note else ""
        print(f"   {status} {relay_url}{suffix}")

    successes = sum(1 for _, ok, _ in results if ok)
    if successes == 0:
        print("❌ Failed to publish to any relay.", file=sys.stderr)
        sys.exit(1)
    print(f"🎉 Nutzap sent! Event id: {event['id']}")


def main():
    asyncio.run(run(parse_args()))


if __name__ == "__main__":
    main()
