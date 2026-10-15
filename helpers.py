import base64
from collections.abc import Callable
from urllib.parse import quote
from http import HTTPStatus
from pathlib import Path
from typing import Any, NamedTuple

import grpc
import httpx
from fastapi import HTTPException
from google.protobuf import descriptor_pb2, descriptor_pool, json_format
from google.protobuf.message import Message
from google.protobuf.message_factory import GetMessageClass
from lnbits.helpers import normalize_endpoint
from lnbits.settings import settings
from lnbits.wallets.lnd_grpc_files import lightning_pb2
from lnbits.wallets.macaroon import load_macaroon

# Load the Loop messages into a private pool, so they can't clash with the
# protos LNbits core registers in the default one.
_loop_pool = descriptor_pool.DescriptorPool()
for _file in descriptor_pb2.FileDescriptorSet.FromString(
    Path(__file__).parent.joinpath("loop_grpc_files", "loop.desc").read_bytes()
).file:
    _loop_pool.Add(_file)


def loop_message(name: str) -> Any:
    return GetMessageClass(_loop_pool.FindMessageTypeByName(f"looprpc.{name}"))


class Rpc(NamedTuple):
    grpc_path: str
    request: Any
    response: Any
    rest_method: str
    # Builds the REST path (with query string) from the request.
    rest_path: Callable[[Any], str]


def _loop_rpc(
    method: str, request: str, response: str, rest_method: str, rest_path
) -> Rpc:
    return Rpc(
        f"/looprpc.SwapClient/{method}",
        loop_message(request),
        loop_message(response),
        rest_method,
        rest_path if callable(rest_path) else lambda _: rest_path,
    )


GET_INFO = _loop_rpc(
    "GetInfo", "GetInfoRequest", "GetInfoResponse", "GET", "/v1/loop/info"
)
LOOP_OUT_TERMS = _loop_rpc(
    "LoopOutTerms", "TermsRequest", "OutTermsResponse", "GET", "/v1/loop/out/terms"
)
LOOP_IN_TERMS = _loop_rpc(
    "GetLoopInTerms", "TermsRequest", "InTermsResponse", "GET", "/v1/loop/in/terms"
)
LOOP_OUT_QUOTE = _loop_rpc(
    "LoopOutQuote",
    "QuoteRequest",
    "OutQuoteResponse",
    "GET",
    lambda r: f"/v1/loop/out/quote/{r.amt}?conf_target={r.conf_target}",
)
LOOP_OUT = _loop_rpc("LoopOut", "LoopOutRequest", "SwapResponse", "POST", "/v1/loop/out")
LIST_SWAPS = _loop_rpc(
    "ListSwaps",
    "ListSwapsRequest",
    "ListSwapsResponse",
    "GET",
    lambda r: (
        "/v1/loop/swaps?list_swap_filter.label="
        f"{quote(r.list_swap_filter.label)}"
    ),
)
SWAP_INFO = _loop_rpc(
    "SwapInfo",
    "SwapInfoRequest",
    "SwapStatus",
    "GET",
    # REST needs the swap id as URL safe base64.
    lambda r: f"/v1/loop/swap/{base64.urlsafe_b64encode(r.id).decode()}",
)
LND_LIST_PAYMENTS = Rpc(
    "/lnrpc.Lightning/ListPayments",
    lightning_pb2.ListPaymentsRequest,
    lightning_pb2.ListPaymentsResponse,
    "GET",
    lambda r: (
        f"/v1/payments?include_incomplete={str(r.include_incomplete).lower()}"
        f"&creation_date_start={r.creation_date_start}"
        f"&max_payments={r.max_payments}"
    ),
)


# gRPC codes that mean litd's answer never reached us, so a request that
# changes state may or may not have been applied. Plain loopd errors arrive as
# UNKNOWN; those are real answers. litd also returns UNAVAILABLE itself when it
# can't reach Loop, which is safe but can't be told apart.
_AMBIGUOUS_CODES = (
    grpc.StatusCode.UNAVAILABLE,
    grpc.StatusCode.DEADLINE_EXCEEDED,
    grpc.StatusCode.CANCELLED,
)


class LitdError(Exception):
    """litd answered with an error, so the request definitely wasn't applied."""


class LitdUnreachableError(Exception):
    """No answer from litd. A request that changes state may or may not have
    been applied."""


class LitdConnection(NamedTuple):
    use_grpc: bool
    endpoint: str
    cert: str | None
    macaroon: str


def _litd_connection() -> LitdConnection:
    """
    Reuse the LND funding source settings LNbits loaded from its .env
    (or the admin UI), over the same protocol LNbits uses. litd serves lnd
    and Loop on one port, so the same endpoint works for Loop as long as the
    macaroon is a super macaroon.
    """
    backend = settings.lnbits_backend_wallet_class
    if backend == "LndRestWallet":
        use_grpc = False
        endpoint = settings.lnd_rest_endpoint
        cert = settings.lnd_rest_cert
        macaroon = (
            settings.lnd_rest_macaroon
            or settings.lnd_admin_macaroon
            or settings.lnd_rest_admin_macaroon
            or settings.lnd_invoice_macaroon
            or settings.lnd_rest_invoice_macaroon
        )
        encrypted = settings.lnd_rest_macaroon_encrypted
    elif backend == "LndWallet":
        use_grpc = True
        endpoint = (
            f"{settings.lnd_grpc_endpoint}:{settings.lnd_grpc_port}"
            if settings.lnd_grpc_endpoint and settings.lnd_grpc_port
            else None
        )
        cert = settings.lnd_grpc_cert or settings.lnd_cert
        macaroon = (
            settings.lnd_grpc_macaroon
            or settings.lnd_grpc_admin_macaroon
            or settings.lnd_grpc_invoice_macaroon
        )
        encrypted = settings.lnd_grpc_macaroon_encrypted
    else:
        raise HTTPException(
            HTTPStatus.BAD_REQUEST,
            f"Funding source '{backend}' is not supported. "
            "Use LndRestWallet or LndWallet pointed at litd.",
        )

    if not endpoint:
        raise HTTPException(HTTPStatus.BAD_REQUEST, "litd endpoint is not set.")
    if use_grpc and not cert:
        raise HTTPException(HTTPStatus.BAD_REQUEST, "litd TLS cert is not set.")
    if encrypted:
        # load_macaroon would prompt for the key on the server's stdin.
        raise HTTPException(
            HTTPStatus.BAD_REQUEST, "Encrypted macaroons are not supported."
        )
    try:
        macaroon_hex = load_macaroon(macaroon)
    except (ValueError, OSError) as exc:
        raise HTTPException(
            HTTPStatus.BAD_REQUEST, "Could not load the macaroon."
        ) from exc

    endpoint = normalize_endpoint(endpoint, add_proto=not use_grpc)
    return LitdConnection(use_grpc, endpoint, cert, macaroon_hex)


async def litd_call(rpc: Rpc, request: Message, timeout: float = 15) -> Any:
    """
    Call an RPC on litd over the same protocol LNbits uses and return the
    response message. Raises LitdError or LitdUnreachableError.
    """
    conn = _litd_connection()
    if conn.use_grpc:
        return await _grpc_call(conn, rpc, request, timeout)
    return await _rest_call(conn, rpc, request, timeout)


async def litd_json(rpc: Rpc, request: Message | None = None) -> dict:
    """Like litd_call, but returns JSON for the admin views."""
    try:
        response = await litd_call(rpc, request or rpc.request())
    except (LitdError, LitdUnreachableError) as exc:
        raise HTTPException(HTTPStatus.BAD_GATEWAY, str(exc)) from exc
    return message_to_dict(response)


def message_to_dict(message: Message) -> dict:
    # Same JSON shape as litd's REST gateway (proto field names, defaults shown).
    return json_format.MessageToDict(
        message,
        preserving_proto_field_name=True,
        always_print_fields_with_no_presence=True,
    )


async def _rest_call(
    conn: LitdConnection, rpc: Rpc, request: Message, timeout: float
) -> Any:
    headers = {
        "Grpc-Metadata-macaroon": conn.macaroon,
        "User-Agent": settings.user_agent,
    }
    try:
        # No cert means a publicly issued certificate, same as LndRestWallet.
        async with httpx.AsyncClient(
            base_url=conn.endpoint,
            headers=headers,
            verify=conn.cert or True,
            timeout=timeout,
        ) as client:
            r = await client.request(
                rpc.rest_method,
                rpc.rest_path(request),
                json=(
                    json_format.MessageToDict(
                        request, preserving_proto_field_name=True
                    )
                    if rpc.rest_method == "POST"
                    else None
                ),
            )
    except httpx.HTTPError as exc:
        raise LitdUnreachableError(
            f"Unable to connect to litd at {conn.endpoint}."
        ) from exc

    try:
        data = r.json()
    except ValueError as exc:
        raise LitdUnreachableError("litd returned an invalid response.") from exc
    if r.is_error:
        message = data.get("message") if isinstance(data, dict) else None
        code = data.get("code") if isinstance(data, dict) else None
        if code in [c.value[0] for c in _AMBIGUOUS_CODES]:
            raise LitdUnreachableError(f"litd: {message or r.text}")
        raise LitdError(f"litd: {message or r.text}")
    return json_format.ParseDict(data, rpc.response(), ignore_unknown_fields=True)


async def _grpc_call(
    conn: LitdConnection, rpc: Rpc, request: Message, timeout: float
) -> Any:
    assert conn.cert
    with open(conn.cert, "rb") as f:
        cert = f.read()

    def metadata_callback(_, callback):
        callback([("macaroon", conn.macaroon)], None)

    creds = grpc.composite_channel_credentials(
        grpc.ssl_channel_credentials(cert),
        grpc.metadata_call_credentials(metadata_callback),  # type: ignore[arg-type]
    )
    try:
        async with grpc.aio.secure_channel(conn.endpoint, creds) as channel:
            call = channel.unary_unary(
                rpc.grpc_path,
                request_serializer=rpc.request.SerializeToString,
                response_deserializer=rpc.response.FromString,
            )
            return await call(request, timeout=timeout)
    except grpc.aio.AioRpcError as exc:
        message = f"litd at {conn.endpoint}: {exc.code().name}: {exc.details()}"
        if exc.code() in _AMBIGUOUS_CODES:
            raise LitdUnreachableError(message) from exc
        raise LitdError(message) from exc
