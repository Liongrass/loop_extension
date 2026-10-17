# Loop

An [LNbits](https://lnbits.com) extension that lets users move sats from their
LNbits wallet to an on-chain Bitcoin address with a
[Lightning Loop](https://lightning.engineering/loop/) Loop Out swap.

It talks to the Loop daemon running inside
[litd](https://github.com/lightninglabs/lightning-terminal) (Lightning
Terminal), reusing the connection LNbits already has to its funding source.

## Requirements

- LNbits 1.0.0 or later.
- litd as the funding source, with Loop enabled in integrated mode, using
  either the `LndWallet` (gRPC) or `LndRestWallet` (REST) backend. Other
  funding sources are not supported.
- A litd **super macaroon** configured as the funding source macaroon, so
  the same credentials can call both lnd and Loop. Encrypted macaroons are
  not supported.
- For REST: `enablerest=true` in litd. litd doesn't serve REST routes for
  daemons running in remote mode.

The extension adds no settings of its own: it uses the endpoint, TLS cert and
macaroon LNbits loaded from its `.env` or admin UI.

## Installation

Put this repository in your LNbits extensions directory as `loop`, then
enable the extension from the LNbits admin UI and restart LNbits if needed.

## Using it

1. Open **Loop** and pick the wallet to pay from.
2. Enter the amount (within Loop's minimum and maximum, shown under the
   field), the destination address and a confirmation target (2 to 100
   blocks, default 9).
3. Tick **Fast** to have the swap published right away. Without it, the
   server may wait up to 30 minutes to batch the swap, which is cheaper.
4. Review the quote and confirm:
   - **You pay over Lightning**: amount plus the swap fee.
   - **Estimated swap fee**: swap fee plus the estimated miner fee.
   - **You receive on-chain**: amount minus the estimated miner fee.

Swaps take a while: the on-chain transaction has to confirm before the swap
completes. **Your swaps** lists each swap's status and refreshes itself while
any swap is still in progress. The ℹ button copies the swap's id (its hash),
which works with `loop swapinfo <id>`.

Admins also get a **Loop daemon** card with Loop's `getinfo` output and a
table of every user's swaps.

## How the money moves

There's no escrow wallet. The swap is paid from the node, and the two
Loop invoices (swap and prepay) are recorded as pending payments in the
user's wallet:

- Before the swap starts, the worst-case cost is reserved in the wallet: the
  swap invoice and prepay, plus LNbits' routing fee reserve and service fee.
  The wallet needs enough balance to cover it, and the usual wallet limits and
  permissions apply.
- If fees have risen since the user saw the quote, the swap isn't started
  (HTTP 409) and the user has to confirm a new quote.
- A background task checks swaps every 30 seconds. It settles each payment
  with its actual routing fee once lnd reports it paid, and fails it only once
  the swap has reached a final state.
- On success, the LNbits service fee (if configured) is charged and credited
  to the service fee wallet, as for any other outgoing payment.
- If a swap fails before anything was paid, the reservation is released. If
  it fails after a payment, an error is logged for an admin to settle.

The miner fee is taken out of the on-chain amount, not the wallet. The
maximum miner fee passed to Loop is 250 times the quoted sweep fee, so a fee
spike doesn't strand the swap.

Swaps carry the label `lnbits-loop:<id>` and the initiator
`LNbits Loop <LNbits version>`, so they can be found in `loop listswaps` and
Loop's logs.

## API

| Method | Path | Key | Description |
| --- | --- | --- | --- |
| GET | `/loop/api/v1/terms` | invoice | Minimum and maximum swap amount |
| GET | `/loop/api/v1/quote?amount=&conf_target=&fast=` | invoice | Quote, including the worst-case total |
| POST | `/loop/api/v1/swaps` | admin | Start a Loop Out |
| GET | `/loop/api/v1/swaps` | invoice | Swaps of the user's wallets |
| GET | `/loop/api/v1/info` | LNbits admin | Loop `getinfo` |
| GET | `/loop/api/v1/swaps/all` | LNbits admin | Swaps of all users |

`POST /loop/api/v1/swaps` takes:

```json
{
  "amount": 250000,
  "address": "bc1q...",
  "max_total": 252000,
  "conf_target": 9,
  "fast": false
}
```

`max_total` is the quote's `max_total`. Starting a swap can take over a
minute, and swaps start one at a time.

## Known limitations

- Swaps from a shared wallet are listed for the wallet's owner, not the user
  it's shared with.
- The confirmation target and fast flag aren't stored with the swap.
- A change to the LNbits service fee setting while a swap is in progress can
  leave the wallet slightly below the reserved amount.

## Development

The extension doesn't use generated gRPC stubs. `loop_grpc_files/loop.proto`
is a trimmed copy of Loop's `client.proto` (with upstream field numbers),
compiled into the descriptor set `loop_grpc_files/loop.desc`, which is loaded
into a private descriptor pool so it can't clash with the lnd protos LNbits
core registers. After changing `loop.proto`, regenerate the descriptor with
`loop_grpc_files/update.sh` (needs `protoc`).

## License

MIT, see [LICENSE](LICENSE).
