### Title
Caller-Controlled Non-Final Finality Levels Enable Forged Foreign-Chain Verification and Bridge Double-Spend - (File: `crates/foreign-chain-inspector/src/evm/inspector.rs`, `crates/foreign-chain-inspector/src/starknet/inspector.rs`, `crates/contract/src/lib.rs`)

---

### Summary

The `verify_foreign_transaction` flow accepts a caller-supplied finality level with no on-chain or node-side minimum enforcement. An unprivileged caller can request MPC attestation of a foreign-chain transaction at a non-final finality level (`EvmFinality::Latest`, `SolanaFinality::Processed`, `StarknetFinality::AcceptedOnL2`, or `BlockConfirmations(0)` for Bitcoin). The MPC network signs the attestation, the caller uses it to claim bridge funds on NEAR, and the foreign-chain transaction is subsequently reorganized or rolled back — a direct analog to the USD rollback problem described in the external report.

---

### Finding Description

The `verify_foreign_transaction` contract method accepts a `ForeignChainRpcRequest` whose finality field is entirely caller-controlled: [1](#0-0) 

The contract's `verify_foreign_transaction` handler performs no validation of the finality level — it only checks that the chain is supported and the deposit is present: [2](#0-1) 

The `ChainEntry` validation only enforces quorum and provider config, with no minimum finality requirement: [3](#0-2) 

On the node side, the EVM inspector maps `EthereumFinality::Latest` directly to `FinalityTag::Latest` without rejection: [4](#0-3) 

For Starknet, `AcceptedOnL2` unconditionally passes the finality check — the transaction is in a Starknet block but not yet proven on Ethereum L1 and can be rolled back by the sequencer: [5](#0-4) 

For Bitcoin, the caller specifies the confirmation threshold. The ABI schema allows `BlockConfirmations(0)`: [6](#0-5) 

The `BlockConfirmations` ABI schema sets `minimum: 0.0`, meaning zero confirmations is a valid caller-supplied value: [7](#0-6) 

There is no grep match for `minimum_finality`, `min_finality`, `minimum_confirmations`, or any finality enforcement anywhere in the production codebase.

---

### Impact Explanation

The primary stated use case for `verify_foreign_transaction` is the **Omnibridge inbound flow** (foreign chain → NEAR), where the MPC signature attests that a foreign transaction finalized successfully: [8](#0-7) 

If an attacker obtains a valid MPC signature over a non-final transaction and uses it to claim bridge funds on NEAR, then the foreign-chain transaction is reorganized, the attacker has extracted real value from the bridge with no corresponding locked collateral. This is a **forged foreign-chain verification causing a double-spend condition** — matching the High impact category.

Concrete reachable scenarios:
- **EVM `Latest`**: Ethereum post-merge reorgs are rare but documented; Polygon, BNB, Arbitrum, and Base have higher reorg rates. A single-block reorg suffices.
- **Starknet `AcceptedOnL2`**: The Starknet sequencer can revert L2 blocks before L1 proof submission. The window between L2 acceptance and L1 finality is hours.
- **Bitcoin `BlockConfirmations(0)`**: A transaction with zero confirmations is not even in a block. The MPC would sign an attestation for a mempool transaction that may never confirm.
- **Solana `Processed`**: Processed transactions are not confirmed by the cluster and can be dropped or rolled back.

---

### Likelihood Explanation

The attack requires only an unprivileged NEAR account and 1 yoctoNEAR deposit. The attacker fully controls the `finality` field in the request. No threshold collusion, no privileged access, and no TEE attack is needed. The attack is directly reachable from the public contract API. For bridge deployments using `verify_foreign_transaction` for inbound flows, the likelihood is high whenever the bridge contract does not independently enforce finality on the caller side (which the MPC contract does not require).

---

### Recommendation

1. **Enforce a minimum finality level per chain in the on-chain `ChainEntry`** (voted in by participants alongside the provider list). Reject `verify_foreign_transaction` requests whose finality field is below the chain's configured minimum.
2. **Reject `EvmFinality::Latest` and `SolanaFinality::Processed` outright** at the contract level for production chains, or at minimum require the `ChainEntry` to specify an allowed finality set.
3. **Enforce a minimum `BlockConfirmations` value** (e.g., ≥ 1 for Bitcoin) in the contract, and consider a per-chain configurable minimum (e.g., 6 for Bitcoin mainnet).
4. **Reject `StarknetFinality::AcceptedOnL2`** for bridge use cases; require `AcceptedOnL1`.

---

### Proof of Concept

1. Attacker calls `verify_foreign_transaction` on the MPC contract with:
   ```json
   {
     "request": {
       "Ethereum": {
         "tx_id": "<attacker_tx_hash>",
         "finality": "Latest",
         "extractors": ["BlockHash"]
       }
     },
     "domain_id": <foreign_tx_domain>,
     "payload_version": "V1"
   }
   ```
2. The contract enqueues the request with no finality validation.
3. MPC nodes call `verify_finality_level` with `FinalityTag::Latest` — this passes as long as the latest block number ≥ the receipt's block number (trivially true for any included transaction).
4. MPC nodes sign the attestation `(request, [block_hash], ...)` and call `respond_verify_foreign_tx`.
5. Attacker submits the MPC signature to the bridge contract on NEAR to claim inbound funds.
6. The Ethereum transaction is reorganized out of the canonical chain.
7. Attacker has received NEAR-side bridge funds with no corresponding locked foreign-chain collateral — a double-spend.

The same flow applies with `SolanaFinality::Processed`, `StarknetFinality::AcceptedOnL2`, and `BitcoinRpcRequest { confirmations: BlockConfirmations(0), ... }`.

### Citations

**File:** crates/near-mpc-contract-interface/src/types/foreign_chain.rs (L767-796)
```rust
#[non_exhaustive]
pub enum EvmFinality {
    Latest,
    Safe,
    Finalized,
}

#[derive(
    Debug,
    Clone,
    Eq,
    PartialEq,
    Ord,
    PartialOrd,
    Hash,
    Serialize,
    Deserialize,
    BorshSerialize,
    BorshDeserialize,
)]
#[cfg_attr(
    all(feature = "abi", not(target_arch = "wasm32")),
    derive(schemars::JsonSchema, borsh::BorshSchema)
)]
#[non_exhaustive]
pub enum SolanaFinality {
    Processed,
    Confirmed,
    Finalized,
}
```

**File:** crates/contract/src/lib.rs (L519-557)
```rust
    pub fn verify_foreign_transaction(&mut self, request: VerifyForeignTransactionRequestArgs) {
        log!(
            "verify_foreign_transaction: predecessor={:?}, request={:?}",
            env::predecessor_account_id(),
            request
        );

        self.check_request_preconditions(
            request.domain_id,
            DomainPurpose::ForeignTx,
            Gas::from_tgas(self.config.sign_call_gas_attachment_requirement_tera_gas),
            MINIMUM_SIGN_REQUEST_DEPOSIT,
        );

        let requested_chain = request.request.chain();
        let supported_chains = self.get_supported_foreign_chains();
        if !supported_chains.contains(&requested_chain) {
            env::panic_str(
                &InvalidParameters::ForeignChainNotSupported {
                    requested: requested_chain,
                }
                .to_string(),
            );
        }

        let callback_gas = Gas::from_tgas(
            self.config
                .return_signature_and_clean_state_on_success_call_tera_gas,
        );

        let request = args_into_verify_foreign_tx_request(request);
        let callback_args = serde_json::to_vec(&(&request,)).unwrap();
        self.enqueue_yield_request(
            method_names::RETURN_VERIFY_FOREIGN_TX_AND_CLEAN_STATE_ON_SUCCESS,
            callback_args,
            callback_gas,
            move |this, id| this.add_verify_foreign_tx_request(request, id),
        );
    }
```

**File:** crates/contract/src/foreign_chain_rpc.rs (L50-93)
```rust
impl TryFrom<dtos::ChainEntry> for ChainEntry {
    type Error = ChainEntryValidationError;

    fn try_from(entry: dtos::ChainEntry) -> Result<Self, Self::Error> {
        let dtos::ChainEntry { providers, quorum } = entry;
        if quorum == 0 {
            return Err(ChainEntryValidationError::ZeroQuorum);
        }
        let providers_len = u64::try_from(providers.len()).map_err(|e| {
            ChainEntryValidationError::ProvidersLenOverflow {
                len: providers.len(),
                reason: e.to_string(),
            }
        })?;
        if quorum > providers_len {
            return Err(ChainEntryValidationError::QuorumExceedsProviders {
                quorum,
                providers_len,
            });
        }
        for (id, config) in providers.iter() {
            if let ChainRouting::PathSegment { segment } = &config.chain_routing
                && segment.contains('/')
            {
                return Err(ChainEntryValidationError::PathSegmentContainsSlash {
                    provider_id: id.0.clone(),
                });
            }
            if let (
                ChainRouting::QueryParam {
                    name: routing_name, ..
                },
                dtos::AuthScheme::Query { name: auth_name },
            ) = (&config.chain_routing, &config.auth_scheme)
                && routing_name == auth_name
            {
                return Err(ChainEntryValidationError::QueryParamCollidesWithAuth {
                    provider_id: id.0.clone(),
                    name: auth_name.clone(),
                });
            }
        }
        Ok(ChainEntry { providers, quorum })
    }
```

**File:** crates/foreign-chain-inspector/src/evm/inspector.rs (L107-124)
```rust
        let finality_tag = match finality {
            EthereumFinality::Finalized => FinalityTag::Finalized,
            EthereumFinality::Safe => FinalityTag::Safe,
            EthereumFinality::Latest => FinalityTag::Latest,
        };
        let args = GetBlockByNumberArgs::new(
            BlockNumberOrTag::Tag(finality_tag),
            ReturnFullTransactionObjects::from(false),
        );
        let head: GetBlockByNumberResponse = self
            .client
            .request(GET_BLOCK_BY_NUMBER_METHOD, &args)
            .await?;

        if head.number < receipt_block_number {
            return Err(ForeignChainInspectionError::NotFinalized);
        }
        Ok(())
```

**File:** crates/foreign-chain-inspector/src/starknet/inspector.rs (L50-57)
```rust
        let finality_sufficient = match finality {
            StarknetFinality::AcceptedOnL2 => true,
            StarknetFinality::AcceptedOnL1 => actual_finality == StarknetFinality::AcceptedOnL1,
        };

        if !finality_sufficient {
            return Err(ForeignChainInspectionError::NotFinalized);
        }
```

**File:** crates/foreign-chain-inspector/src/bitcoin/inspector.rs (L50-59)
```rust
        let transaction_block_confirmation = rpc_response.confirmations.into();
        let enough_block_confirmations =
            block_confirmations_threshold <= transaction_block_confirmation;

        if !enough_block_confirmations {
            return Err(ForeignChainInspectionError::NotEnoughBlockConfirmations {
                expected: block_confirmations_threshold,
                got: transaction_block_confirmation,
            });
        }
```

**File:** crates/contract/tests/snapshots/abi__abi_has_not_changed.snap (L2516-2520)
```text
        "BlockConfirmations": {
          "type": "integer",
          "format": "uint64",
          "minimum": 0.0
        },
```

**File:** docs/foreign-chain-transactions.md (L7-10)
```markdown
This feature lets the MPC network sign payloads only after verifying a specific foreign-chain transaction, so NEAR contracts can react to external chain events without a trusted relayer. Primary use cases:

* Omnibridge inbound flow (foreign chain -> NEAR) where Chain Signatures are required to attest that a foreign transaction finalized successfully.
* Broader chain abstraction: a single MPC network verifies foreign chain state and returns small, typed observations that contracts can interpret.
```
