### Title
Single Byzantine Participant Can Block All Foreign-Chain Transaction Verification via Empty Chain Registration - (`crates/contract/src/lib.rs`)

### Summary

The `verify_foreign_transaction` endpoint gates on `get_supported_foreign_chains()`, which computes the **strict intersection** of every active participant's registered chain set. A single Byzantine participant (strictly below the signing threshold) can call `register_foreign_chain_support` with an empty `SupportedForeignChains` set, instantly collapsing the intersection to empty and causing every subsequent `verify_foreign_transaction` call to be rejected with `ForeignChainNotSupported`. Bridge services that rely on this flow to release user funds are disrupted for as long as the attacker maintains the empty registration.

### Finding Description

**Root cause — strict-intersection logic in `get_supported_foreign_chains()`:** [1](#0-0) 

The function builds a per-chain map of supporting nodes, then keeps only chains where `nodes_supporting_chain.is_superset(&active_participant_account_ids)`. If any active participant is absent from a chain's supporter set, that chain is dropped. A participant that registers an empty set never appears in the supporter map for any chain, so the superset check fails for every chain and the function returns empty.

**Unconstrained registration — `register_foreign_chain_support`:** [2](#0-1) 

The function accepts any `SupportedForeignChains` value, including an empty set, with no lower-bound validation. Any active participant can call it at will.

**Gate in `verify_foreign_transaction`:** [3](#0-2) 

Every new `verify_foreign_transaction` call panics immediately if the requested chain is absent from `get_supported_foreign_chains()`. Once the intersection is empty, all chains are absent.

**Attack sequence:**

1. Byzantine participant (one node, below signing threshold) calls `register_foreign_chain_support` with `SupportedForeignChains::from(BTreeSet::new())`.
2. `get_supported_foreign_chains()` now returns an empty set for all callers.
3. Every subsequent `verify_foreign_transaction` call panics with `ForeignChainNotSupported`.
4. The attacker re-calls with an empty set on each block to prevent honest nodes from restoring the intersection by re-registering their own chains (the attacker's empty entry always breaks the superset check).

The design document explicitly acknowledges this property:



> "A single node that registers an empty list (or hasn't registered yet) drops **every** chain — one operator can take the whole feature down."

The fix (`get_available_foreign_chains()` with a threshold-based count) exists in `ForeignChainsMetadata::update_available_chains_config_cache` but has **not yet been wired into `verify_foreign_transaction`**: [4](#0-3) 

### Impact Explanation

Any bridge or application that uses `verify_foreign_transaction` to confirm a user's foreign-chain deposit before releasing funds on NEAR is blocked for the duration of the attack. Users who have already committed funds on the foreign chain (e.g., sent Bitcoin or ETH) cannot get the MPC network to attest to that fact, so the bridge cannot release the corresponding NEAR-side assets. This is a request-lifecycle and contract execution-flow manipulation that breaks the production safety invariant that a whitelisted chain is always serviceable, without requiring network-level DoS or operator misconfiguration. Impact: **Medium** per the allowed scope (balance/request-lifecycle manipulation breaking production safety invariants).

### Likelihood Explanation

The attacker needs only one compromised or malicious participant account — strictly below the signing threshold. The call is cheap (no deposit required beyond gas), can be repeated every block, and requires no coordination with other participants. The attack is therefore realistic and sustainable.

### Recommendation

Replace the `get_supported_foreign_chains()` gate in `verify_foreign_transaction` with `get_available_foreign_chains()`, which uses the threshold-based count already implemented in `ForeignChainsMetadata::update_available_chains_config_cache`. A chain should be considered available when at least `signing_threshold` active participants cover it, not when all of them do. This migration is already tracked and the threshold-based implementation already exists in the codebase; the remaining step is wiring `verify_foreign_transaction` to use it.

### Proof of Concept

```
// Attacker is participant_A (one of n participants, n > threshold)
// Step 1: register empty chain support
participant_A.call(mpc_contract, "register_foreign_chain_support", {
    foreign_chain_support: []   // empty SupportedForeignChains
})

// Step 2: verify_foreign_transaction now panics for every caller
user.call(mpc_contract, "verify_foreign_transaction", {
    request: { domain_id: 0, payload_version: "V1",
               request: { Bitcoin: { tx_id: "...", confirmations: 6, extractors: ["BlockHash"] } } }
})
// → panics: ForeignChainNotSupported { requested: Bitcoin }

// Step 3: attacker repeats step 1 each block to prevent restoration
// Any honest participant re-registering their chains does not help:
// participant_B.call(..., "register_foreign_chain_support", { foreign_chain_support: ["Bitcoin"] })
// get_supported_foreign_chains() still returns [] because participant_A's entry is empty
// and the superset check requires ALL active participants to support the chain.
```

The sandbox test `register_foreign_chain_config__returns_empty_when_not_all_registered` already demonstrates this exact collapse with a single non-registering participant: [5](#0-4)

### Citations

**File:** crates/contract/src/lib.rs (L533-542)
```rust
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
```

**File:** crates/contract/src/lib.rs (L972-983)
```rust
    pub fn register_foreign_chain_support(
        &mut self,
        foreign_chain_support: dtos::SupportedForeignChains,
    ) -> Result<(), Error> {
        let account_id = self.voter_or_panic();

        self.node_foreign_chain_support
            .foreign_chain_support_by_node
            .insert(account_id, foreign_chain_support);

        Ok(())
    }
```

**File:** crates/contract/src/lib.rs (L2203-2217)
```rust
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

**File:** crates/contract/tests/sandbox/foreign_chain_configuration.rs (L204-240)
```rust
#[tokio::test]
async fn register_foreign_chain_config__returns_empty_when_not_all_registered(
    #[case] method_name: &str,
    #[case] bitcoin_only: serde_json::Value,
) {
    // Given: a running contract with participants
    let SandboxTestSetup {
        contract,
        mpc_signer_accounts,
        ..
    } = SandboxTestSetup::builder()
        .with_protocols(ALL_PROTOCOLS)
        .build()
        .await;

    // When: only one participant registers
    let result = mpc_signer_accounts[0]
        .call(contract.id(), method_name)
        .args_json(bitcoin_only.clone())
        .transact()
        .await
        .unwrap()
        .into_result();
    assert_matches!(result, Ok(_));

    // Then: get_supported_foreign_chains returns empty (not all participants registered)
    let supported: Vec<String> = contract
        .view("get_supported_foreign_chains")
        .await
        .unwrap()
        .json()
        .unwrap();
    assert!(
        supported.is_empty(),
        "should be empty when not all participants have registered"
    );
}
```
