### Title
Missing Canonical-Chain Verification in Aptos Inspector Enables Forged Foreign-Chain Attestation - (File: `crates/foreign-chain-inspector/src/aptos/inspector.rs`)

---

### Summary

`AptosInspector::extract` verifies that a transaction is committed and successful but, unlike every other chain inspector in the codebase (Bitcoin, EVM, Starknet), never performs a canonical-chain check. A malicious or compromised whitelisted RPC provider can return a transaction that exists on a shadow network, a testnet, or a non-canonical fork, causing the MPC network to produce a threshold signature attesting to fabricated foreign-chain events.

---

### Finding Description

Every other chain inspector in the codebase performs a two-step verification:

1. Fetch the transaction receipt / raw transaction.
2. **Re-fetch the canonical block at that height and compare hashes** to confirm the transaction is anchored to the canonical chain.

`BitcoinInspector::extract` calls `self.verify_block_is_canonical(rpc_response.blockhash).await?` after the confirmation check. [1](#0-0) 

`EvmInspector::extract` calls `self.verify_block_is_canonical(transaction_receipt.block_number, transaction_receipt.block_hash).await?` after the finality check. [2](#0-1) 

`StarknetInspector::extract` calls `self.verify_block_is_canonical(rpc_response.block_number, rpc_response.block_hash).await?` after the finality check. [3](#0-2) 

`AptosInspector::extract` performs only three checks and then immediately returns extracted values:

```
ensure_hash_matches(&tx_id, &tx.hash)?;                  // hash echo check
if tx.transaction_type == "pending_transaction" { … }    // not-pending check
if !success { return Err(TransactionFailed); }            // success check
``` [4](#0-3) 

There is no `verify_block_is_canonical` call, no second RPC call to cross-check the block, and no equivalent defense-in-depth step. The `AptosFinality` enum has only one variant (`Committed`) and the match arm for it contains no canonicality assertion. [5](#0-4) [6](#0-5) 

---

### Impact Explanation

The MPC node calls `execute_foreign_chain_request`, which dispatches to `AptosInspector::extract`, and on success builds a `ForeignTxSignPayload` that is then threshold-signed by the MPC network and posted on-chain. [7](#0-6) 

Because there is no canonical-chain check, a malicious or compromised whitelisted RPC provider can return a transaction that:

- Was committed on Aptos **testnet** instead of mainnet (same transaction hash format, same API).
- Was committed on a **shadow/private Aptos network** the attacker controls.
- Was committed in a block that the attacker's node considers canonical but the real network does not (relevant if Aptos ever experiences a brief fork during a validator set change).

In all cases the inspector returns `Ok(extracted_values)`, the MPC network reaches threshold on the signing round, and a valid ECDSA signature over `(request, fabricated_observed_values, observed_at)` is posted on-chain. A bridge contract consuming this attestation (e.g., the Omnibridge inbound flow) would mint or release assets on NEAR for a deposit that never occurred on Aptos mainnet — a direct double-spend / theft of funds.

**Impact category matched**: *Cross-chain replay, forged foreign-chain verification, light-client-style verification bypass that causes invalid bridge execution or double-spend conditions.*

---

### Likelihood Explanation

**Attacker-controlled entry path**:

1. An unprivileged NEAR contract caller submits `verify_foreign_transaction` with an Aptos transaction hash that exists on a shadow network.
2. The request is stored on-chain and picked up by MPC nodes.
3. Each node fans the query to its whitelisted Aptos RPC providers. If the attacker controls (or has compromised) enough providers to satisfy the per-node RPC quorum, every node's `AptosInspector::extract` returns `Ok`.
4. All nodes independently sign the same fabricated payload; threshold is reached; the signature is posted.

The RPC whitelist voted in on-chain reduces the attack surface, but:

- A single whitelisted provider can be compromised (BGP hijack, supply-chain attack, insider).
- If the per-chain RPC quorum (`ChainEntry.quorum`) is configured as 1, a single compromised provider is sufficient per node.
- The design explicitly documents that canonical-chain verification is a **defense-in-depth** measure for all other chains precisely because provider compromise is a realistic threat. [8](#0-7) [9](#0-8) 

Aptos's BFT finality eliminates reorg-based attacks but does **not** protect against a provider returning data from a different network instance.

---

### Recommendation

Add a canonical-chain verification step to `AptosInspector::extract`, analogous to the pattern used by all other inspectors. The Aptos REST API exposes `/v1/blocks/by_height/{block_height}` which returns the canonical block hash at a given height. After confirming the transaction is committed:

1. Read `tx.block_height` from the `TransactionResponse`.
2. Call `/v1/blocks/by_height/{block_height}` to obtain the canonical block hash at that height.
3. Compare the canonical block hash against the block hash embedded in the transaction response.
4. Return `ForeignChainInspectionError::NonCanonicalBlock` on mismatch.

This mirrors the two-call pattern used by `BitcoinInspector::verify_block_is_canonical` and `EvmInspector::verify_block_is_canonical` and closes the gap between Aptos and every other supported chain.

---

### Proof of Concept

**Setup**: Configure one Aptos RPC provider in the MPC node's `foreign_chains.yaml` with `quorum = 1`. Stand up a private Aptos node (or use Aptos testnet) and submit a transaction `T` that emits a bridge deposit event for 1 000 000 USDC. Record `T`'s hash `H`.

**Attack**:

1. Submit `verify_foreign_transaction` on NEAR mainnet with `AptosRpcRequest { tx_id: H, finality: Committed, extractors: [Event { event_index: 0 }] }`.
2. The compromised/testnet RPC provider returns the `TransactionResponse` for `H` from the private/testnet network.
3. `AptosInspector::extract` passes all three checks (hash matches, not pending, success = true) and returns the fabricated deposit event. [4](#0-3) 
4. `execute_foreign_chain_request` builds `ForeignTxSignPayload::V1` containing the fabricated event. [10](#0-9) 
5. The MPC network threshold-signs the payload; the bridge contract mints 1 000 000 USDC on NEAR for a deposit that never occurred on Aptos mainnet.

### Citations

**File:** crates/foreign-chain-inspector/src/bitcoin/inspector.rs (L61-62)
```rust
        self.verify_block_is_canonical(rpc_response.blockhash)
            .await?;
```

**File:** crates/foreign-chain-inspector/src/bitcoin/inspector.rs (L81-93)
```rust
    /// Checks that the receipt's block is on the canonical chain by resolving its height via
    /// `getblockheader` and then asking the RPC for the canonical hash at that height via
    /// `getblockhash`. `getblockhash` only ever returns canonical blocks, so a mismatch means
    /// the `getrawtransaction` response was anchored to a side block (stale tx index,
    /// partially-applied reorg, divergent RPC backend, etc.).
    ///
    /// The two RPC calls are necessarily sequential — `getblockhash`'s height parameter
    /// depends on `getblockheader`'s response — so a reorg landing between them could in
    /// principle yield a spurious `NonCanonicalBlock`. The caller is expected to retry.
    ///
    /// Failures from the RPCs themselves ("Block not found" / "block height out of range")
    /// surface as `ClientError` rather than `NonCanonicalBlock`; mapping those error
    /// messages to a more specific variant is left to a follow-up.
```

**File:** crates/foreign-chain-inspector/src/evm/inspector.rs (L69-73)
```rust
        self.verify_block_is_canonical(
            transaction_receipt.block_number,
            transaction_receipt.block_hash,
        )
        .await?;
```

**File:** crates/foreign-chain-inspector/src/evm/inspector.rs (L127-134)
```rust
    /// Checks that the receipt's block is on the canonical chain by re-fetching the canonical
    /// block at `receipt_block_number` and comparing hashes. `eth_getBlockByNumber` only ever
    /// resolves to a canonical block, so a mismatch means the receipt was indexed against a
    /// side block (stale tx index, partially-applied reorg, divergent RPC backend, etc.).
    ///
    /// The canonical block's height is also asserted against the requested one — a divergent
    /// RPC that returns a hash from a different height would otherwise sneak past a
    /// hash-only check.
```

**File:** crates/foreign-chain-inspector/src/starknet/inspector.rs (L59-60)
```rust
        self.verify_block_is_canonical(rpc_response.block_number, rpc_response.block_hash)
            .await?;
```

**File:** crates/foreign-chain-inspector/src/aptos/inspector.rs (L20-24)
```rust
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash)]
#[non_exhaustive]
pub enum AptosFinality {
    Committed,
}
```

**File:** crates/foreign-chain-inspector/src/aptos/inspector.rs (L77-103)
```rust
        ensure_hash_matches(&tx_id, &tx.hash)?;

        if tx.transaction_type == "pending_transaction" {
            return Err(ForeignChainInspectionError::NotFinalized);
        }

        match finality {
            AptosFinality::Committed => {
                // A committed transaction always carries an execution result.
                let Some(success) = tx.success else {
                    return Err(ForeignChainInspectionError::MalformedRpcResponse(
                        "committed transaction is missing the success field".to_string(),
                    ));
                };
                if !success {
                    return Err(ForeignChainInspectionError::TransactionFailed);
                }
            }
        }

        let extracted_values = extractors
            .iter()
            .map(|extractor| extractor.extract_value(&tx))
            .collect::<Result<Vec<_>, _>>()?;

        Ok(extracted_values)
    }
```

**File:** crates/node/src/providers/verify_foreign_tx/sign.rs (L311-346)
```rust
            dtos::ForeignChainRpcRequest::Aptos(request) => {
                let inspector = self
                    .inspectors
                    .aptos
                    .as_ref()
                    .context("no inspector configured for Aptos")?;

                let tx_id = request.tx_id.0.into();
                let finality: AptosFinality = request.finality.clone().try_into()?;
                let extractors: Vec<AptosExtractor> = request
                    .extractors
                    .iter()
                    .cloned()
                    .map(TryInto::try_into)
                    .collect::<Result<_, _>>()?;

                let extracted_values = inspector
                    .extract(tx_id, finality, extractors)
                    .timeout(FOREIGN_CHAIN_INSPECTION_TIMEOUT)
                    .await
                    .context("timed out during execution of foreign chain request")??;

                extracted_values.into_iter().map(Into::into).collect()
            }
            _ => bail!("unsupported foreign chain request"),
        };
        let payload = match payload_version {
            dtos::ForeignTxPayloadVersion::V1 => {
                dtos::ForeignTxSignPayload::V1(dtos::ForeignTxSignPayloadV1 {
                    request: request.clone(),
                    values,
                })
            }
            _ => bail!("unsupported payload_version"),
        };
        Ok(payload)
```
