### Title
`available_foreign_chains` Cache Is Never Consulted in `verify_foreign_transaction` — Old Intersection Rule Remains Active - (File: `crates/contract/src/lib.rs`)

### Summary

`verify_foreign_transaction` is documented and designed to gate on `get_available_foreign_chains()` (the threshold-based, whitelist-gated set), but the implementation still calls the legacy `get_supported_foreign_chains()` (strict all-participant intersection). The `available_foreign_chains` cache stored in `ForeignChainsMetadata` is computed and maintained but never consulted in the critical execution path — a direct analog to the `transferWhitelist` that existed but was never checked in `_checkTransfer`.

### Finding Description

`ForeignChainsMetadata` stores three fields: the voted-in `rpc_whitelist`, the per-node `foreign_chains_configs`, and the derived `available_foreign_chains` cache. [1](#0-0) 

`update_available_chains_config_cache` correctly computes `available_foreign_chains` as the set of whitelisted chains covered by ≥ `threshold` active participants. [2](#0-1) 

The design documentation is explicit: "`verify_foreign_transaction(C)` is **rejected unless `C` is available**" and "The legacy `get_supported_foreign_chains()` (the intersection rule) is **to be deprecated**." [3](#0-2) 

However, the actual `verify_foreign_transaction` implementation calls `self.get_supported_foreign_chains()` — the old strict intersection rule — not `self.get_available_foreign_chains()`: [4](#0-3) 

`get_supported_foreign_chains()` requires **every** active participant to have registered a given chain. If any single participant has not registered (or registers an empty set), the intersection is empty and all foreign-chain verification requests are rejected. [5](#0-4) 

The `available_foreign_chains` field — which correctly requires only a signing-threshold of participants and also enforces the on-chain RPC whitelist — is never read by `verify_foreign_transaction`.

### Impact Explanation

This is a **Medium** impact: contract execution-flow manipulation that breaks production safety/accounting invariants.

- The `ForeignChainRpcWhitelist` (voted in by a threshold of participants) has no effect on `verify_foreign_transaction`. A chain can be whitelisted and covered by a threshold of participants, yet still be rejected because one participant hasn't registered it.
- A single participant below the signing threshold can block **all** `verify_foreign_transaction` requests for any chain by calling `register_foreign_chain_support` with an empty set. This can freeze bridge inbound flows that depend on `verify_foreign_transaction` to release funds.
- The `available_foreign_chains` cache — the intended safety invariant — is silently ignored.

### Likelihood Explanation

Any single active participant (not requiring threshold collusion) can trigger this by registering an empty foreign-chain config. This is a reachable, unprivileged call within the participant set. The participant does not need to be malicious — a misconfigured or newly joined node that hasn't registered yet produces the same effect. The attack surface is permanent until the implementation is corrected.

### Recommendation

Replace the `get_supported_foreign_chains()` call in `verify_foreign_transaction` with `get_available_foreign_chains()`:

```rust
// crates/contract/src/lib.rs, inside verify_foreign_transaction()

let requested_chain = request.request.chain();
// Replace:
// let supported_chains = self.get_supported_foreign_chains();
// With:
let supported_chains = self.get_available_foreign_chains();
if !supported_chains.contains(&requested_chain) {
    env::panic_str(
        &InvalidParameters::ForeignChainNotSupported {
            requested: requested_chain,
        }
        .to_string(),
    );
}
```

This aligns the implementation with the documented design intent and ensures the threshold-based, whitelist-gated availability check is actually enforced.

### Proof of Concept

1. Deploy the contract with 4 participants, threshold 3.
2. Vote Bitcoin into the `ForeignChainRpcWhitelist` (threshold of 3 participants vote).
3. Have 3 of 4 participants call `register_available_foreign_chain_config` with Bitcoin — `get_available_foreign_chains()` now returns `{Bitcoin}`.
4. Have participant 4 call `register_foreign_chain_support` with an empty set.
5. Call `verify_foreign_transaction` for Bitcoin.
6. **Expected (per design):** accepted, because Bitcoin is available (3 ≥ threshold).
7. **Actual:** rejected with `ForeignChainNotSupported`, because `get_supported_foreign_chains()` returns `{}` (participant 4 didn't register Bitcoin, breaking the all-participant intersection).

The `available_foreign_chains` cache correctly holds `{Bitcoin}` throughout, but is never read. [4](#0-3) [6](#0-5)

### Citations

**File:** crates/contract/src/foreign_chains_metadata.rs (L11-18)
```rust
#[near(serializers=[borsh])]
#[derive(Debug)]
pub(crate) struct ForeignChainsMetadata {
    pub(crate) rpc_whitelist: ForeignChainRpcWhitelist,
    pub(crate) available_foreign_chains: dtos::AvailableForeignChains,
    pub(crate) foreign_chains_configs:
        IterableMap<dtos::Ed25519PublicKey, dtos::ForeignChainsConfig>,
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

**File:** docs/design/calculating-supported-foreign-chains.md (L32-37)
```markdown
`verify_foreign_transaction(C)` is **rejected unless `C` is available**: the contract fails fast
instead of accepting a request that can't reach the signing threshold and letting it time out. The
rejection is temporary — `C` becomes serviceable again as soon as enough nodes report coverage.

The legacy `get_supported_foreign_chains()` (the intersection rule) is **to be deprecated** in favour
of the two views above.
```

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

**File:** docs/foreign-chain-transactions.md (L314-317)
```markdown
- **Available chain** — a whitelisted chain that at least `signing_threshold` active participants
  currently cover, so the network can serve it now. Computed dynamically from per-node reports;
  `available ⊆ whitelisted`. `verify_foreign_transaction(C)` is rejected unless `C` is available.
  Returned by `get_available_foreign_chains()`.
```
