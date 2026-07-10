### Title
Single-Participant Foreign-Chain Registration Wipes Entire Supported-Chain Set, Permanently Blocking All `verify_foreign_transaction` Requests - (File: crates/contract/src/lib.rs)

### Summary
`get_supported_foreign_chains()` computes the **strict intersection** of every active participant's registered chains. A single participant who registers an empty (or chain-absent) configuration causes the intersection to collapse to the empty set, making every subsequent `verify_foreign_transaction` call revert with "not supported." This is the direct analog of the reported oracle single-point-of-failure: one data source going silent kills the entire feature.

### Finding Description

`get_supported_foreign_chains()` in `crates/contract/src/lib.rs` computes availability by requiring **all** active participants to have registered a chain:

```rust
// lib.rs:2203-2217
foreign_chain_to_node_mapping
    .into_iter()
    .filter_map(|(foreign_chain, nodes_supporting_chain)| {
        let all_active_nodes_supports_chain =
            nodes_supporting_chain.is_superset(&active_participant_account_ids);
        if all_active_nodes_supports_chain {
            Some(foreign_chain)
        } else {
            None
        }
    })
``` [1](#0-0) 

`verify_foreign_transaction` (and the node-side `chain_is_supported()` guard) gates on this set. The project's own design document explicitly acknowledges the root cause:

> "Today, `get_supported_foreign_chains()` returns the **strict intersection** of every active participant's registered chains, and `verify_foreign_transaction` rejects any request whose target chain is not in it. **A single node that registers an empty list (or hasn't registered yet) drops every chain** — one operator can take the whole feature down."



The attacker-controlled entry point is `register_foreign_chain_config` / `register_available_foreign_chain_config`, which any authenticated participant can call at any time with an empty payload. The call is idempotent and requires no threshold agreement:

```rust
// lib.rs — voter_or_panic() is the only gate; no threshold required
contract.register_foreign_chain_config(empty_foreign_chain_configuration)
``` [2](#0-1) 

The node-side guard in `crates/node/src/providers/verify_foreign_tx/sign.rs` also reads the same on-chain supported-chain set and refuses to produce a signature share if the chain is absent: [3](#0-2) 

A threshold-based fix (`get_available_foreign_chains()` backed by `update_available_chains_config_cache`) exists in the codebase but is tracked as "Status: Proposed" and `verify_foreign_transaction` has not yet been migrated to use it: [4](#0-3) 

### Impact Explanation

Every `verify_foreign_transaction` call for every chain (Bitcoin, Ethereum, Solana, Base, BNB, Arbitrum, Starknet, etc.) reverts with "not supported" for as long as the adversarial registration persists. Bridge inbound flows (foreign chain → NEAR) that depend on MPC attestation of foreign transaction finality are completely halted. Pending bridge requests time out and must be resubmitted after the attacker's registration is corrected — but because the fix requires the attacker to re-register (or be reshared out), the attacker can sustain the outage indefinitely at zero cost. This breaks the request-lifecycle and contract execution-flow invariant for the entire foreign-chain verification subsystem.

### Likelihood Explanation

Any single active participant — strictly below the signing threshold — can trigger this by calling `register_foreign_chain_config` with an empty map. No key material, no collusion, no privileged access is required. The call is cheap and can be repeated after any corrective re-registration. The attack surface is open on mainnet to any of the ~10 MPC node operators.

### Recommendation

Replace the strict-intersection rule in `get_supported_foreign_chains()` with the threshold-based `get_available_foreign_chains()` already implemented in `ForeignChainsMetadata::update_available_chains_config_cache`. Gate `verify_foreign_transaction` on the available set (≥ signing-threshold participants cover the chain) rather than the unanimous set. This mirrors the recommendation in the external report: add a reserve/fallback source so that one silent data provider cannot collapse the entire feature. [5](#0-4) [6](#0-5) 

### Proof of Concept

1. Network has N=10 participants, signing threshold T=7. All 10 have registered Bitcoin and Ethereum. `get_supported_foreign_chains()` returns `{Bitcoin, Ethereum}`.
2. Participant P₁ (one of the 10, below threshold) calls:
   ```
   register_foreign_chain_config({ foreign_chain_configuration: {} })
   ```
   with an empty map. This is accepted — `voter_or_panic()` passes because P₁ is a participant.
3. `get_supported_foreign_chains()` now computes: `{Bitcoin, Ethereum} ∩ {} = {}` (P₁'s empty set is a subset of no chain's supporter set, so `is_superset` fails for every chain).
4. Any user calling `verify_foreign_transaction` for Bitcoin or Ethereum receives "not supported" and the request reverts.
5. P₁ can re-register the empty list after every corrective action by other participants, sustaining the outage. [5](#0-4)

### Citations

**File:** crates/contract/src/lib.rs (L2176-2218)
```rust
    pub fn get_supported_foreign_chains(&self) -> dtos::SupportedForeignChains {
        let active_participant_account_ids = self
            .protocol_state
            .active_participants()
            .participants()
            .iter()
            .map(|(account_id, _, _)| account_id.clone())
            .collect::<BTreeSet<_>>();

        let mut foreign_chain_to_node_mapping: BTreeMap<
            &dtos::ForeignChain,
            BTreeSet<dtos::AccountId>,
        > = BTreeMap::new();

        for (account_id, chains) in self
            .node_foreign_chain_support
            .foreign_chain_support_by_node
            .iter()
        {
            for chain in chains.iter() {
                foreign_chain_to_node_mapping
                    .entry(chain)
                    .or_default()
                    .insert(account_id.clone());
            }
        }

        foreign_chain_to_node_mapping
            .into_iter()
            .filter_map(|(foreign_chain, nodes_supporting_chain)| {
                let all_active_nodes_supports_chain =
                    nodes_supporting_chain.is_superset(&active_participant_account_ids);

                if all_active_nodes_supports_chain {
                    Some(foreign_chain)
                } else {
                    None
                }
            })
            .cloned()
            .collect::<BTreeSet<dtos::ForeignChain>>()
            .into()
    }
```

**File:** crates/contract/src/lib.rs (L2224-2228)
```rust
    /// The **available** foreign chains: whitelisted chains that are supported
    /// by at least the signing threshold of active participants.
    pub fn get_available_foreign_chains(&self) -> dtos::AvailableForeignChains {
        self.foreign_chains.get().available_foreign_chains.clone()
    }
```

**File:** crates/contract/src/lib.rs (L6992-7015)
```rust
    #[test]
    #[should_panic(expected = "not a voter")]
    fn register_foreign_chain_config__should_reject_non_participant() {
        // Given
        let running_state = gen_running_state(1);
        let mut contract =
            MpcContract::new_from_protocol_state(ProtocolContractState::Running(running_state));
        let foreign_chain_configuration: dtos::ForeignChainConfiguration = BTreeMap::from([(
            dtos::ForeignChain::Bitcoin,
            NonEmptyBTreeSet::new(dtos::RpcProvider {
                rpc_url: "https://btc.example.com".to_string(),
            }),
        )])
        .into();

        let non_participant = gen_account_id();
        let _env = Environment::new(None, Some(non_participant), None);

        // When / Then: a non-participant is rejected. Registration now authenticates via
        // `voter_or_panic()`, which panics rather than returning an error.
        contract
            .register_foreign_chain_config(foreign_chain_configuration)
            .expect("non-participant should not be able to register");
    }
```

**File:** crates/node/src/providers/verify_foreign_tx/sign.rs (L360-378)
```rust
async fn chain_is_supported(
    policy_reader: &impl ReadSupportedForeignChain,
    request: &dtos::ForeignChainRpcRequest,
) -> Result<(), ForeignChainSupportError> {
    let on_chain_foreign_chains_support = policy_reader
        .get_supported_chains()
        .await
        .map_err(ForeignChainSupportError::FailedToReadContract)?;

    let requested_chain = request.chain();

    if on_chain_foreign_chains_support.contains(&requested_chain) {
        Ok(())
    } else {
        Err(ForeignChainSupportError::ChainNotSupported {
            requested: requested_chain,
        })
    }
}
```

**File:** crates/contract/src/foreign_chains_metadata.rs (L41-66)
```rust
    pub(crate) fn update_available_chains_config_cache(
        &mut self,
        active_tls_keys: &BTreeSet<dtos::Ed25519PublicKey>,
        threshold: u64,
    ) {
        let mut chain_to_supporter_count: std::collections::BTreeMap<dtos::ForeignChain, u64> =
            std::collections::BTreeMap::new();
        for tls_key in active_tls_keys {
            let Some(chains) = self.foreign_chains_configs.get(tls_key) else {
                continue;
            };
            for chain in chains.iter() {
                if self.rpc_whitelist.entries.is_whitelisted(chain) {
                    let count = chain_to_supporter_count.entry(*chain).or_default();
                    *count = count
                        .checked_add(1)
                        .expect("supporter count bounded by participant set size");
                }
            }
        }
        self.available_foreign_chains = chain_to_supporter_count
            .into_iter()
            .filter_map(|(chain, count)| (count >= threshold).then_some(chain))
            .collect::<BTreeSet<_>>()
            .into();
    }
```
