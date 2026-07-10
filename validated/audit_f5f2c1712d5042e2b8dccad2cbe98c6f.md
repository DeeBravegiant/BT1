### Title
Single Byzantine Participant Can Block All `verify_foreign_transaction` Requests via Strict Intersection Rule - (`crates/contract/src/lib.rs`)

### Summary

`verify_foreign_transaction` gates on `get_supported_foreign_chains()`, which requires **every** active participant to have registered a chain (strict unanimity). A single participant below the signing threshold can register an empty foreign-chain support list, causing the intersection to collapse to the empty set and permanently rejecting all foreign-chain verification requests — even for chains that have enough participants to reach the signing threshold.

### Finding Description

`verify_foreign_transaction` in `crates/contract/src/lib.rs` calls `get_supported_foreign_chains()` and panics if the requested chain is not in the result: [1](#0-0) 

`get_supported_foreign_chains()` computes the **strict intersection** of all active participants' registered chains — a chain is included only if `nodes_supporting_chain.is_superset(&active_participant_account_ids)`: [2](#0-1) 

The project's own design document explicitly identifies this as a bug:

> "Today, `get_supported_foreign_chains()` returns the **strict intersection** of every active participant's registered chains, and `verify_foreign_transaction` rejects any request whose target chain is not in it. A single node that registers an empty list (or hasn't registered yet) drops **every** chain — one operator can take the whole feature down."



The proposed fix (threshold-based `available` set) exists only as a design document; the production contract still uses the unanimity rule. [3](#0-2) 

The `register_foreign_chain_config` / `register_foreign_chain_support` call is restricted to participants (`voter_or_panic()`), but any **single** participant can register an empty list: [4](#0-3) 

The sandbox test `register_foreign_chain_config__returns_empty_when_not_all_registered` confirms that one non-registering participant collapses the supported set to empty.

The `FakeMpcContractState` in the node also mirrors this unanimity logic: [5](#0-4) 

### Impact Explanation

A single Byzantine participant (strictly below the signing threshold) can call `register_foreign_chain_support` with an empty `SupportedForeignChains` set. Because `get_supported_foreign_chains()` requires unanimity, the intersection immediately collapses to empty. Every subsequent `verify_foreign_transaction` call panics with `ForeignChainNotSupported`, regardless of how many other participants correctly cover the chain. The entire foreign-chain verification feature — including bridge inbound flows — is permanently disabled until the Byzantine participant re-registers or is reshared out. This breaks the request-lifecycle invariant: requests that are fully serviceable by a threshold-sized honest majority are unconditionally rejected.

### Likelihood Explanation

Any single active participant can trigger this with one on-chain transaction. No threshold collusion, no key material, and no network-level attack is required. The attack is cheap, reversible by the attacker at will (re-register to restore, then re-empty to re-block), and leaves no on-chain evidence of malicious intent (an empty registration is indistinguishable from a misconfigured node).

### Recommendation

Replace the unanimity gate in `verify_foreign_transaction` with the threshold-based `available` set already designed in `docs/design/calculating-supported-foreign-chains.md`. Concretely:

1. Implement `get_available_foreign_chains()` using `ForeignChainsMetadata::update_available_chains_config_cache`, which already counts per-chain supporters and filters by `>= threshold`: [6](#0-5) 

2. In `verify_foreign_transaction`, replace the call to `get_supported_foreign_chains()` with `get_available_foreign_chains()`.
3. Deprecate `get_supported_foreign_chains()` per the migration plan in the design doc.

### Proof of Concept

1. Deploy the contract with N participants (threshold T < N).
2. All participants register Bitcoin as a supported chain → `get_supported_foreign_chains()` returns `{Bitcoin}`.
3. One Byzantine participant calls `register_foreign_chain_support` with an empty `BTreeSet`.
4. `get_supported_foreign_chains()` now returns `{}` (empty intersection).
5. Any user calling `verify_foreign_transaction` for Bitcoin receives `ForeignChainNotSupported` panic, even though N−1 honest participants can still reach threshold T and service the request.
6. The attacker can toggle the attack on/off at will by re-registering or clearing their entry.

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

**File:** docs/design/calculating-supported-foreign-chains.md (L1-13)
```markdown
# Calculating the whitelisted and available foreign-chain sets

Status: Proposed — supersedes the all-participant intersection rule in
[`docs/foreign-chain-transactions.md`](../foreign-chain-transactions.md). Tracked by
[#3434](https://github.com/near/mpc/issues/3434).

## Background

Today, `get_supported_foreign_chains()` returns the **strict intersection** of every
active participant's registered chains, and `verify_foreign_transaction` rejects any
request whose target chain is not in it. A single node that registers an empty list
(or hasn't registered yet) drops **every** chain — one operator can take the whole
feature down. That is what this proposal fixes.
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

**File:** crates/node/src/indexer/fake.rs (L141-169)
```rust
        // Derive supported_foreign_chains as intersection of all active participants' votes
        let active_participant_account_ids: BTreeSet<dtos::AccountId> = state
            .parameters
            .participants()
            .participants()
            .iter()
            .map(|(id, _, _)| id.clone())
            .collect();

        let mut chain_to_supporters: BTreeMap<dtos::ForeignChain, BTreeSet<dtos::AccountId>> =
            BTreeMap::new();
        for (voter_id, chains) in &self
            .supported_foreign_chains_by_node
            .foreign_chain_support_by_node
        {
            for chain in chains.iter().copied() {
                chain_to_supporters
                    .entry(chain)
                    .or_default()
                    .insert(voter_id.clone());
            }
        }

        self.supported_foreign_chains = chain_to_supporters
            .into_iter()
            .filter(|(_, supporters)| supporters.is_superset(&active_participant_account_ids))
            .map(|(chain, _)| chain)
            .collect::<BTreeSet<_>>()
            .into();
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
