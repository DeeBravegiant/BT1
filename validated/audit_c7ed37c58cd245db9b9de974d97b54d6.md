### Title
Single-Participant Instantaneous Chain-Support Manipulation Blocks All Foreign-Transaction Verification Requests - (File: crates/contract/src/lib.rs)

### Summary
Any single participant can call `register_foreign_chain_support()` with an empty set in one transaction, immediately causing `get_supported_foreign_chains()` — which computes the **strict intersection** of all participants' registered chains — to return an empty set. Because `verify_foreign_transaction()` gates on this result, all new foreign-chain verification requests are instantly rejected for every chain, breaking the bridge flow for all users without requiring threshold-level collusion.

### Finding Description

`verify_foreign_transaction()` checks whether the requested chain is in the supported set before queuing the request:

```rust
// crates/contract/src/lib.rs  lines 533-542
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

`get_supported_foreign_chains()` computes the **strict intersection** of every active participant's registered chains:

```rust
// crates/contract/src/lib.rs  lines 2203-2217
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
    ...
```

Any participant can update their entry in `node_foreign_chain_support` at any time via `register_foreign_chain_support()`:

```rust
// crates/contract/src/lib.rs  lines 972-983
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

This is an instantaneous, persistent, single-transaction state change. Because the intersection requires **all** active participants to support a chain, inserting an empty set for any one participant immediately drops every chain from the supported set. The design documentation explicitly acknowledges this root cause:

> "Today, `get_supported_foreign_chains()` returns the **strict intersection** of every active participant's registered chains, and `verify_foreign_transaction` rejects any request whose target chain is not in it. A single node that registers an empty list (or hasn't registered yet) drops **every** chain — one operator can take the whole feature down."

The analog to the external report is exact: just as a whale withdrawing TVL instantaneously drops the kerosene price and breaks other users' collateralization ratios, a single participant re-registering with an empty chain list instantaneously drops the supported-chain set and breaks all users' foreign-transaction verification requests.

### Impact Explanation

A single participant (strictly below the signing threshold) can, in one transaction, cause `get_supported_foreign_chains()` to return an empty set. Every subsequent `verify_foreign_transaction()` call panics with `ForeignChainNotSupported`, regardless of which chain is requested. This breaks the request-lifecycle safety invariant: the network is healthy and capable of signing, but no new foreign-chain verification requests can be submitted. Any in-flight bridge operations that have not yet been queued are permanently blocked until the attacker re-registers their chains. This matches the **Medium** allowed impact: *request-lifecycle manipulation that breaks production safety/accounting invariants without relying on network-level DoS or operator misconfiguration*.

### Likelihood Explanation

The attack requires only that the caller is a current participant (`voter_or_panic()` check). No threshold collusion, no key material, no privileged operator access is needed. The call is cheap (a single storage write) and the effect is immediate and persistent. Any one of the n participants can execute this unilaterally.

### Recommendation

Replace the strict-intersection `get_supported_foreign_chains()` call inside `verify_foreign_transaction()` with the threshold-based `get_available_foreign_chains()` already implemented in `ForeignChainsMetadata::update_available_chains_config_cache()`. The threshold-based approach requires ≥ `signing_threshold` participants to cover a chain before it is considered available, so a single participant dropping their registration cannot remove a chain from the available set. This migration is already tracked internally (issue #3434) and the new API (`register_foreign_chains_config` / `get_available_foreign_chains`) is already deployed alongside the legacy path.

### Proof of Concept

**Setup:** Network has 4 participants (threshold = 3). All 4 have registered support for Bitcoin via `register_foreign_chain_support`. `get_supported_foreign_chains()` returns `{Bitcoin}`.

**Attack:**
1. Participant P1 (one of the 4) calls `register_foreign_chain_support({})` — an empty `SupportedForeignChains`.
2. `node_foreign_chain_support.foreign_chain_support_by_node[P1]` is now `{}`.
3. `get_supported_foreign_chains()` computes the intersection: `{Bitcoin} ∩ {} = {}`. Returns empty set.
4. Any user calling `verify_foreign_transaction({ chain: Bitcoin, ... })` hits the guard at line 535 and receives `ForeignChainNotSupported { requested: Bitcoin }`.
5. The network is fully operational (3 of 4 nodes still cover Bitcoin, threshold is met), but no new bridge requests can be submitted.
6. P1 can restore service at will by re-registering `{Bitcoin}`, making this a repeatable, zero-cost griefing and request-blocking primitive available to any single participant. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** crates/contract/src/lib.rs (L2176-2217)
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
```

**File:** docs/design/calculating-supported-foreign-chains.md (L9-13)
```markdown
Today, `get_supported_foreign_chains()` returns the **strict intersection** of every
active participant's registered chains, and `verify_foreign_transaction` rejects any
request whose target chain is not in it. A single node that registers an empty list
(or hasn't registered yet) drops **every** chain — one operator can take the whole
feature down. That is what this proposal fixes.
```
