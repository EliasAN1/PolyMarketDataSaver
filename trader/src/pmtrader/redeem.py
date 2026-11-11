"""Gasless CTF redeem via the Polymarket builder relayer (deposit wallet)."""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx
from eth_abi import encode as eth_encode
from eth_utils import keccak

from pmtrader.config import env

logger = logging.getLogger(__name__)

RELAYER_URL = "https://relayer-v2.polymarket.com/"
POLYGON_RPC = "https://polygon-rpc.com"

PUSD = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"
CTF = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
CTF_ADAPTER = "0xAdA100Db00Ca00073811820692005400218FcE1f"
NEG_RISK_ADAPTER = "0xadA2005600Dec949baf300f4C6120000bDB6eAab"

REDEEM_SELECTOR = keccak(text="redeemPositions(address,bytes32,bytes32,uint256[])")[:4]
APPROVE_SELECTOR = keccak(text="setApprovalForAll(address,bool)")[:4]
IS_APPROVED_SELECTOR = keccak(text="isApprovedForAll(address,address)")[:4]

BATCH_SIZE = 25
DEADLINE_SECS = 300


def builder_creds() -> tuple[str, str, str] | None:
    key = env("POLYMARKET_BUILDER_API_KEY") or env("BUILDER_KEY") or env("BUILDER_API_KEY")
    secret = env("POLYMARKET_BUILDER_SECRET") or env("BUILDER_SECRET")
    phrase = env("POLYMARKET_BUILDER_PASSPHRASE") or env("BUILDER_PASSPHRASE") or env("BUILDER_PASS_PHRASE")
    if key and secret and phrase:
        return key, secret, phrase
    return None


def _pad32(value: bytes) -> bytes:
    return value.rjust(32, b"\x00")


def _addr_word(address: str) -> bytes:
    raw = address.lower().replace("0x", "")
    return _pad32(bytes.fromhex(raw))


def redeem_calldata(condition_id: str) -> str:
    cid = condition_id.strip()
    if cid.startswith("0x"):
        cid = cid[2:]
    condition = bytes.fromhex(cid)
    if len(condition) != 32:
        raise ValueError(f"condition_id must be 32 bytes, got {len(condition)}")
    encoded = eth_encode(
        ["address", "bytes32", "bytes32", "uint256[]"],
        [PUSD, b"\x00" * 32, condition, [1, 2]],
    )
    return "0x" + (REDEEM_SELECTOR + encoded).hex()


def approve_calldata(operator: str) -> str:
    encoded = eth_encode(["address", "bool"], [operator, True])
    return "0x" + (APPROVE_SELECTOR + encoded).hex()


def adapter_for(negative_risk: bool) -> str:
    return NEG_RISK_ADAPTER if negative_risk else CTF_ADAPTER


def is_approved(owner: str, operator: str) -> bool:
    data = "0x" + (IS_APPROVED_SELECTOR + _addr_word(owner) + _addr_word(operator)).hex()
    rpc = env("POLYGON_RPC_URL") or POLYGON_RPC
    try:
        with httpx.Client(timeout=10.0) as http:
            response = http.post(
                rpc,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "eth_call",
                    "params": [{"to": CTF, "data": data}, "latest"],
                },
            )
            response.raise_for_status()
            result = response.json().get("result") or "0x0"
            return int(result, 16) == 1
    except Exception as exc:
        logger.warning("isApprovedForAll failed (%s); will send approval", exc)
        return False


def _nonce_value(raw: Any) -> str:
    if isinstance(raw, dict):
        nonce = raw.get("nonce", raw)
    else:
        nonce = raw
    if isinstance(nonce, dict):
        nonce = nonce.get("nonce", 0)
    return str(int(nonce))


def _tx_hash(result: Any) -> str | None:
    if result is None:
        return None
    if isinstance(result, dict):
        return result.get("transactionHash") or result.get("transaction_hash") or result.get("hash")
    return getattr(result, "transaction_hash", None) or getattr(result, "hash", None)


class DepositRelayer:
    def __init__(self) -> None:
        creds = builder_creds()
        private_key = env("POLYMARKET_PRIVATE_KEY")
        funder = env("POLYMARKET_WALLET_ADDRESS") or env("POLYMARKET_FUNDER")
        if not creds:
            raise RuntimeError(
                "Redeem needs POLYMARKET_BUILDER_API_KEY / SECRET / PASSPHRASE "
                "(Builder keys from polymarket.com/settings)"
            )
        if not private_key:
            raise RuntimeError("Redeem needs POLYMARKET_PRIVATE_KEY")
        if not funder:
            raise RuntimeError("Redeem needs POLYMARKET_FUNDER")

        from py_builder_relayer_client.client import RelayClient
        from py_builder_relayer_client.models import RelayerTxType, TransactionType
        from py_builder_signing_sdk.config import BuilderApiKeyCreds, BuilderConfig

        key, secret, phrase = creds
        self._tx_type = TransactionType
        self.funder = funder
        self.client = RelayClient(
            RELAYER_URL,
            137,
            private_key,
            BuilderConfig(
                local_builder_creds=BuilderApiKeyCreds(key=key, secret=secret, passphrase=phrase)
            ),
            RelayerTxType.SAFE,
        )

    def redeem_batch(self, conditions: list[tuple[str, bool]]) -> str:
        from py_builder_relayer_client.models import DepositWalletCall

        calls: list[DepositWalletCall] = []
        approved: set[str] = set()
        for condition_id, negative_risk in conditions:
            adapter = adapter_for(negative_risk)
            if adapter not in approved and not is_approved(self.funder, adapter):
                calls.append(
                    DepositWalletCall(target=CTF, value="0", data=approve_calldata(adapter))
                )
                approved.add(adapter)
            calls.append(
                DepositWalletCall(
                    target=adapter,
                    value="0",
                    data=redeem_calldata(condition_id),
                )
            )

        if not calls:
            raise RuntimeError("empty redeem batch")

        signer = self.client.signer.address()
        nonce_raw = self.client.get_nonce(signer, self._tx_type.WALLET.value)
        nonce = _nonce_value(nonce_raw)
        deadline = str(int(time.time()) + DEADLINE_SECS)
        last_err = "relayer redeem failed"
        for attempt in range(3):
            try:
                response = self.client.execute_deposit_wallet_batch(
                    calls, self.funder, nonce, deadline
                )
                result = response.wait()
                tx_hash = _tx_hash(result) or getattr(response, "transaction_hash", None)
                if result is None and not tx_hash:
                    raise RuntimeError("relayer redeem failed on-chain")
                return str(tx_hash or "relayer-confirmed")
            except Exception as exc:
                last_err = str(exc)
                if "deadline too soon" in last_err.lower() and attempt < 2:
                    deadline = str(int(time.time()) + DEADLINE_SECS)
                    time.sleep(0.5)
                    continue
                raise RuntimeError(last_err) from exc
        raise RuntimeError(last_err)
