from http import HTTPStatus

import httpx
from fastapi import HTTPException
from lnbits.helpers import normalize_endpoint
from lnbits.settings import settings
from lnbits.wallets.macaroon import load_macaroon


def _litd_connection() -> tuple[str, str | bool, str]:
    """
    Reuse the LND funding source settings LNbits loaded from its .env
    (or the admin UI). litd serves lnd and Loop, REST and gRPC, on one port,
    so the same endpoint works for Loop as long as the macaroon is a
    super macaroon.
    """
    backend = settings.lnbits_backend_wallet_class
    if backend == "LndRestWallet":
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

    # No cert means a publicly issued certificate, same as LndRestWallet.
    return normalize_endpoint(endpoint), cert or True, macaroon_hex


async def loop_get(path: str) -> dict:
    endpoint, cert, macaroon = _litd_connection()
    headers = {"Grpc-Metadata-macaroon": macaroon, "User-Agent": settings.user_agent}
    try:
        async with httpx.AsyncClient(
            base_url=endpoint, headers=headers, verify=cert, timeout=15
        ) as client:
            r = await client.get(path)
    except httpx.HTTPError as exc:
        raise HTTPException(
            HTTPStatus.BAD_GATEWAY, f"Unable to connect to litd at {endpoint}."
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
