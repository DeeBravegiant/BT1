### Title
`recompute_available_foreign_chains` Uses Raw `reconstruction_threshold` Instead of Protocol-Adjusted Signer Count, Inflating the Available-Chain Set for DamgardEtAl ForeignTx Domains - (File: crates/contract/src/lib.rs, crates/contract/src/foreign_chains_metadata.rs)

---

### Summary

`recompute_available_foreign_chains` computes the available foreign-chain set by comparing a per-chain supporter count against the raw `reconstruction_threshold` (`t`). For the DamgardEtAl (Robust ECDSA) protocol, the actual number of active signers required to produce a valid signature is `2t − 1`, not `t`. The code contains an explicit TODO acknowledging this gap. As a result, when a `ForeignTx` domain is configured with DamgardEtAl, chains are promoted to "available" with fewer supporters than the network actually needs to sign them, breaking the contract's core safety invariant for `verify_foreign_transaction`.

---

### Finding Description

`recompute_available_foreign_chains` selects the threshold to use as:

```rust
let Some(threshold) = self.protocol_state.domain_registry().ok().and_then(|r| {
    r.domains()
        .iter()
        .filter(|d| d.purpose == DomainPurpose::ForeignTx)
        .map(|d| d.reconstruction_threshold.inner())   // raw t
        .max()
}) else { ... };
``` [1](#0-0) 

It then passes this raw `t` directly into `update_available_chains_config_cache`:

```rust
self.foreign_chains
    .get_mut()
    .update_available_chains_config_cache(&active_tls_keys, threshold);
``` [2](#0-1) 

Inside `update_available_chains_config_cache`, a chain is marked available when `count >= threshold`:

```rust
self.available_foreign_chains = chain_to_supporter_count
    .into_iter()
    .filter_map(|(chain, count)| (count >= threshold).then_some(chain))
    .collect::<BTreeSet<_>>()
    .into();
``` [3](#0-2) 

The code itself acknowledges the defect with a TODO:

```rust
// TODO(#3556): replace this with a per-scheme
// `required_active_signers(protocol, reconstruction_threshold)`.
``` [4](#0-3) 

For DamgardEtAl, `validate_domain_threshold` already enforces the honest-majority bound `2t − 1 ≤ n` at configuration time:

```rust
if domain.protocol == Protocol::DamgardEtAl {
    let required = t.checked_mul(2).and_then(|x| x.checked_sub(1))...;
    if required > num_participants { return Err(...) }
}
``` [5](#0-4) 

So the contract knows DamgardEtAl needs `2t − 1` signers, but `recompute_available_foreign_chains` ignores this and uses `t`. This is the direct analog of M-14: just as the Sentiment rate model used the full `liquidity` balance instead of `liquidity − reserves`, this code uses the raw reconstruction threshold instead of the protocol-adjusted signer count, making the computed "available" set more permissive than the network can actually service.

---

### Impact Explanation

The design invariant, stated in the documentation, is:

> `verify_foreign_transaction(C)` is **rejected unless `C` is available**: the contract fails fast instead of accepting a request that can't reach the signing threshold. [6](#0-5) 

When a `ForeignTx` domain uses DamgardEtAl with reconstruction threshold `t`, the contract marks chain `C` as available once `t` participants report coverage. But signing actually requires `2t − 1` participants. Any `verify_foreign_transaction` request accepted under this inflated available set will be dispatched to the MPC nodes, which will fail to reach the required signer count and let the request time out. This breaks the request-lifecycle safety invariant: accepted requests are guaranteed to fail rather than being rejected up front, corrupting the contract's execution flow and wasting user gas.

---

### Likelihood Explanation

A `ForeignTx` domain using DamgardEtAl is a valid, governance-approved configuration — `validate_domain_threshold` explicitly handles it. Once such a domain exists, the miscalculation is permanent and automatic: every call to `register_foreign_chains_config` (callable by any attested participant) triggers `recompute_available_foreign_chains`, which re-inflates the available set. Any unprivileged user can then submit `verify_foreign_transaction` requests for chains that are incorrectly marked available, causing systematic request-lifecycle failures. The TODO comment confirms the developers are aware the current implementation is wrong for this case.

---

### Recommendation

Replace the raw `reconstruction_threshold` with a protocol-aware helper, as the TODO already prescribes:

```rust
fn required_active_signers(protocol: Protocol, t: u64) -> u64 {
    match protocol {
        Protocol::DamgardEtAl => 2 * t - 1,
        _ => t,
    }
}
```

Then in `recompute_available_foreign_chains`:

```rust
.map(|d| required_active_signers(d.protocol, d.reconstruction_threshold.inner()))
.max()
```

This mirrors the fix in M-14 — exclude the "reserved" capacity (the extra `t − 1` signers DamgardEtAl requires) from the effective threshold, so the available-chain gate reflects what the network can actually deliver.

---

### Proof of Concept

**Setup:** 5 participants, DamgardEtAl ForeignTx domain with `reconstruction_threshold = 3` (so `2t − 1 = 5` signers required; `validate_domain_threshold` accepts this since `5 ≤ 5`).

**Current behavior:**
- `recompute_available_foreign_chains` uses `threshold = 3`.
- 3 participants register coverage for Bitcoin → `count = 3 ≥ 3` → Bitcoin marked **available**.
- A user calls `verify_foreign_transaction` for Bitcoin → accepted by the contract.
- MPC nodes attempt signing with only 3 covering participants; DamgardEtAl requires 5 → signing fails → request times out.

**Expected behavior:**
- `recompute_available_foreign_chains` should use `threshold = 2*3 − 1 = 5`.
- 3 participants register coverage → `count = 3 < 5` → Bitcoin marked **not available**.
- `verify_foreign_transaction` for Bitcoin → rejected immediately with `ForeignChainNotSupported`.

The discrepancy is structurally identical to M-14: the formula uses an uncorrected base value (`t`) where the correct value excludes a reserved portion (`t` adjusted to `2t − 1`), causing the system to report more capacity than it actually has.

### Citations

**File:** crates/contract/src/lib.rs (L1032-1046)
```rust
        // TODO(#3556): replace this with a per-scheme
        // `required_active_signers(protocol, reconstruction_threshold)`.
        let Some(threshold) = self.protocol_state.domain_registry().ok().and_then(|r| {
            r.domains()
                .iter()
                .filter(|d| d.purpose == DomainPurpose::ForeignTx)
                .map(|d| d.reconstruction_threshold.inner())
                .max()
        }) else {
            // No op if contract isn't in Running or Resharing state, or
            // there is no foreign tx domain registered.
            // Not panicking is intentional.
            log!("Skipping available foreign chains recomputation");
            return;
        };
```

**File:** crates/contract/src/lib.rs (L1053-1055)
```rust
        self.foreign_chains
            .get_mut()
            .update_available_chains_config_cache(&active_tls_keys, threshold);
```

**File:** crates/contract/src/foreign_chains_metadata.rs (L61-65)
```rust
        self.available_foreign_chains = chain_to_supporter_count
            .into_iter()
            .filter_map(|(chain, count)| (count >= threshold).then_some(chain))
            .collect::<BTreeSet<_>>()
            .into();
```

**File:** crates/contract/src/primitives/domain.rs (L57-70)
```rust
    if domain.protocol == Protocol::DamgardEtAl {
        let required = t
            .checked_mul(2)
            .and_then(|x| x.checked_sub(1))
            .ok_or(DomainError::ReconstructionThresholdOverflow { threshold: t })?;
        if required > num_participants {
            return Err(DomainError::InsufficientParticipantsForProtocol {
                protocol: domain.protocol,
                required,
                participants: num_participants,
            }
            .into());
        }
    }
```

**File:** docs/design/calculating-supported-foreign-chains.md (L32-34)
```markdown
`verify_foreign_transaction(C)` is **rejected unless `C` is available**: the contract fails fast
instead of accepting a request that can't reach the signing threshold and letting it time out. The
rejection is temporary — `C` becomes serviceable again as soon as enough nodes report coverage.
```
