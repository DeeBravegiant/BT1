### Title
`verify_foreign_transaction` Gates on Strict-Intersection Availability Check Instead of Threshold-Based Check, Enabling a Single Byzantine Participant to Block All Foreign Chain Verification — (`File: crates/contract/src/lib.rs`)

---

### Summary

`verify_foreign_transaction` calls `get_supported_foreign_chains()`, which requires **every** active participant to have registered support for a chain (strict set-intersection). A single Byzantine participant below the signing threshold can block all foreign chain verification requests by simply not registering support, even when a full signing-threshold of participants is ready to serve the request. The contract already contains the correct threshold-based function (`get_available_foreign_chains()`), but the request gate uses the wrong one.

---

### Finding Description

`verify_foreign_transaction` enforces the following availability gate:

```rust
let supported_chains = self.get_supported_foreign_chains();
if !supported_chains.contains(&requested_chain) {
    env::panic_str(
        &InvalidParameters::ForeignChainNotSupported { requested: requested_chain }.to_string(),
    );
}
``` [1](#0-0) 

`get_supported_foreign_chains()` computes the strict intersection of every active participant's registered chains:

```rust
let all_active_nodes_supports_chain =
    nodes_supporting_chain.is_superset(&active_participant_account_ids);

if all_active_nodes_supports_chain {
    Some(foreign_chain)
} else {
    None
}
``` [2](#0-1) 

This means **all** active participants must have registered support for a chain before any user can submit a `verify_foreign_transaction` request for it. If even one participant has not registered (or registers an empty set), the chain is absent from the supported set and every request for it is rejected.

The contract already exposes the correct threshold-based function:

```rust
/// The **available** foreign chains: whitelisted chains that are supported
/// by at least the signing threshold of active participants.
pub fn get_available_foreign_chains(&self) -> dtos::AvailableForeignChains {
    self.foreign_chains.get().available_foreign_chains.clone()
}
``` [3](#0-2) 

The project's own design document explicitly identifies this as a defect:

> "Today, `get_supported_foreign_chains()` returns the **strict intersection** of every active participant's registered chains … A single node that registers an empty list (or hasn't registered yet) drops **every** chain — one operator can take the whole feature down. That is what this proposal fixes."



The fix proposed in the design document — using `get_available_foreign_chains()` (threshold-based) instead of `get_supported_foreign_chains()` (all-participant intersection) — has **not** been applied to `verify_foreign_transaction`.

---

### Impact Explanation

A single Byzantine participant (strictly below the signing threshold) can suppress all foreign chain verification requests for any or all chains by simply omitting their `register_foreign_chain_support` call (or registering an empty set). Because `verify_foreign_transaction` panics before enqueuing the yield when the chain is absent from the strict-intersection set, no request ever reaches the MPC signing nodes — the feature becomes entirely inaccessible for the targeted chain(s). This breaks the foreign chain verification request lifecycle and the bridge inbound flow that depends on it, without requiring any threshold-level collusion.

This matches the allowed impact: **Medium — request-lifecycle manipulation that breaks production safety/accounting invariants without relying on network-level DoS or operator misconfiguration.**

---

### Likelihood Explanation

Any single active participant can trigger this unilaterally and at any time by not registering (or deregistering) foreign chain support. No cryptographic capability, key material, or threshold collusion is required. The action is silent and indistinguishable from a misconfigured node, making it easy to sustain.

---

### Recommendation

Replace the availability gate in `verify_foreign_transaction` with the threshold-based check:

```rust
// Before (strict intersection — all participants required):
let supported_chains = self.get_supported_foreign_chains();

// After (threshold-based — signing_threshold participants sufficient):
let supported_chains = self.get_available_foreign_chains();
``` [1](#0-0) 

---

### Proof of Concept

Given a 4-participant network with signing threshold 3 and Bitcoin whitelisted:

1. Participants P1, P2, P3 call `register_foreign_chain_support([Bitcoin])`.
2. Byzantine participant P4 calls `register_foreign_chain_support([])` (empty).
3. `get_supported_foreign_chains()` computes the strict intersection → **empty set** (P4 does not support Bitcoin).
4. Any user calling `verify_foreign_transaction` for Bitcoin hits the panic: `ForeignChainNotSupported`.
5. P1, P2, P3 (= signing threshold) are fully capable of serving the request, but it is never enqueued.

This is confirmed by the existing contract test:

```rust
// Then - only Bitcoin is unanimous
assert!(result.contains(&dtos::ForeignChain::Bitcoin));
assert!(!result.contains(&dtos::ForeignChain::Ethereum)); // one participant didn't register Ethereum
``` [4](#0-3) 

The test at lines 7545–7563 further confirms that `get_available_foreign_chains()` correctly returns Bitcoin as available when exactly the threshold (3 of 4) participants cover it — the behavior that `verify_foreign_transaction` should rely on. [5](#0-4)

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

**File:** crates/contract/src/lib.rs (L2224-2228)
```rust
    /// The **available** foreign chains: whitelisted chains that are supported
    /// by at least the signing threshold of active participants.
    pub fn get_available_foreign_chains(&self) -> dtos::AvailableForeignChains {
        self.foreign_chains.get().available_foreign_chains.clone()
    }
```

**File:** crates/contract/src/lib.rs (L7116-7120)
```rust
        // Then - only Bitcoin is unanimous
        assert!(result.contains(&dtos::ForeignChain::Bitcoin));
        assert!(!result.contains(&dtos::ForeignChain::Ethereum));
        assert_eq!(result.len(), 1);
    }
```

**File:** crates/contract/src/lib.rs (L7545-7563)
```rust
    #[test]
    fn get_available_foreign_chains__should_include_chain_when_at_least_threshold_participants_cover_it()
     {
        // Given: 4 participants, signing threshold 3; Bitcoin whitelisted.
        let (_context, mut contract, _) =
            basic_setup_with_protocol(Protocol::CaitSith, DomainPurpose::ForeignTx, &mut OsRng);
        let participants = participant_account_ids(&contract);
        whitelist_chain(&mut contract, dtos::ForeignChain::Bitcoin);

        // When: exactly the threshold (3) of 4 participants cover Bitcoin — one node does not.
        for account_id in participants.iter().take(3) {
            register_foreign_chain_config(&mut contract, account_id, [dtos::ForeignChain::Bitcoin]);
        }

        // Then: Bitcoin is available. A single non-covering node cannot take it down — the
        // regression the legacy intersection rule had.
        let available = contract.get_available_foreign_chains();
        assert!(available.contains(&dtos::ForeignChain::Bitcoin));
        assert_eq!(available.len(), 1);
```
