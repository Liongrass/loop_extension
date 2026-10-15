from datetime import datetime, timezone

from pydantic import BaseModel, Field

# loopd's minimum sweep confirmation target is 2.
MIN_CONF_TARGET = 2
MAX_CONF_TARGET = 100
DEFAULT_CONF_TARGET = 9


class LoopOutTerms(BaseModel):
    min_swap_amount: int
    max_swap_amount: int


class LoopOutQuote(BaseModel):
    amount: int
    swap_fee: int
    prepay_amount: int
    miner_fee_estimate: int
    max_miner_fee: int
    max_swap_routing_fee: int
    max_prepay_routing_fee: int
    conf_target: int
    fast: bool
    server_pubkey: str
    # What the wallet must hold: everything paid over Lightning in the worst
    # case. The miner fee comes out of the on-chain amount instead.
    max_total: int


class CreateLoopOut(BaseModel):
    amount: int = Field(gt=0)
    address: str = Field(min_length=1, max_length=100)
    conf_target: int = Field(
        DEFAULT_CONF_TARGET, ge=MIN_CONF_TARGET, le=MAX_CONF_TARGET
    )
    # Let the server publish the HTLC right away, for a higher fee.
    fast: bool = False
    # The max_total of the quote the user approved. The swap is refused if a
    # fresh quote would need more.
    max_total: int = Field(gt=0)


class LoopSwap(BaseModel):
    id: str
    wallet: str
    amount: int
    address: str
    # starting: reserved, LoopOut not answered yet
    # unknown: LoopOut got no answer, resolved later via ListSwaps by label
    # pending, success, failed
    status: str = "starting"
    loop_state: str | None = None
    failure_reason: str | None = None
    swap_hash: str | None = None
    htlc_address: str | None = None
    server_pubkey: str
    swap_fee: int
    prepay_amount: int
    max_miner_fee: int
    max_swap_routing_fee: int
    max_prepay_routing_fee: int
    # checking_id of the swap payment. It starts out as the placeholder that
    # reserves the worst case and becomes the swap payment once found in lnd.
    swap_checking_id: str
    # checking_id of the prepay record, once recorded.
    prepay_checking_id: str | None = None
    # Hash of loopd's prepay payment in lnd, if it could be matched.
    prepay_hash: str | None = None
    payments_recorded: bool = False
    cost_server: int = 0
    cost_onchain: int = 0
    cost_offchain: int = 0
    # True once the swap and all its LNbits payments are final.
    done: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AdminLoopSwap(LoopSwap):
    # None if the wallet was deleted.
    wallet_name: str | None = None
