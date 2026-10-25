#!/usr/bin/env python3
"""
Receipt Relay Q46 (FlapJax) — FLAPJAX holder snapshot at the last block of 2026-10-23 COT (UTC-5).

Method: from the chain alone. Every ERC-20/BEP-20 balance originates from a Transfer
event, so the balance of any address at a block equals the sum of transfers into it
minus transfers out of it, over all logs up to that block. This script enumerates the
token's full Transfer log history from its deployment block to the target block on a
public BSC node, derives balances, and cross-checks the sum against totalSupply()
read at the same block.

No indexer, no explorer API, no third-party holder list.

Usage:
    export BSC_RPC_URL=https://<public-bsc-node>
    python3 q46_holders.py [--target-block N] [--out data/]
"""

import argparse
import json
import os
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

TOKEN = "0x90c8889f428F9Ebb77BB8f15CAD3a50a9aC680df"
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
ZERO = "0x0000000000000000000000000000000000000000"

# Last block of 2026-10-23 in COT (UTC-5) == 2026-10-24T05:00:00Z.
DEFAULT_TARGET_BLOCK = 129454096
DEFAULT_DEPLOY_BLOCK = 44582615

CHUNK = 50_000          # widest eth_getLogs range accepted by the node used
WORKERS = 6
RETRIES = 12

RPC = os.environ.get("BSC_RPC_URL")
if not RPC:
    sys.exit("set BSC_RPC_URL to a public BSC JSON-RPC endpoint")

# --- adaptive client-side throttle -------------------------------------------------
# Public nodes 429 under sustained load; pace requests and back off on rejection.
_throttle_lock = threading.Lock()
_next_slot = [0.0]
_rate = [6.0]            # requests per second, adapts between 1.5 and 12


def _pace():
    with _throttle_lock:
        now = time.monotonic()
        wait = max(0.0, _next_slot[0] - now)
        _next_slot[0] = max(now, _next_slot[0]) + 1.0 / _rate[0]
    if wait > 0:
        time.sleep(wait)


def _slow_down():
    with _throttle_lock:
        _rate[0] = max(1.5, _rate[0] * 0.75)


def _speed_up():
    with _throttle_lock:
        _rate[0] = min(12.0, _rate[0] * 1.15)


def rpc(method, params, timeout=60):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    _pace()
    req = urllib.request.Request(
        RPC, data=body,
        headers={"Content-Type": "application/json", "User-Agent": "Hermes-Agent/1.0"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read())
    if "result" not in d:
        raise RuntimeError(f"{method}: {d.get('error')}")
    return d["result"]


def rpc_retry(method, params, tries=8, timeout=60):
    last = None
    for attempt in range(tries):
        try:
            return rpc(method, params, timeout)
        except Exception as e:  # noqa: BLE001
            last = e
            _slow_down()
            time.sleep(min(10.0, 1.0 * (attempt + 1)))
    raise RuntimeError(f"{method} failed after {tries} tries: {last}")


def get_logs_chunk(args):
    lo, hi, cache_dir = args
    cache = os.path.join(cache_dir, f"{lo}_{hi}.json")
    if os.path.exists(cache):
        with open(cache) as fh:
            return json.load(fh)
    last = None
    for attempt in range(RETRIES):
        try:
            res = rpc("eth_getLogs", [{
                "address": TOKEN,
                "topics": [TRANSFER_TOPIC],
                "fromBlock": hex(lo),
                "toBlock": hex(hi),
            }])
            out = [{
                "from": "0x" + l["topics"][1][-40:],
                "to": "0x" + l["topics"][2][-40:],
                "value": int(l["data"], 16),
                "block": int(l["blockNumber"], 16),
                "tx": l["transactionHash"],
                "logIndex": int(l["logIndex"], 16),
            } for l in res]
            _speed_up()
            with open(cache, "w") as fh:
                json.dump(out, fh)
            return out
        except Exception as e:  # noqa: BLE001 - transport errors are retried
            last = e
            _slow_down()
            time.sleep(min(8.0, 0.8 * (attempt + 1)) * (1 + 0.25 * (attempt % 3)))
    raise RuntimeError(f"chunk {lo}-{hi} failed after {RETRIES} tries: {last}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target-block", type=int, default=DEFAULT_TARGET_BLOCK)
    ap.add_argument("--deploy-block", type=int, default=DEFAULT_DEPLOY_BLOCK)
    ap.add_argument("--out", default="data")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    os.makedirs(os.path.join(args.out, "chunks"), exist_ok=True)
    t0 = time.time()

    blk = rpc_retry("eth_getBlockByNumber", [hex(args.target_block), False])
    target_hash = blk["hash"]
    target_ts = int(blk["timestamp"], 16)

    def token_call(data):
        return rpc_retry("eth_call", [{"to": TOKEN, "data": data}, hex(args.target_block)])

    decimals = int(token_call("0x313ce567"), 16)
    # ABI-encoded string: 32-byte offset, 32-byte length, then the bytes.
    raw_sym = token_call("0x95d89b41")[2:]
    off = int(raw_sym[0:64], 16) * 2
    ln = int(raw_sym[off:off + 64], 16) * 2
    symbol = bytes.fromhex(raw_sym[off + 64:off + 64 + ln]).decode(errors="replace")
    supply = int(token_call("0x18160ddd"), 16)

    jobs = [(b, min(b + CHUNK - 1, args.target_block), os.path.join(args.out, "chunks"))
            for b in range(args.deploy_block, args.target_block + 1, CHUNK)]
    print(f"scanning {len(jobs)} chunks over blocks {args.deploy_block}..{args.target_block}", flush=True)

    all_logs = []
    pending = list(jobs)
    for p in range(4):
        if not pending:
            break
        failed = []
        done = 0
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            futs = {ex.submit(get_logs_chunk, j): j for j in pending}
            for fut in as_completed(futs):
                j = futs[fut]
                try:
                    all_logs.extend(fut.result())
                    done += 1
                    if done % 100 == 0:
                        print(f"  pass {p+1}: {done}/{len(pending)} chunks, {len(all_logs)} transfers, "
                              f"rate={_rate[0]:.1f}/s, {time.time()-t0:.0f}s", flush=True)
                except Exception as e:  # noqa: BLE001 - isolate chunk failures
                    failed.append(j)
                    print(f"  pass {p+1}: chunk {j[0]}-{j[1]} failed: {str(e)[:90]}", flush=True)
        pending = failed
        if pending:
            print(f"  pass {p+1}: {len(pending)} chunks to retry, sleeping 10s", flush=True)
            time.sleep(10)
    if pending:
        raise SystemExit(f"scan incomplete: {len(pending)} chunks unresolved after 4 passes")

    all_logs.sort(key=lambda l: (l["block"], l["logIndex"]))
    print(f"done: {len(all_logs)} Transfer logs in {time.time()-t0:.1f}s", flush=True)

    bal, minted, burned = {}, 0, 0
    for l in all_logs:
        f, t, v = l["from"].lower(), l["to"].lower(), l["value"]
        if f == ZERO:
            minted += v
        else:
            bal[f] = bal.get(f, 0) - v
        if t == ZERO:
            burned += v
        else:
            bal[t] = bal.get(t, 0) + v

    holders = {a: b for a, b in bal.items() if b > 0}
    top = sorted(holders.items(), key=lambda kv: kv[1], reverse=True)[:20]

    code_cache = {}
    def has_code(addr):
        if addr not in code_cache:
            code = rpc_retry("eth_getCode", [addr, hex(args.target_block)])
            code_cache[addr] = code if code and code != "0x" else "0x"
        return code_cache[addr]

    rows = []
    for rank, (addr, balance) in enumerate(top, 1):
        code = has_code(addr)
        delegated = code.lower().startswith("0xef0100")
        rows.append({
            "rank": rank,
            "address": addr,
            "balance": str(balance),
            "balance_tokens": f"{balance / 10**decimals:.6f}",
            "share_pct": round(100 * balance / supply, 6),
            "code_len": len(code) // 2 - 1 if code != "0x" else 0,
            "kind": "eoa" if code == "0x" else ("eoa-delegated" if delegated else "contract"),
        })

    def sh(k):
        return round(100 * sum(b for _, b in top[:k]) / supply, 6)

    result = {
        "token": TOKEN,
        "symbol": symbol,
        "decimals": decimals,
        "chain": "BNB Smart Chain (56)",
        "target_block": args.target_block,
        "target_block_hash": target_hash,
        "target_block_time_utc": datetime.fromtimestamp(target_ts, timezone.utc).isoformat(),
        "deploy_block": args.deploy_block,
        "total_supply_at_block": str(supply),
        "total_supply_tokens": f"{supply / 10**decimals:.6f}",
        "transfer_logs_scanned": len(all_logs),
        "minted_total": str(minted),
        "burned_to_zero": str(burned),
        "holder_count_nonzero": len(holders),
        "sum_of_balances": str(sum(holders.values())),
        "sum_equals_supply": sum(holders.values()) == supply,
        "top20": rows,
        "concentration": {
            "top5_pct": sh(5), "top10_pct": sh(10), "top20_pct": sh(20),
            "top5_tokens": f"{sum(b for _, b in top[:5]) / 10**decimals:.6f}",
            "top10_tokens": f"{sum(b for _, b in top[:10]) / 10**decimals:.6f}",
            "top20_tokens": f"{sum(b for _, b in top[:20]) / 10**decimals:.6f}",
        },
        "top20_kinds": {
            "contracts": sum(1 for r in rows if r["kind"] == "contract"),
            "eoa": sum(1 for r in rows if r["kind"] == "eoa"),
        },
    }

    with open(os.path.join(args.out, "q46_holders_raw.json"), "w") as fh:
        json.dump(result, fh, indent=2)
    with open(os.path.join(args.out, "q46_transfers.json"), "w") as fh:
        json.dump({"target_block": args.target_block, "logs": all_logs}, fh)

    print(json.dumps({k: v for k, v in result.items() if k != "top20"}, indent=2))
    for r in rows:
        print(f"{r['rank']:2d} {r['address']} {r['balance_tokens']:>18} {r['share_pct']:>8}% {r['kind']}")
    return 0 if result["sum_equals_supply"] else 2


if __name__ == "__main__":
    sys.exit(main())
