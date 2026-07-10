### Title
Node's `translate_threshold()` Hardcoded Minimum of 5 Signers Is Out of Sync with Contract's DamgardEtAl Threshold Validation — (File: `crates/node/src/providers/robust_ecdsa.rs`)

---

### Summary

The node's `translate_threshold()` function enforces a hardcoded minimum of 5 signers for DamgardEtAl (Robust ECDSA) operations, but the contract's `validate_domain_threshold()` permits DamgardEtAl domains with governance thresholds as low as 2. For networks with fewer than 7 participants, the contract can accept a DamgardEtAl domain configuration that the node cannot operate, causing all DamgardEtAl signing requests to fail permanently — a direct analog to the Infrared `INITIAL_DEPOSIT` / `MIN_DEPOSIT_AMOUNT_IN_GWEI` mismatch.

---

### Finding Description

**Contract side** — `validate_domain_threshold()` in `crates/contract/src/primitives/domain.rs` validates DamgardEtAl domains using the honest-majority bound `2t − 1 ≤ n` with `t ≥ 2`: [1](#0-0) 

The governance threshold is separately constrained to `≥ ceil(0.6 × n)` by `validate_threshold()`: [2](#0-1) 

For `n = 6` participants, the minimum governance threshold is `ceil(0.6 × 6) = 4`. A DamgardEtAl domain with `reconstruction_threshold = 2` is valid at the contract level (`2×2−1 = 3 ≤ 6`), and governance threshold 4 satisfies `≥ max(reconstruction_threshold) = 2`. The contract accepts this configuration.

**Node side** — `translate_threshold()` in `crates/node/src/providers/robust_ecdsa.rs` enforces a hardcoded minimum of 5 signers, derived from the DamgardEtAl protocol's requirement that `MaxMalicious ≥ 2`: [3](#0-2) 

The node uses the **governance threshold** (not the per-domain reconstruction threshold) for all DamgardEtAl operations, as acknowledged by a TODO comment: [4](#0-3) 

The presignature background task calls `compute_thresholds` with `.expect()`: [5](#0-4) 

For `n = 6`, governance threshold = 4: `compute_thresholds(4, 6)` → `translate_threshold(4, 6)` → `number_of_signers = 4 < 5` → **panic**. The background presignature generation task crashes. No presignatures are ever produced for the DamgardEtAl domain, and all signing requests for it are permanently stuck in the pending queue until they time out.

The design document explicitly acknowledges this as an unresolved hack: [6](#0-5) 

---

### Impact Explanation

**Medium.** All DamgardEtAl signing requests fail permanently for any network configuration where the governance threshold is below 5 (i.e., `n < 7` participants at minimum governance threshold). The node's presignature generation background task panics on startup for the DamgardEtAl domain, producing no presignatures. Every `sign()` call routed to a DamgardEtAl domain is enqueued, never resolved, and eventually times out — breaking the request lifecycle and violating the production safety invariant that a valid on-chain domain configuration is operable by the node.

---

### Likelihood Explanation

**Low.** Requires participants to vote to add a DamgardEtAl domain on a network with fewer than 7 participants and a governance threshold below 5. The current production deployment uses `n = 10` with threshold = 7, which satisfies the node's requirement. However, the contract provides no guard against this configuration, so honest participants on a smaller network could unknowingly create it — exactly the "specification not in sync" risk described in the external report.

---

### Recommendation

1. **Add a contract-level guard**: In `validate_domain_threshold()` or `vote_add_domains`, enforce that when a DamgardEtAl domain is added, the current governance threshold satisfies `governance_threshold ≥ 5` (i.e., `MaxMalicious ≥ 2`). This mirrors the node's actual requirement and prevents the contract from accepting configurations the node cannot execute.

2. **Resolve the per-domain threshold TODO**: Implement the planned fix in `TODO(#3164)` — pass the per-domain `reconstruction_threshold` to `compute_thresholds` instead of the global governance threshold. This eliminates the semantic mismatch between the two values.

3. **Promote the hardcoded `5` to a shared protocol constant**: Define `MIN_DAMGARD_ETAL_SIGNERS: usize = 5` (or equivalently `MIN_MAX_MALICIOUS: usize = 2`) in a shared crate and reference it from both the contract's validation logic and the node's `translate_threshold()`, so a future change to the underlying protocol requirement is reflected in both places simultaneously.

---

### Proof of Concept

1. Deploy a network with `n = 6` participants; governance threshold = 4 (minimum: `ceil(0.6 × 6) = 4`).
2. Participants vote to add a DamgardEtAl domain with `reconstruction_threshold = 2`. Contract validation: `2×2−1 = 3 ≤ 6` ✓, governance threshold `4 ≥ 2` ✓ — **accepted**.
3. Node starts presignature generation: `compute_thresholds(governance_threshold=4, num_participants=6)` → `translate_threshold(4, 6)` → `number_of_signers = 4 < 5` → **panic** via `.expect("invalid governance threshold for robust-ECDSA")`.
4. The presignature background task crashes; no presignatures are ever generated for the DamgardEtAl domain.
5. Every `sign()` call targeting the DamgardEtAl domain is enqueued, never fulfilled, and times out — permanently breaking DamgardEtAl signing for the network.

### Citations

**File:** crates/contract/src/primitives/domain.rs (L39-72)
```rust
/// Validates the per-domain reconstruction threshold against the participant
/// count. Universal bound `2 <= t <= n` plus, for `DamgardEtAl`, the
/// honest-majority bound `2t - 1 <= n`.
pub fn validate_domain_threshold(
    domain: &DomainConfig,
    num_participants: u64,
) -> Result<(), Error> {
    let t = domain.reconstruction_threshold.inner();
    if t < MIN_RECONSTRUCTION_THRESHOLD {
        return Err(DomainError::ReconstructionThresholdTooLow.into());
    }
    if t > num_participants {
        return Err(DomainError::ReconstructionThresholdExceedsParticipants {
            threshold: t,
            participants: num_participants,
        }
        .into());
    }
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
    Ok(())
}
```

**File:** crates/contract/src/primitives/thresholds.rs (L56-84)
```rust
    fn validate_threshold(n_shares: u64, k: Threshold) -> Result<(), Error> {
        if k.value() > n_shares {
            return Err(InvalidThreshold::MaxRequirementFailed {
                max: n_shares,
                found: k.value(),
            }
            .into());
        }
        if k.value() < MIN_THRESHOLD_ABSOLUTE {
            return Err(InvalidThreshold::MinAbsRequirementFailed.into());
        }
        let lower_relative_bound = governance_threshold_lower_relative_bound(n_shares);
        if k.value() < lower_relative_bound {
            return Err(InvalidThreshold::MinRelRequirementFailed {
                required: lower_relative_bound,
                found: k.value(),
            }
            .into());
        }
        let upper_relative_bound = governance_threshold_upper_relative_bound(n_shares);
        if k.value() > upper_relative_bound {
            return Err(InvalidThreshold::MaxRelRequirementFailed {
                max: upper_relative_bound,
                found: k.value(),
            }
            .into());
        }
        Ok(())
    }
```

**File:** crates/node/src/providers/robust_ecdsa.rs (L280-290)
```rust
pub(super) fn translate_threshold(
    threshold: usize,
    number_of_participants: usize,
) -> anyhow::Result<MaxMalicious> {
    let number_of_signers = get_number_of_signers(threshold, number_of_participants)?;
    anyhow::ensure!(
        number_of_signers >= 5,
        "Robust ECDSA requires the threshold to be at least 2, which implies that the number of signers needs to be at least 5"
    );
    Ok(MaxMalicious::from((number_of_signers - 1) / 2))
}
```

**File:** crates/node/src/providers/robust_ecdsa/presign.rs (L90-94)
```rust
    let (num_signers, robust_ecdsa_threshold) = compute_thresholds(
        mpc_config.participants.threshold,
        running_participants.len(),
    )
    .expect("invalid governance threshold for robust-ECDSA");
```

**File:** crates/node/src/providers/robust_ecdsa/presign.rs (L183-205)
```rust
/// Computes `(num_signers, robust_ecdsa_threshold)` and validates the
/// `2 * max_malicious + 1 <= num_signers` invariant. Returns an error only if
/// the configured governance threshold is invalid for robust-ECDSA.
///
/// TODO(#3164): once the node supports per-domain thresholds, this should
/// take the domain-specific threshold instead of the single governance threshold.
fn compute_thresholds(
    governance_threshold: u64,
    num_running_participants: usize,
) -> anyhow::Result<(usize, MaxMalicious)> {
    let governance_threshold: usize = governance_threshold.try_into()?;
    let num_signers = get_number_of_signers(governance_threshold, num_running_participants)?;
    let robust_ecdsa_threshold =
        translate_threshold(governance_threshold, num_running_participants)?;
    anyhow::ensure!(
        robust_ecdsa_threshold
            .value()
            .checked_mul(2)
            .and_then(|v| v.checked_add(1))
            .is_some_and(|v| v <= num_signers)
    );
    Ok((num_signers, robust_ecdsa_threshold))
}
```

**File:** docs/design/domain-separation.md (L1-11)
```markdown
# Domain Separation: Protocol & Governance Configuration Design

The addition of Robust ECDSA (aka DamgardEtAl) invalidates three assumptions in the current design:

✗ There is one protocol per curve (now: both CaitSith and DamgardEtAl operate over Secp256k1).

✗ All domains share a single cryptographic threshold. The node already has a `translate_threshold()` hack to bridge this gap.

✗ Governance voting threshold and cryptographic reconstruction threshold are the same value. The threshold of how many participants must vote to change parameters is currently the same `Threshold` value as the cryptographic reconstruction threshold.

Orthogonally, first trials of adding Robust ECDSA revealed an unecessary (a tech-dept) entanglement between the smart contract and the node which makes it difficult to update the Smart Contract "independently" of the node.
```
