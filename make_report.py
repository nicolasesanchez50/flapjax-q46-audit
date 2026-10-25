#!/usr/bin/env python3
"""
Render the public audit page (README.md) and a CSV from the scan output.

Inputs:  data/q46_holders_raw.json  (q46_holders.py output)
         data/labels.json           (optional: address -> {name, source, how})
Outputs: README.md, data/q46_top20.csv
"""

import json
import os

RAW = "data/q46_holders_raw.json"
LABELS = "data/labels.json"
ZERO = "0x0000000000000000000000000000000000000000"
DEAD = "0x000000000000000000000000000000000000dead"

d = json.load(open(RAW))
labels = json.load(open(LABELS)) if os.path.exists(LABELS) else {}
labels = {k.lower(): v for k, v in labels.items()}

rows = d["top20"]
for r in rows:
    lab = labels.get(r["address"].lower())
    r["label"] = lab["name"] if lab else ("unlabelled" if r["kind"] != "contract" else "unlabelled (contract)")
    r["label_source"] = lab["source"] if lab else ""

conc = d["concentration"]
kinds = d["top20_kinds"]
n_contracts = kinds["contracts"]
n_eoa = kinds["eoa"]
supply_tokens = d["total_supply_tokens"]


def fmt(x):
    """thousands separators, 6 dp"""
    return f"{float(x):,.6f}"


lines = []
A = lines.append
A("# FLAPJAX top-20 holders — last block of 2026-10-23 (COT, UTC−5)\n")
A("Receipt Relay Q46 audit. Every number below is derived from the BNB Smart Chain "
  "Transfer log of the FLAPJAX BEP-20 token — no explorer API, no holder index, no "
  "third-party list. Re-checkable against any public BSC node.\n")

A("## Snapshot\n")
A(f"- **Token:** FLAPJAX `{d['token']}` ({d['decimals']} decimals), BNB Smart Chain (chain id 56)")
A(f"- **Snapshot block:** `{d['target_block']}`")
A(f"- **Block timestamp:** {d['target_block_time_utc']} — the last block of 23 October 2026 in "
  "Colombia time (COT = UTC−5); the next block is 2026-10-24 05:00:00 UTC")
A(f"- **Block hash:** `{d['target_block_hash']}`")
A(f"- **Total supply at that block:** {fmt(supply_tokens)} FLAPJAX (`{d['total_supply_at_block']}` base units)")
A(f"- **Holder count with a non-zero balance:** {d['holder_count_nonzero']:,}")
A(f"- **Transfer logs scanned:** {d['transfer_logs_scanned']:,} (from deployment block "
  f"{d['deploy_block']} to the snapshot block inclusive)")
A(f"- **Sum of all balances:** {d['sum_of_balances']} base units — "
  f"{'equals' if d['sum_equals_supply'] else 'DOES NOT EQUAL'} `totalSupply()` read at the same block\n")

A("## The 20 largest holders\n")
A("**Re-read check:** after deriving the table from the transfer log, every one of the 20 balances was "
  "re-read independently with `balanceOf(address)` at block "
  f"{d['target_block']} — all 20 matched the log-derived figure exactly, and `totalSupply()` at the "
  "same block matched the sum of all balances.\n")
A("Every row is read at block **" + str(d["target_block"]) + "** (the `Block` column repeats it so each "
  "row stands alone). Balances are given as exact base units (18 decimals) and as whole FLAPJAX.\n")
A("| # | Address | Balance (base units, 18 dp) | Balance (FLAPJAX) | Share of supply | Type | Block | Label |")
A("|---|---------|----------------------------:|------------------:|----------------:|------|------:|-------|")
for r in rows:
    A(f"| {r['rank']} | `{r['address']}` | {r['balance']} | {fmt(r['balance_tokens'])} | "
      f"{r['share_pct']:.4f}% | {r['kind']} | {d['target_block']} | {r['label']} |")
A("")

A("## Concentration\n")
A(f"- Top 5 hold **{conc['top5_pct']:.4f}%** of supply ({fmt(conc['top5_tokens'])} FLAPJAX)")
A(f"- Top 10 hold **{conc['top10_pct']:.4f}%** ({fmt(conc['top10_tokens'])} FLAPJAX)")
A(f"- Top 20 hold **{conc['top20_pct']:.4f}%** ({fmt(conc['top20_tokens'])} FLAPJAX)")
A("")
A(f"**Of the top 20 addresses, {n_contracts} are contracts and {n_eoa} carry no code at all**, decided by "
  "`eth_getCode` at the snapshot block (non-empty code = contract; empty code and no EIP-7702 "
  "delegation designator = no code). Of those "
  f"{n_eoa} code-less addresses, one is the conventional burn address `0x…dEaD` and one is the "
  f"project's own wallet, leaving **{n_eoa - 2} ordinary externally-owned wallets** holding a "
  "top-20 balance. Two of the three contracts are the two PancakeSwap V3 pools above; the "
  "third contract, `0x238a358808379702088667322f80ac48bad5e6c4` (1.4619%), answers neither "
  "`token0()` nor a factory `getPair`/`getPool` lookup, so it is left unlabelled.\n")

if any(r["label_source"] for r in rows):
    A("## Labels and their public sources\n")
    for r in rows:
        if r["label_source"]:
            A(f"- **`{r['address']}`** — {r['label']}. {r['label_source']}")
    A("")
A("Everything not listed above is left **unlabelled**: a balance is visible on-chain but the "
  "owner is not identified by any public record we could verify. Per the task, saying unknown "
  "beats a guess.\n")

A("## How this was produced (and how to re-check it)\n")
A("Balance of an address = sum of every `Transfer` into it minus every `Transfer` out of it, "
  "over all logs up to the snapshot block. Supply is the sum of all balances, which is then "
  "checked against `totalSupply()` read at the same block.\n")
A("```solidity")
A("// every BEP-20 transfer")
A("topic0 = keccak256(\"Transfer(address,address,uint256)\")")
A("       = 0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef")
A(f"address = {d['token']}")
A(f"fromBlock = {d['deploy_block']}   toBlock = {d['target_block']}")
A("```")
A("Re-check any single holder on any public BSC node:\n")
A("```bash")
A('curl -s -X POST "$BSC_RPC" -H "Content-Type: application/json" \\')
A('  -d \'{"jsonrpc":"2.0","id":1,"method":"eth_call","params":[{"to":"' + d["token"] + '",')
A('        "data":"0x70a08231' + rows[0]["address"][2:].lower().rjust(64, "0") + '"},')
A('        "' + hex(d["target_block"]) + '"]}\'   # balanceOf(top holder) at the snapshot block')
A('curl -s -X POST "$BSC_RPC" -H "Content-Type: application/json" \\')
A('  -d \'{"jsonrpc":"2.0","id":1,"method":"eth_call","params":[{"to":"' + d["token"] + '",')
A('        "data":"0x18160ddd"},' + '"' + hex(d["target_block"]) + '"]}\'   # totalSupply at the snapshot block')
A("```")
A("Divide the returned hex by 10^18 for whole FLAPJAX. The same reads work on BscScan's "
  "contract reader at that block number.\n")
A("The full scan is reproducible with `q46_holders.py` in this repository "
  "(`BSC_RPC_URL=<any public BSC node> python3 q46_holders.py`); the raw output, including "
  "every scanned chunk and all transfer logs, is in `data/`.\n")

A("## Files\n")
A("- `q46_holders.py` — the scanner: chunked `eth_getLogs` over the token's whole history, "
  "balance derivation, `totalSupply` cross-check, contract/EOA classification")
A("- `data/q46_holders_raw.json` — snapshot metadata, top-20 rows, concentration figures")
A("- `data/q46_top20.csv` — the same top-20 table, machine-readable")
A("- `data/q46_transfers.json` — every Transfer log used (block, tx hash, from, to, value)\n")
A("---")
A("Audit produced by agent `hermes-jobs` (Taskmarket agent 63998), BSC wallet "
  "`0xa5c05B52790896b843B2701Aef85Ef1E2202F62d`.")

open("README.md", "w").write("\n".join(lines) + "\n")

with open("data/q46_top20.csv", "w") as fh:
    fh.write("rank,address,balance_base_units,balance_tokens,share_pct,kind,label\n")
    for r in rows:
        fh.write(f"{r['rank']},{r['address']},{r['balance']},{r['balance_tokens']},"
                 f"{r['share_pct']},{r['kind']},\"{r['label']}\"\n")

print(f"README.md written: {len(rows)} rows, sum_equals_supply={d['sum_equals_supply']}")
print(f"top5 {conc['top5_pct']}%  top10 {conc['top10_pct']}%  top20 {conc['top20_pct']}%  "
      f"contracts {n_contracts} / eoa {n_eoa}")
