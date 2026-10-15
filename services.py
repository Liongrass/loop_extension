"""
Loop Out for LNbits users.

loopd pays two invoices through the node's lnd: a small prepay, and the swap
invoice (amount + swap fee - prepay). Both are recorded as pending outgoing
payments in the user's wallet, with their real bolt11 and payment hash. The
miner fee comes out of the on-chain amount and never touches the LNbits
balance.

Both payments use internal_ checking_ids, so core's pending checker leaves
them alone: loopd retries a timed out payment under the same hash, and core
would otherwise mark it failed (releasing the user's funds) before the retry
succeeds. Instead, this extension settles them from lnd itself: a success as
soon as lnd reports it, a failure only once the swap is final.

Lifecycle of a swap:
1. A placeholder payment reserves the worst case in the wallet, under core's
   per-wallet payment lock.
2. LoopOut is called with a label that identifies the swap.
3. The two payments are found in lnd's payment list. The placeholder becomes
   the swap payment, and the prepay is recorded next to it. No Loop RPC
   returns the prepay invoice, so it's matched by destination, amount and
   time. If it can't be matched unambiguously, it's settled from Loop's final
   swap costs instead.
4. A background task follows the swap with SwapInfo and settles both payments
   with lnd (failures only once the swap is final).
"""

import asyncio
import math
import time
import uuid
from datetime import datetime
from http import HTTPStatus

from bolt11 import Bolt11, MilliSatoshi, Tags
from bolt11 import decode as bolt11_decode
from bolt11 import encode as bolt11_encode
from fastapi import HTTPException
from lnbits.core.crud import (
    create_payment,
    get_standalone_payment,
    get_wallet,
    update_payment,
)
from lnbits.core.models import CreatePayment, Payment, PaymentState, Wallet
from lnbits.core.services.payments import (
    check_wallet_limits,
    fee_reserve,
    payment_lock,
    service_fee,
    wallets_payments_lock,
)
from lnbits.decorators import check_user_extension_access
from lnbits.exceptions import PaymentError
from lnbits.settings import settings
from lnbits.utils.crypto import fake_privkey, random_secret_and_hash
from lnbits.wallets import get_funding_source
from lnbits.wallets.lnd_grpc_files import lightning_pb2
from loguru import logger

from .crud import create_swap, get_unfinished_swaps, update_swap
from .helpers import (
    LIST_SWAPS,
    LND_LIST_PAYMENTS,
    LOOP_OUT,
    LOOP_OUT_QUOTE,
    LOOP_OUT_TERMS,
    SWAP_INFO,
    LitdError,
    LitdUnreachableError,
    litd_call,
)
from .models import CreateLoopOut, LoopOutQuote, LoopOutTerms, LoopSwap

EXT_ID = "loop"
# Same defaults as the loop CLI.
SWEEP_CONF_TARGET = 9
MINER_FEE_MULTIPLIER = 250
# How long to look for loopd's payments in lnd right after LoopOut.
FIND_PAYMENTS_ATTEMPTS = 10
FIND_PAYMENTS_INTERVAL = 2
# How long a swap whose LoopOut got no answer may stay unresolved before the
# reservation is released, if loopd doesn't know the swap's label.
UNKNOWN_SWAP_TIMEOUT = 10 * 60

# Starting swaps one at a time keeps the prepay match unambiguous. The
# background sync takes it too, so it never races a swap being started.
_swap_lock = asyncio.Lock()


def _sat_ceil(msat: int) -> int:
    return math.ceil(msat / 1000)


def _max_routing_fee(amount_sat: int) -> int:
    # Use LNbits' own fee reserve rules, like any other outgoing payment.
    return _sat_ceil(fee_reserve(amount_sat * 1000))


def _reserve_msat(amount_sat: int, max_routing_fee_sat: int) -> int:
    """Fee reserve of one recorded payment: routing limit plus service fee."""
    return max_routing_fee_sat * 1000 + service_fee(amount_sat * 1000)


def _label(swap_id: str) -> str:
    return f"lnbits-loop:{swap_id}"


async def get_terms() -> LoopOutTerms:
    try:
        terms = await litd_call(LOOP_OUT_TERMS, LOOP_OUT_TERMS.request())
    except (LitdError, LitdUnreachableError) as exc:
        raise HTTPException(HTTPStatus.BAD_GATEWAY, str(exc)) from exc
    return LoopOutTerms(
        min_swap_amount=terms.min_swap_amount,
        max_swap_amount=terms.max_swap_amount,
    )


async def get_quote(amount: int) -> LoopOutQuote:
    request = LOOP_OUT_QUOTE.request(amt=amount, conf_target=SWEEP_CONF_TARGET)
    try:
        quote = await litd_call(LOOP_OUT_QUOTE, request)
    except LitdError as exc:
        raise HTTPException(HTTPStatus.BAD_REQUEST, str(exc)) from exc
    except LitdUnreachableError as exc:
        raise HTTPException(HTTPStatus.BAD_GATEWAY, str(exc)) from exc

    swap_invoice = amount + quote.swap_fee_sat - quote.prepay_amt_sat
    max_swap_routing_fee = _max_routing_fee(amount)
    max_prepay_routing_fee = _max_routing_fee(quote.prepay_amt_sat)
    max_total = _sat_ceil(
        (swap_invoice + quote.prepay_amt_sat) * 1000
        + _reserve_msat(swap_invoice, max_swap_routing_fee)
        + _reserve_msat(quote.prepay_amt_sat, max_prepay_routing_fee)
    )
    return LoopOutQuote(
        amount=amount,
        swap_fee=quote.swap_fee_sat,
        prepay_amount=quote.prepay_amt_sat,
        miner_fee_estimate=quote.htlc_sweep_fee_sat,
        max_miner_fee=quote.htlc_sweep_fee_sat * MINER_FEE_MULTIPLIER,
        max_swap_routing_fee=max_swap_routing_fee,
        max_prepay_routing_fee=max_prepay_routing_fee,
        conf_target=quote.conf_target,
        server_pubkey=quote.swap_payment_dest.hex(),
        max_total=max_total,
    )


def _fake_bolt11(payment_hash: str, payment_secret: str, msat: int, memo: str):
    # Same as core's update_wallet_balance: a record that isn't a real invoice.
    invoice = Bolt11(
        currency="bc",
        amount_msat=MilliSatoshi(msat),
        date=int(time.time()),
        tags=Tags.from_dict(
            {
                "payment_hash": payment_hash,
                "payment_secret": payment_secret,
                "description": memo,
            }
        ),
    )
    return bolt11_encode(invoice, fake_privkey(settings.fake_wallet_secret))


def _expiry(bolt11: str) -> datetime:
    # Like core's pay_invoice. Without it the UI shows the Unix epoch.
    return bolt11_decode(bolt11).expiry_date


async def _check_wallet(wallet: Wallet, amount_msat: int) -> None:
    if settings.lnbits_only_allow_incoming_payments:
        raise HTTPException(HTTPStatus.FORBIDDEN, "Only incoming payments allowed.")
    if not wallet.can_send_payments:
        raise HTTPException(
            HTTPStatus.FORBIDDEN, "Wallet does not have permission to pay invoices."
        )
    access = await check_user_extension_access(wallet.user, EXT_ID)
    if not access.success:
        raise HTTPException(HTTPStatus.FORBIDDEN, access.message)
    try:
        await check_wallet_limits(wallet.source_wallet_id, amount_msat)
    except PaymentError as exc:
        raise HTTPException(HTTPStatus.BAD_REQUEST, exc.message) from exc
    except ValueError as exc:
        # The daily withdraw limit raises ValueError.
        raise HTTPException(HTTPStatus.BAD_REQUEST, str(exc)) from exc


async def _reserve(swap: LoopSwap, quote: LoopOutQuote) -> None:
    """Record the placeholder that reserves the worst case in the wallet."""
    # Take the lock core's pay_invoice uses for this wallet, so the balance
    # check and the insert can't interleave with another payment.
    async with payment_lock:
        if swap.wallet not in wallets_payments_lock:
            wallets_payments_lock[swap.wallet] = asyncio.Lock()

    async with wallets_payments_lock[swap.wallet]:
        wallet = await get_wallet(swap.wallet)
        if not wallet:
            raise HTTPException(HTTPStatus.NOT_FOUND, "Wallet not found.")
        if wallet.balance_msat < quote.max_total * 1000:
            raise HTTPException(
                HTTPStatus.BAD_REQUEST,
                f"Insufficient balance: this Loop Out needs up to "
                f"{quote.max_total} sat in the wallet.",
            )
        amount_msat = (quote.amount + quote.swap_fee) * 1000
        memo = f"Loop Out to {swap.address} (starting)"
        payment_secret, payment_hash = random_secret_and_hash()
        bolt11 = _fake_bolt11(payment_hash, payment_secret, amount_msat, memo)
        await create_payment(
            checking_id=swap.swap_checking_id,
            data=CreatePayment(
                wallet_id=swap.wallet,
                payment_hash=payment_hash,
                bolt11=bolt11,
                expiry=_expiry(bolt11),
                amount_msat=-amount_msat,
                fee=quote.max_total * 1000 - amount_msat,
                memo=memo,
                extension=EXT_ID,
                extra={"tag": EXT_ID, "swap_id": swap.id},
            ),
        )


async def _release_reservation(swap: LoopSwap, reason: str) -> None:
    placeholder = await get_standalone_payment(swap.swap_checking_id)
    if placeholder:
        placeholder.status = PaymentState.FAILED
        placeholder.memo = f"Loop Out to {swap.address} failed"
        await update_payment(placeholder)
    swap.status = "failed"
    swap.failure_reason = reason
    swap.done = True
    await update_swap(swap)


async def start_loop_out(wallet: Wallet, data: CreateLoopOut) -> LoopSwap:
    await _check_wallet(wallet, data.amount * 1000)
    quote = await get_quote(data.amount)
    if quote.max_total > data.max_total:
        raise HTTPException(
            HTTPStatus.CONFLICT,
            f"Fees went up: this Loop Out now needs up to {quote.max_total} sat. "
            "Please review the new quote.",
        )

    swap_id = uuid.uuid4().hex
    swap = LoopSwap(
        id=swap_id,
        wallet=wallet.source_wallet_id,
        amount=data.amount,
        address=data.address,
        server_pubkey=quote.server_pubkey,
        swap_fee=quote.swap_fee,
        prepay_amount=quote.prepay_amount,
        max_miner_fee=quote.max_miner_fee,
        max_swap_routing_fee=quote.max_swap_routing_fee,
        max_prepay_routing_fee=quote.max_prepay_routing_fee,
        swap_checking_id=f"internal_loop_{swap_id}",
    )
    request = LOOP_OUT.request(
        amt=data.amount,
        dest=data.address,
        max_swap_routing_fee=quote.max_swap_routing_fee,
        max_prepay_routing_fee=quote.max_prepay_routing_fee,
        max_swap_fee=quote.swap_fee,
        max_prepay_amt=quote.prepay_amount,
        max_miner_fee=quote.max_miner_fee,
        sweep_conf_target=SWEEP_CONF_TARGET,
        label=_label(swap_id),
        initiator="lnbits",
        is_external_addr=True,
    )

    async with _swap_lock:
        await _reserve(swap, quote)
        await create_swap(swap)
        # lnd's creation_date_start is in seconds; leave a little slack.
        started_at = int(time.time()) - 5
        try:
            response = await litd_call(LOOP_OUT, request, timeout=60)
        except LitdError as exc:
            await _release_reservation(swap, str(exc))
            raise HTTPException(HTTPStatus.BAD_REQUEST, str(exc)) from exc
        except LitdUnreachableError as exc:
            # The swap may have started. Keep the reservation; the background
            # task looks the swap up by label and settles it either way.
            logger.warning(f"Loop Out {swap.id}: no answer from LoopOut: {exc}")
            swap.status = "unknown"
            swap.failure_reason = str(exc)
            return await update_swap(swap)

        swap.swap_hash = response.id_bytes.hex()
        swap.htlc_address = response.htlc_address_p2tr or response.htlc_address_p2wsh
        swap.status = "pending"
        await update_swap(swap)
        await _record_payments(swap, started_at, match_prepay=True)
    return swap


async def _list_new_payments(since: int) -> list:
    request = lightning_pb2.ListPaymentsRequest(
        include_incomplete=True, creation_date_start=since, max_payments=1000
    )
    response = await litd_call(LND_LIST_PAYMENTS, request)
    return list(response.payments)


async def _is_prepay_candidate(swap: LoopSwap, payment) -> bool:
    if payment.payment_hash == swap.swap_hash:
        return False
    if payment.value_sat > swap.prepay_amount:
        return False
    # The destination is only known once lnd made an attempt.
    hops = [h.route.hops for h in payment.htlcs if h.route.hops]
    if not hops or hops[-1][-1].pub_key != swap.server_pubkey:
        return False
    # Payments LNbits knows about (its own, or other swaps' prepays) aren't
    # this swap's prepay. This matches on payment_hash too.
    return not await get_standalone_payment(payment.payment_hash)


async def _record_payments(swap: LoopSwap, since: int, match_prepay: bool) -> None:
    """
    Turn the placeholder into the two payments loopd made. Only call with
    _swap_lock held. match_prepay is only safe right after LoopOut, while no
    other swap can have started.
    """
    assert swap.swap_hash
    swap_payment = None
    prepays: list = []
    attempts = FIND_PAYMENTS_ATTEMPTS if match_prepay else 1
    for attempt in range(attempts):
        if attempt:
            await asyncio.sleep(FIND_PAYMENTS_INTERVAL)
        try:
            payments = await _list_new_payments(since)
        except (LitdError, LitdUnreachableError) as exc:
            logger.warning(f"Loop Out {swap.id}: could not list payments: {exc}")
            continue
        swap_payment = next(
            (p for p in payments if p.payment_hash == swap.swap_hash), None
        )
        if match_prepay:
            prepays = [p for p in payments if await _is_prepay_candidate(swap, p)]
        if swap_payment and len(prepays) == 1:
            break

    if not swap_payment:
        # Try again on the next background sync; the placeholder stays.
        logger.info(f"Loop Out {swap.id}: swap payment not in lnd yet")
        return

    placeholder = await get_standalone_payment(swap.swap_checking_id)
    if not placeholder:
        logger.error(f"Loop Out {swap.id}: placeholder payment missing")
        return

    # Record the prepay first: until the placeholder shrinks into the swap
    # payment, the wallet is reserved twice over, never too little.
    if not swap.prepay_checking_id:
        if len(prepays) != 1:
            logger.warning(
                f"Loop Out {swap.id}: {len(prepays)} prepay candidates, "
                "settling the prepay from Loop's swap costs instead"
            )
        await _record_prepay(swap, prepays[0] if len(prepays) == 1 else None)

    placeholder.bolt11 = swap_payment.payment_request
    placeholder.expiry = _expiry(swap_payment.payment_request)
    placeholder.payment_hash = swap.swap_hash
    placeholder.amount = -swap_payment.value_msat
    placeholder.fee = -_reserve_msat(
        swap_payment.value_sat, swap.max_swap_routing_fee
    )
    placeholder.memo = f"Loop Out to {swap.address}"
    await update_payment(placeholder)
    swap.payments_recorded = True
    await update_swap(swap)


async def _record_prepay(swap: LoopSwap, prepay) -> None:
    """Record the prepay: matched in lnd, or with a stand-in invoice."""
    memo = f"Loop Out prepay ({swap.address})"
    if prepay:
        payment_hash = prepay.payment_hash
        bolt11 = prepay.payment_request
        amount_msat = prepay.value_msat
    else:
        payment_secret, payment_hash = random_secret_and_hash()
        amount_msat = swap.prepay_amount * 1000
        bolt11 = _fake_bolt11(payment_hash, payment_secret, amount_msat, memo)

    swap.prepay_checking_id = f"internal_loopprepay_{swap.id}"
    swap.prepay_hash = prepay.payment_hash if prepay else None
    await create_payment(
        checking_id=swap.prepay_checking_id,
        data=CreatePayment(
            wallet_id=swap.wallet,
            payment_hash=payment_hash,
            bolt11=bolt11,
            expiry=_expiry(bolt11),
            amount_msat=-amount_msat,
            fee=_reserve_msat(amount_msat // 1000, swap.max_prepay_routing_fee),
            memo=memo,
            extension=EXT_ID,
            extra={"tag": EXT_ID, "swap_id": swap.id},
        ),
    )
    await update_swap(swap)


async def sync_swaps() -> None:
    """Background task: follow every swap that isn't done yet."""
    async with _swap_lock:
        for swap in await get_unfinished_swaps():
            try:
                await _sync_swap(swap)
            except (LitdError, LitdUnreachableError) as exc:
                logger.warning(f"Loop Out {swap.id}: sync failed: {exc}")


async def _sync_swap(swap: LoopSwap) -> None:
    if swap.status in ("starting", "unknown"):
        await _resolve_unknown(swap)
        return
    assert swap.swap_hash

    if not swap.payments_recorded:
        since = int(swap.created_at.timestamp()) - 5
        await _record_payments(swap, since, match_prepay=False)

    status = await litd_call(
        SWAP_INFO, SWAP_INFO.request(id=bytes.fromhex(swap.swap_hash))
    )
    swap.loop_state = _enum_name(status, "state")
    if status.failure_reason:
        swap.failure_reason = _enum_name(status, "failure_reason")
    swap.htlc_address = (
        status.htlc_address_p2tr or status.htlc_address_p2wsh or swap.htlc_address
    )
    swap.cost_server = status.cost_server
    swap.cost_onchain = status.cost_onchain
    swap.cost_offchain = status.cost_offchain
    final = swap.loop_state in ("SUCCESS", "FAILED")
    if swap.payments_recorded:
        swap.done = await _settle_payments(swap, final)
    if final:
        swap.status = swap.loop_state.lower()
    if final and not swap.payments_recorded:
        if swap.status == "failed" and not (swap.cost_server or swap.cost_offchain):
            # Failed before loopd paid anything.
            await _release_reservation(swap, swap.failure_reason or "FAILED")
            return
        logger.error(
            f"Loop Out {swap.id}: final, but its payments weren't found "
            "in lnd. The reservation stays until an admin settles it."
        )
    await update_swap(swap)


def _enum_name(message, field: str) -> str:
    enum = message.DESCRIPTOR.fields_by_name[field].enum_type
    return enum.values_by_number[getattr(message, field)].name


async def _resolve_unknown(swap: LoopSwap) -> None:
    """Find out whether a LoopOut without an answer started a swap."""
    request = LIST_SWAPS.request()
    request.list_swap_filter.label = _label(swap.id)
    response = await litd_call(LIST_SWAPS, request)
    found = next((s for s in response.swaps if s.label == _label(swap.id)), None)
    if found:
        logger.info(f"Loop Out {swap.id}: found in loopd")
        swap.swap_hash = found.id_bytes.hex()
        swap.status = "pending"
        swap.failure_reason = None
        await update_swap(swap)
        # Other swaps may have started since, so don't guess the prepay.
        since = int(swap.created_at.timestamp()) - 5
        await _record_payments(swap, since, match_prepay=False)
        return

    if time.time() - swap.created_at.timestamp() > UNKNOWN_SWAP_TIMEOUT:
        logger.info(f"Loop Out {swap.id}: not in loopd, releasing reservation")
        await _release_reservation(swap, "Loop Out was not started.")


async def _settle_payments(swap: LoopSwap, swap_final: bool) -> bool:
    """
    Settle both payments from lnd. Successes are settled as soon as lnd has
    them: the preimage is out, nothing can undo them. Failures only once the
    swap is final, because until then loopd may retry a payment under the same
    hash. True once the swap is final and both payments are.
    """
    assert swap.swap_hash and swap.prepay_checking_id
    swap_payment = await get_standalone_payment(swap.swap_checking_id)
    prepay = await get_standalone_payment(swap.prepay_checking_id)
    if not swap_payment or not prepay:
        logger.error(f"Loop Out {swap.id}: payment records missing")
        return False

    swap_payment = await _settle_from_lnd(swap_payment, swap.swap_hash, swap_final)
    if swap.prepay_hash:
        prepay = await _settle_from_lnd(prepay, swap.prepay_hash, swap_final)
    elif swap_final:
        # Needs Loop's final costs.
        prepay = await _settle_from_costs(swap, swap_payment, prepay)
    return swap_final and not swap_payment.pending and not prepay.pending


async def _settle_from_lnd(
    payment: Payment, payment_hash: str, accept_failure: bool
) -> Payment:
    if not payment.pending:
        return payment
    status = await get_funding_source().get_payment_status(payment_hash)
    if status.success:
        payment.preimage = payment.preimage or status.preimage
        return await _mark_success(payment, abs(status.fee_msat or 0))
    if status.failed and accept_failure:
        payment.status = PaymentState.FAILED
        return await update_payment(payment)
    return payment


async def _settle_from_costs(
    swap: LoopSwap, swap_payment: Payment, prepay: Payment
) -> Payment:
    """Settle an unmatched prepay from Loop's final swap costs."""
    if swap_payment.pending or not prepay.pending:
        return prepay
    if swap_payment.success:
        # cost_server = swap invoice + prepay - amount
        prepay_sat = swap.cost_server + swap.amount - abs(swap_payment.amount) // 1000
        swap_routing_msat = abs(swap_payment.fee) - await _charged_service_fee(
            swap_payment
        )
        routing_sat = max(0, swap.cost_offchain - swap_routing_msat // 1000)
    else:
        # A failed swap only costs the prepay, if the server took it.
        prepay_sat = swap.cost_server
        routing_sat = max(0, swap.cost_offchain)

    if prepay_sat <= 0:
        prepay.status = PaymentState.FAILED
        return await update_payment(prepay)
    prepay.amount = -prepay_sat * 1000
    return await _mark_success(prepay, routing_sat * 1000)


def _service_fee_checking_id(payment: Payment) -> str:
    # Same checking_id core uses for its service fee records.
    return f"service_fee_{payment.payment_hash}"


async def _charged_service_fee(payment: Payment) -> int:
    record = await get_standalone_payment(_service_fee_checking_id(payment))
    return record.amount if record else 0


async def _mark_success(payment: Payment, routing_fee_msat: int) -> Payment:
    """
    Mark a payment successful, charging the routing fee plus LNbits' service
    fee, and credit that same service fee to the service fee wallet.

    Core's _credit_service_fee_wallet isn't used: it computes the fee as for an
    internal payment (these use internal_ checking_ids), and credits when the
    payment is created rather than when it succeeds.
    """
    service_fee_msat = service_fee(payment.amount)
    if service_fee_msat:
        await _credit_service_fee(payment, service_fee_msat)
    payment.status = PaymentState.SUCCESS
    payment.fee = -(routing_fee_msat + service_fee_msat)
    return await update_payment(payment)


async def _credit_service_fee(payment: Payment, service_fee_msat: int) -> None:
    # Credited before the payment is marked successful; if that update is
    # lost, the next sync finds this record and doesn't credit twice.
    checking_id = _service_fee_checking_id(payment)
    if await get_standalone_payment(checking_id):
        return
    wallet = await get_wallet(payment.wallet_id)
    wallet_name = wallet.name if wallet else ""
    await create_payment(
        checking_id=checking_id,
        data=CreatePayment(
            wallet_id=settings.lnbits_service_fee_wallet,
            bolt11=payment.bolt11,
            expiry=payment.expiry,
            payment_hash=payment.payment_hash,
            amount_msat=service_fee_msat,
            memo=(
                f"Service fee for payment of {abs(payment.sat)} sats. "
                f"Wallet: '{wallet_name}' ({payment.wallet_id})."
            ),
            extension=EXT_ID,
        ),
        status=PaymentState.SUCCESS,
    )
