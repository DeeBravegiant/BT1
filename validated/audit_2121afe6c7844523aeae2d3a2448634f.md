### Title
`EvmFinality::Latest` Allows Unfinalized Foreign-Chain State to Authorize MPC Threshold Signatures - (File: `crates/near-mpc-contract-interface/src/types/foreign_chain.rs`)

---

### Summary

The `EvmFinality` enum exposes a `Latest` variant that allows any unprivileged caller to request MPC threshold signatures verified against the current, unfinalized tip of an EVM chain. This is the direct NEAR MPC analog of the Uniswap spot-price oracle manipulation: just as `getAmountsIn()` reads a manipulable instantaneous price, `EvmFinality::Latest` reads manipulable, reorganizable chain state. An attacker can craft a transaction, obtain a valid MPC signature while the transaction is at the chain tip, then cause or wait for a reorg that erases the original transaction — leaving a valid MPC signature backed by state that no longer exists on the canonical chain.

---

### Finding Description

`EvmFinality` is a caller-controlled field inside `EvmRpcRequest`, which is embedded in `ForeignChainRpcRequest`, which is embedded in `VerifyForeignTransactionRequestArgs` — the argument to the public `verify_foreign_transaction` entry point. The enum has three variants:

```rust
pub enum EvmFinality {
    Latest,
    Safe,
    Finalized,
}
``` [1](#0-0) 

`Latest` maps to Ethereum's `"latest"` block tag — the most recently produced block, which has **zero finality guarantees**. The wiki for the EVM inspector explicitly documents only `Finalized` and `Safe` as the intended finality models, yet `Latest` is a valid, deserializable variant that the contract accepts without rejection. [2](#0-1) 

The same structural problem exists for Solana: `SolanaFinality::Processed` is the weakest commitment level (transaction seen by one validator, not yet confirmed by the cluster). [3](#0-2) 

The MPC nodes independently query the foreign chain using the caller-supplied finality tag, extract the requested values, reach RPC quorum, and then participate in the threshold signing protocol. There is no contract-side guard that rejects `EvmFinality::Latest` before the request is dispatched to nodes. [4](#0-3) 

---

### Impact Explanation

A valid MPC threshold signature is produced over a `ForeignTxSignPayloadV1` that commits to the extracted values observed at `Latest`. If the underlying EVM block is subsequently reorganized (naturally or adversarially), the canonical chain no longer contains the transaction that was verified. The MPC signature remains valid and usable, but it attests to a state that has been erased. This enables:

- **Double-spend**: A cross-chain bridge contract that releases funds upon receiving the MPC-signed attestation will release funds for a deposit transaction that was later reorganized away.
- **Forged foreign-chain verification**: The signed payload encodes extracted log data (e.g., a deposit amount, recipient address) from a block that is no longer canonical, constituting a forged verification of foreign-chain state.

This matches the allowed **High** impact: *"forged foreign-chain verification, light-client-style verification bypass … that causes invalid bridge execution or double-spend conditions."* [5](#0-4) 

---

### Likelihood Explanation

- **Attacker-controlled entry**: Any NEAR account can call `verify_foreign_transaction` with `EvmFinality::Latest` — no privileged role required.
- **Reorg feasibility**: On EVM chains with low validator sets (Polygon PoS, BNB Chain, HyperEVM, Abstract), single-block reorgs are routine. On Ethereum mainnet, MEV-driven reorgs of 1–2 blocks have been observed. The attacker only needs a reorg of the single block containing their transaction.
- **Profit motive**: Any bridge or application that releases value upon receiving the MPC-signed attestation is directly exploitable for the full value of the bridged asset.
- **No special capability needed**: The attack requires only the ability to submit an EVM transaction and call a NEAR contract — both are permissionless.

---

### Recommendation

1. **Reject `EvmFinality::Latest` and `SolanaFinality::Processed` at the contract level** inside `verify_foreign_transaction`, returning an error before the request is dispatched to nodes. Only `Safe`/`Finalized` (EVM) and `Confirmed`/`Finalized` (Solana) should be accepted.
2. **Enforce minimum finality per chain** in the on-chain `ChainEntry` configuration (voted in by participants), so that even if the enum is extended in the future, the contract can enforce a floor.
3. **Document the security invariant** that `Latest`/`Processed` are present in the enum solely for internal testing and must never be accepted in production signing flows.

---

### Proof of Concept

1. Attacker deploys a Polygon bridge contract that emits a `Deposit(address recipient, uint256 amount)` log.
2. Attacker calls the bridge contract, emitting the log in block N (the current `latest` block).
3. Attacker immediately calls NEAR `verify_foreign_transaction` with:
   ```json
   {
     "request": {
       "Polygon": {
         "tx_id": "<tx_hash>",
         "finality": "Latest",
         "extractors": [{"Log": {"log_index": 0}}]
       }
     },
     "domain_id": 0,
     "payload_version": 1
   }
   ```
4. MPC nodes query the Polygon RPC at `latest`, find the transaction in block N, extract the log, reach quorum, and produce a threshold signature over the payload hash.
5. Attacker (or a colluding validator) causes a 1-block reorg that replaces block N with a block that does not contain the deposit transaction.
6. Attacker presents the MPC-signed attestation to the destination bridge contract, which verifies the signature and releases funds — for a deposit that no longer exists on the canonical Polygon chain. [2](#0-1) [1](#0-0) [6](#0-5)

### Citations

**File:** crates/near-mpc-contract-interface/src/types/foreign_chain.rs (L101-105)
```rust
pub struct VerifyForeignTransactionRequestArgs {
    pub request: ForeignChainRpcRequest,
    pub domain_id: DomainId,
    pub payload_version: ForeignTxPayloadVersion,
}
```

**File:** crates/near-mpc-contract-interface/src/types/foreign_chain.rs (L147-150)
```rust
pub struct VerifyForeignTransactionResponse {
    pub payload_hash: Hash256,
    pub signature: SignatureResponse,
}
```

**File:** crates/near-mpc-contract-interface/src/types/foreign_chain.rs (L221-225)
```rust
pub struct EvmRpcRequest {
    pub tx_id: EvmTxId,
    pub extractors: Vec<EvmExtractor>,
    pub finality: EvmFinality,
}
```

**File:** crates/near-mpc-contract-interface/src/types/foreign_chain.rs (L768-772)
```rust
pub enum EvmFinality {
    Latest,
    Safe,
    Finalized,
}
```

**File:** crates/near-mpc-contract-interface/src/types/foreign_chain.rs (L792-796)
```rust
pub enum SolanaFinality {
    Processed,
    Confirmed,
    Finalized,
}
```

**File:** crates/near-mpc-contract-interface/src/types/foreign_chain.rs (L841-844)
```rust
pub enum EvmExtractor {
    BlockHash = 0,
    Log { log_index: u64 } = 1,
}
```
