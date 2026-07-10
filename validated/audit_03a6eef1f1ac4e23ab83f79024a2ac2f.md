### Title
`verify_foreign_transaction` Gates on Legacy All-Participant Intersection Rule Instead of Documented Threshold-Based Availability Check — (`crates/contract/src/lib.rs`)

---

### Summary

The `verify_foreign_transaction` contract method checks chain support using `get_supported_foreign_chains()`, which requires **every** active participant to have registered the chain (strict intersection). The design documentation explicitly states this rule is deprecated and that the gate should use `get_available_foreign_chains()`, which only requires ≥ `signing_threshold` participants to cover the chain. The implementation/documentation mismatch means a single participant below the signing threshold can prevent all foreign chain verification requests from being accepted by registering an empty chain support list.

---

### Finding Description

The design document `docs/design/calculating-supported-foreign-chains.md` explicitly states:

> `verify_foreign_transaction(C)` is **rejected unless `C` is available** … The legacy `get_supported_foreign_chains()` (the intersection rule) is **to be deprecated** in favour of the two views above.

The **available** set is defined as: a whitelisted chain that ≥ `signing_threshold` active participants currently cover.

However, the production implementation of `verify_foreign_transaction` in `crates/contract/src/lib.rs` still calls the old intersection-based method:

```rust
let supported_chains = self.get_supported_foreign_chains();   // line 534
if !supported_chains.contains(&requested_chain) {
    env::panic_str(...)
}
```

`get_supported_foreign_chains()` computes the **strict intersection** of every active participant's registered chains:

```rust
let all_active_nodes_supports_chain =
    nodes_supporting_chain.is_superset(&active_participant_account_ids);  // line 2206-2207
```

This means if **any single** active participant has not registered a chain (or registers an empty set), that chain is absent from the supported set and all `verify_foreign_transaction` requests for it are rejected.

The node-side check in `crates/node/src/providers/verify_foreign_tx/sign.rs` has the same flaw — `chain_is_supported` calls `policy_reader.get_supported_chains()`, which reads the same intersection-based contract view.

The design document acknowledges this directly:

> A single node that registers an empty list (or hasn't registered yet) drops **every** chain — one operator can take the whole feature down. That is what this proposal fixes.

The fix (`get_available_foreign_chains()`) exists in the contract and is correctly implemented, but `verify_foreign_transaction` was never updated to use it.

---

### Impact Explanation

A single active participant (strictly below the signing threshold) can call `register_foreign_chain_support` with an empty `BTreeSet`, immediately removing every foreign chain from `get_supported_foreign_chains()`. Every subsequent `verify_foreign_transaction` call for any chain panics with `ForeignChainNotSupported`, permanently blocking the bridge inbound flow until the participant re-registers. This breaks the production safety invariant that foreign chain requests should be accepted whenever ≥ `signing_threshold` participants cover the chain — a condition that is fully satisfied and verifiable on-chain via `get_available_foreign_chains()`.

**Impact class**: Medium — request-lifecycle and contract execution-flow manipulation that breaks production safety/accounting invariants. The bridge feature is rendered non-functional by a single below-threshold participant without any threshold collusion.

---

### Likelihood Explanation

Any active participant can call `register_foreign_chain_support` at any time; the method is authenticated only to current participants, not gated on threshold. A single malicious or compromised participant (one of potentially many) can trigger this. The attack requires no special privilege beyond being an active participant, no key material, and no coordination with other nodes.

---

### Recommendation

Replace the `get_supported_foreign_chains()` call inside `verify_foreign_transaction` with `get_available_foreign_chains()`, as the design document specifies. The threshold-based availability check already exists and is correctly computed; it just needs to be wired into the gate:

```rust
// Replace:
let supported_chains = self.get_supported_foreign_chains();
// With:
let supported_chains = self.get_available_foreign_chains();
```

Apply the same substitution to the node-side `chain_is_supported` function in `crates/node/src/providers/verify_foreign_tx/sign.rs`, replacing the `get_supported_chains()` contract view call with `get_available_foreign_chains()`.

---

### Proof of Concept

1. A running contract has 4 participants with signing threshold 3. Bitcoin is whitelisted and all 4 participants have registered Bitcoin coverage — `get_available_foreign_chains()` returns `{Bitcoin}`.
2. Participant 4 (below threshold) calls `register_foreign_chain_support({})` (empty set).
3. `get_supported_foreign_chains()` now returns `{}` (intersection with empty set = empty).
4. Any user calling `verify_foreign_transaction` for Bitcoin receives `ForeignChainNotSupported` and their deposit is consumed by the failed transaction.
5. `get_available_foreign_chains()` still returns `{Bitcoin}` (3 of 4 participants still cover it, meeting the threshold of 3) — confirming the documented gate would have accepted the request.

The existing unit test `get_available_foreign_chains__should_include_chain_when_at_least_threshold_participants_cover_it` in `crates/contract/src/lib.rs` (line 7546) already proves that `get_available_foreign_chains()` correctly returns the chain in this scenario, while `get_supported_foreign_chains()` would return empty. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

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

**File:** crates/contract/src/lib.rs (L7546-7563)
```rust
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

**File:** docs/design/calculating-supported-foreign-chains.md (L32-37)
```markdown
`verify_foreign_transaction(C)` is **rejected unless `C` is available**: the contract fails fast
instead of accepting a request that can't reach the signing threshold and letting it time out. The
rejection is temporary — `C` becomes serviceable again as soon as enough nodes report coverage.

The legacy `get_supported_foreign_chains()` (the intersection rule) is **to be deprecated** in favour
of the two views above.
```

**File:** docs/design/calculating-supported-foreign-chains.md (L69-76)
```markdown
## Participant election

Foreign-tx signing must elect participants that **cover** the requested chain
(report ≥ `rpc_quorum(C)` providers for `C`), not merely online ones — a
non-covering participant produces no share and can stall the request.
Implementation requirement, not current behavior: today the signing set is inherited
from a presignature, whose
participants were chosen for liveness, not chain coverage.
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
