from http import HTTPStatus
from pathlib import Path
from typing import NamedTuple

import grpc
import httpx
from fastapi import HTTPException
from google.protobuf import descriptor_pb2, descriptor_pool, json_format
from google.protobuf.message_factory import GetMessageClass
from lnbits.helpers import normalize_endpoint
from lnbits.settings import settings
from lnbits.wallets.macaroon import load_macaroon

# Loop RPCs this extension calls: gRPC method -> (REST path, request, response).
_LOOP_METHODS = {
    "GetInfo": ("/v1/loop/info", "GetInfoRequest", "GetInfoResponse"),
    "LoopOutTerms": ("/v1/loop/out/terms", "TermsRequest", "OutTermsResponse"),
    "GetLoopInTerms": ("/v1/loop/in/terms", "TermsRequest", "InTermsResponse"),
}

# Load the Loop messages into a private pool, so they can't clash with the
# protos LNbits core registers in the default one.
_loop_pool = descriptor_pool.DescriptorPool()
for _file in descriptor_pb2.FileDescriptorSet.FromString(
    Path(__file__).parent.joinpath("loop_grpc_files", "loop.desc").read_bytes()
).file:
    _loop_pool.Add(_file)


def _loop_message(name: str):
    return GetMessageClass(_loop_pool.FindMessageTypeByName(f"looprpc.{name}"))


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


async def loop_call(method: str) -> dict:
    """Call a Loop RPC through litd and return the response as JSON."""
    conn = _litd_connection()
    if conn.use_grpc:
        return await _loop_grpc_call(conn, method)
    return await _loop_rest_call(conn, method)


async def _loop_rest_call(conn: LitdConnection, method: str) -> dict:
    path = _LOOP_METHODS[method][0]
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
            timeout=15,
        ) as client:
            r = await client.get(path)
    except httpx.HTTPError as exc:
        raise HTTPException(
            HTTPStatus.BAD_GATEWAY, f"Unable to connect to litd at {conn.endpoint}."
        ) from exc

    try:
        data = r.json()
    except ValueError as exc:
        raise HTTPException(
            HTTPStatus.BAD_GATEWAY, "litd returned an invalid response."
        ) from exc
    if r.is_error:
        message = data.get("message") if isinstance(data, dict) else None
        raise HTTPException(HTTPStatus.BAD_GATEWAY, f"litd: {message or r.text}")
    return data


async def _loop_grpc_call(conn: LitdConnection, method: str) -> dict:
    _, request_name, response_name = _LOOP_METHODS[method]
    request_cls = _loop_message(request_name)
    response_cls = _loop_message(response_name)

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
            rpc = channel.unary_unary(
                f"/looprpc.SwapClient/{method}",
                request_serializer=request_cls.SerializeToString,
                response_deserializer=response_cls.FromString,
            )
            response = await rpc(request_cls(), timeout=15)
    except grpc.aio.AioRpcError as exc:
        # Keep litd's details even for UNAVAILABLE: litd itself returns that
        # code when it can't reach Loop, e.g. a remote loopd that isn't up.
        raise HTTPException(
            HTTPStatus.BAD_GATEWAY,
            f"litd at {conn.endpoint}: {exc.code().name}: {exc.details()}",
        ) from exc

    # Same JSON shape as litd's REST gateway (proto field names, defaults shown).
    return json_format.MessageToDict(
        response,
        preserving_proto_field_name=True,
        always_print_fields_with_no_presence=True,
    )
