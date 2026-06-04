# nutzap-cli

Send [NIP-61](https://github.com/nostr-protocol/nips/blob/master/61.md) nutzaps from the command line. Takes a Cashu token, P2PK-locks the proofs to the recipient via a mint swap, and publishes a `kind:9321` event to Nostr.

## How it works

1. Decodes the Cashu token (v3 `cashuA` or v4 `cashuB`)
2. Looks up the recipient's `kind:10019` event to get their trusted mints, P2PK lock pubkey, and preferred relays
3. Swaps the proofs at the mint for new proofs locked to the recipient's key ([NUT-11](https://github.com/cashubtc/nuts/blob/main/11.md))
4. Builds and signs a `kind:9321` nutzap event ([NIP-61](https://github.com/nostr-protocol/nips/blob/master/61.md))
5. Publishes to relays with NIP-42 AUTH support

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)

## Setup

```bash
# Clone and enter the repo
git clone <repo-url>
cd nutzap-cli

# Create virtualenv and install dependencies
uv sync
```

### Generate a sender keypair

```bash
# Using nak
nak key generate | nak encode nsec          # nsec1...
nak key generate | nak key public | nak encode npub  # npub1...

# Or derive npub from an existing hex privkey
echo <hex> | nak encode nsec
```

Set your key via environment variable or pass it with `--nsec`:

```bash
export NOSTR_NSEC="nsec1..."
# or use a .env file in the project directory
```

## Usage

```
python nutzap.py <token> <npub> [OPTIONS]
```

### Arguments

| Argument | Description |
|----------|-------------|
| `token`  | Cashu token string (`cashuA...` or `cashuB...`) |
| `npub`   | Recipient's npub or hex pubkey |

### Options

| Option | Description |
|--------|-------------|
| `--message TEXT` | Zap message (default: empty) |
| `--nsec TEXT` | Sender private key (falls back to `$NOSTR_NSEC`) |
| `--relay URL` | Extra relay to publish to (repeatable) |
| `--dry-run` | Preview what would happen — nothing is spent or published |
| `--publish-event FILE` | Publish a pre-signed event JSON file, skipping token/swap (use `-` for stdin) |

### Examples

```bash
# Send a nutzap
export NOSTR_NSEC="nsec1..."
python nutzap.py "cashuAeyJ..." "npub1wetq..." --message "gm"

# Preview without spending
python nutzap.py "cashuAeyJ..." "npub1wetq..." --dry-run

# Publish a previously built event (recovery)
python nutzap.py --publish-event event.json --nsec nsec1...
python nutzap.py --publish-event - --nsec nsec1... < event.json
```

### Example output

```
✅ Decoded token: 21 sat from https://mint.minibits.cash/Bitcoin
🔑 Recipient: npub1wetq...
🔒 Swapping proofs at https://mint.minibits.cash/Bitcoin ...
🔒 Swapped proofs: P2PK-locked to 022abb8d...
📡 Publishing to 3 relays...
   ✅ wss://relay1.blackbyte.nl
   ✅ wss://relay2.blackbyte.nl
   ✅ wss://relay.damus.io
🎉 Nutzap sent! Event id: abc123...
```

## Default relays

Events are published to these relays by default, merged with any `relay` tags from the recipient's `kind:10019`:

- `wss://relay1.blackbyte.nl`
- `wss://relay2.blackbyte.nl`
- `wss://relay.damus.io`

Use `--relay` to add extras.

## Relay AUTH (NIP-42)

If a relay requires authentication, pass `--nsec` (or set `$NOSTR_NSEC`) and the tool will automatically respond to AUTH challenges with a signed `kind:22242` event.

Haven relays with write whitelisting require your sender pubkey to be added to the whitelist before AUTH will be accepted.

## Recipient without kind:10019

If the recipient has no `kind:10019` event, the tool warns you and falls back to:
- P2PK locking to `02` + recipient's x-only Nostr pubkey
- No mint validation
- Default relays only

The recipient may not be able to redeem in this case.

## File structure

```
nutzap-cli/
├── nutzap.py        # CLI entry point and orchestration
├── nostr.py         # Key handling, event build/sign/publish, NIP-42 AUTH
├── cashu_utils.py   # Token decode (v3/v4), mint keyset lookup, P2PK swap
├── resolver.py      # kind:10019 relay fetch with fallback
├── pyproject.toml
├── LICENSE
└── README.md
```

> **Note:** The Cashu module is named `cashu_utils.py` (not `cashu.py`) to avoid shadowing the installed `cashu` package on `sys.path`.

## Development

### Running locally

```bash
# Dry run (nothing spent or published)
python nutzap.py "cashuAeyJ..." "npub1..." --dry-run

# Live run with a small token first
python nutzap.py "cashuAeyJ..." "npub1..." --nsec nsec1...
```

### Dependencies

| Package | Purpose |
|---------|---------|
| `cashu` | DHKE crypto (`step1_alice`, `step3_alice`) for P2PK blinding/unblinding |
| `secp256k1` | Schnorr signing for Nostr events (BIP-340) |
| `coincurve` | Used internally by `cashu` for elliptic curve operations |
| `websockets` | Nostr relay communication |
| `httpx` | Mint REST API calls |
| `bech32` | npub/nsec encode/decode |
| `cbor2` | Cashu v4 token decoding |
| `python-dotenv` | `.env` file support for `NOSTR_NSEC` |

### Protocol references

- [NIP-01](https://github.com/nostr-protocol/nips/blob/master/01.md) — Nostr event format and signing
- [NIP-42](https://github.com/nostr-protocol/nips/blob/master/42.md) — Relay authentication
- [NIP-61](https://github.com/nostr-protocol/nips/blob/master/61.md) — Nutzaps (`kind:9321`, `kind:10019`)
- [NUT-00](https://github.com/cashubtc/nuts/blob/main/00.md) — Cashu token format and DHKE
- [NUT-03](https://github.com/cashubtc/nuts/blob/main/03.md) — Swap endpoint
- [NUT-11](https://github.com/cashubtc/nuts/blob/main/11.md) — P2PK spending conditions

## License

MIT — see [LICENSE](LICENSE)
