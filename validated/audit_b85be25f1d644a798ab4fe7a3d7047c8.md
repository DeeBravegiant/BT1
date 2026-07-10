### Title
Single Byzantine Participant Can Permanently Block All `verify_foreign_transaction` Requests via Empty Chain Registration - (File: `crates/contract/src/lib.rs`)

---

### Summary

`verify_foreign_transaction()` gates on `get_supported_foreign_chains()`, which uses a **strict all-participant intersection rule**: a chain is only "supported" if every single active participant has registered it. A single Byzantine participant — strictly below the signing threshold — can call `register_foreign_chain_config` with an empty chain list, instantly dropping every foreign chain from the supported set and causing every subsequent `verify_foreign_transaction` call to panic with `ForeignChainNotSupported`. No threshold collusion is required.

---

### Finding Description

`verify_foreign_transaction()` in `crates/contract/src/lib.rs` calls `get_supported_foreign_chains()` and panics if the requested chain is absent:

```rust
let supported_chains = self.get_supported_foreign_chains();
if !supported_chains.contains(&requested_chain) {
    env::panic_str(
        &InvalidParameters::ForeignChainNotSupported { requested: requested_chain }.to_string(),
    );
}
```

`get_supported_foreign_chains()` computes the intersection of every active participant's registered chains:

```rust
let all_active_nodes_supports_chain =
    nodes_supporting_chain.is_superset(&active_participant_account_ids);
```

A chain is included only when `nodes_supporting_chain` is a **superset** of the full active participant set — i.e., every participant must have registered it. If even one participant's entry is absent or empty, the chain is excluded.

`register_foreign_chain_config` is callable by any authenticated participant with no lower bound on the number of chains registered. A participant may submit an empty `ForeignChainConfiguration` map, which removes their entry from every chain's supporter set. After that call, `is_superset` fails for every chain, `get_supported_foreign_chains()` returns an empty set, and `verify_foreign_transaction` panics for every chain.

The codebase's own design documentation explicitly acknowledges this: *"A single node that registers an empty list (or hasn't registered yet) drops every chain — one operator can take the whole feature down."* The fix (`get_available_foreign_chains()` with a threshold-based count) is proposed but **not yet wired into `verify_foreign_transaction`** in the production contract code.

---

### Impact Explanation

Every call to `verify_foreign_transaction` for any foreign chain panics immediately at the gate check. Users cannot submit foreign-chain transaction verification requests; any attached deposit is returned, but the service is completely unavailable. This breaks the request-lifecycle and contract execution-flow invariant that a whitelisted, operationally healthy chain should be serviceable. The impact maps to:

> **Medium. Balance, request-lifecycle, participant-state, or contract execution-flow manipulation that breaks production safety/accounting invariants without relying on network-level DoS or operator misconfiguration.**

---

### Likelihood Explanation

A single participant — one of the n MPC nodes, strictly below the signing threshold — must call `register_foreign_chain_config` with an empty payload. This is a direct on-chain call requiring only that participant's NEAR account key, with no cryptographic barrier. The participant does not need to collude with others, compromise any key, or perform any off-chain action. The call is cheap and immediate. Likelihood is low because participants are economically incentivized to behave honestly, but the attack surface is a single actor with no threshold requirement.

---

### Recommendation

Wire `verify_foreign_transaction` to gate on `get_available_foreign_chains()` (threshold-based coverage count) instead of `get_supported_foreign_chains()` (strict intersection). The design document at `docs/design/calculating-supported-foreign-chains.md` already specifies this fix: a chain is available iff ≥ `signing_threshold` active participants cover it, so up to `n − signing_threshold` nodes can drop coverage without affecting availability. This is tracked under issue [#3434](https://github.com/near/mpc/issues/3434) but has not yet been applied to the production `verify_foreign_transaction` gate.

---

### Proof of Concept

1. Network is `Running` with participants `[P0, P1, …, Pn-1]`, all having registered `{Bitcoin, Ethereum}`. `verify_foreign_transaction(Bitcoin)` succeeds.

2. Participant `P0` submits:
   ```
   register_foreign_chain_config({ foreign_chain_configuration: {} })
   ```
   (empty map — no chains registered).

3. `get_supported_foreign_chains()` now evaluates `is_superset` for Bitcoin: `{P1, …, Pn-1}` is **not** a superset of `{P0, P1, …, Pn-1}` → Bitcoin excluded. Same for Ethereum. Returns `{}`.

4. Any user calling `verify_foreign_transaction(Bitcoin)` hits:
   ```
   env::panic_str("ForeignChainNotSupported { requested: Bitcoin }")
   ```
   The service is fully unavailable for all chains until `P0` re-registers.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2)

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
