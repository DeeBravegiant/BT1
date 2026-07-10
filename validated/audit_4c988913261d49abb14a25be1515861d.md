### Title
Node Uses Governance Threshold Instead of Per-Domain Reconstruction Threshold in Robust ECDSA Presignature Generation — (`crates/node/src/providers/robust_ecdsa/presign.rs`)

---

### Summary

The MPC node's robust ECDSA presignature generation always derives its cryptographic threshold from the single governance threshold (`mpc_config.participants.threshold`) rather than from the per-domain `reconstruction_threshold` stored in each domain's `DomainConfig`. The contract now supports per-domain reconstruction thresholds that can legitimately differ (be lower than) the governance threshold, but the node never reads them. This is the direct analog of the Balancer bug: a cached/stale parameter is used in a critical computation instead of the current, domain-specific one.

---

### Finding Description

The contract stores a `reconstruction_threshold` per domain inside `DomainConfig`, which is part of `DomainRegistry` inside `RunningContractState`. These per-domain thresholds can be updated independently of the governance threshold via `vote_new_parameters` with a populated `per_domain_thresholds` map, and are applied to the `DomainRegistry` when resharing completes. [1](#0-0) [2](#0-1) 

However, the node's `MpcConfig` and `ParticipantsConfig` only carry a single `threshold` field (the governance threshold): [3](#0-2) 

The robust ECDSA presignature leader path calls `compute_thresholds` with the governance threshold, and the follower path reads `self.mpc_config.participants.threshold` directly — neither ever consults the domain's `reconstruction_threshold`: [4](#0-3) [5](#0-4) 

The same stale-threshold pattern appears in the coordinator's triple-threshold computation: [6](#0-5) 

Both sites carry an explicit `TODO(#3164)` acknowledging the problem:

> "TODO(#3164): once the node supports per-domain thresholds, this should take the domain-specific threshold instead of the single governance threshold." [7](#0-6) 

---

### Impact Explanation

The contract enforces `max(t_i) <= GovernanceThreshold`, so the governance threshold is always ≥ every per-domain reconstruction threshold. [8](#0-7) 

When a domain's `reconstruction_threshold` is set lower than the governance threshold (a valid, intended governance action), the node computes a `max_malicious` value derived from the higher governance threshold. The robust ECDSA invariant `2 * max_malicious + 1 <= num_signers` then requires more active participants than the domain's actual cryptographic threshold demands. If the number of online participants falls between the per-domain threshold and the governance threshold, presignature generation for that domain fails entirely — blocking all signing requests routed to it — even though the cryptographic security requirement is fully satisfied.

This matches the **Medium** allowed impact: *"request-lifecycle manipulation that breaks production safety/accounting invariants without relying on network-level DoS or operator misconfiguration."*

---

### Likelihood Explanation

The per-domain threshold feature is production-ready on the contract side (fully validated, stored in `DomainRegistry`, applied on resharing completion, and covered by tests). Any governance action that sets a domain's `reconstruction_threshold` below the governance threshold — a legitimate and explicitly supported operation — immediately creates the divergence. No attacker capability is required; the trigger is a normal governance vote by honest participants. [9](#0-8) 

---

### Recommendation

In `compute_thresholds` and `run_presignature_generation_follower`, replace the governance threshold with the per-domain `reconstruction_threshold` read from the domain's `DomainConfig` in the contract running state. The design document already describes the intended fix:

```rust
// Proposed (from docs/design/domain-separation.md §6.4):
let dk = distributed_key_registry.get(domain_id);
let active_signers = min_active_participants(&dk.protocol, &dk.reconstruction_threshold);
``` [10](#0-9) 

The `ContractRunningState` already carries the full `DomainRegistry` with per-domain thresholds; the node only needs to thread the correct `reconstruction_threshold` through to `compute_thresholds` and the follower path instead of reading `mpc_config.participants.threshold`.

---

### Proof of Concept

**Setup**: 10 participants, governance threshold = 7, domain D0 `reconstruction_threshold` = 5 (set via `vote_new_parameters` with `per_domain_thresholds = {D0: 5}`). 6 participants are online.

**Expected behavior**: Robust ECDSA presignature generation for D0 should succeed — 6 ≥ 5 satisfies the domain's reconstruction threshold.

**Actual behavior**: `compute_thresholds(governance_threshold=7, num_running_participants=6)` is called. `get_number_of_signers(7, 6)` returns an error or requires 7 signers; with only 6 online the invariant `2 * max_malicious + 1 <= num_signers` fails, and presignature generation for D0 is blocked. All pending sign requests for domain D0 time out and are never fulfilled, permanently freezing any cross-chain transaction that depends on a D0 signature. [11](#0-10)

### Citations

**File:** crates/contract/src/primitives/thresholds.rs (L86-108)
```rust
    /// Validates the GovernanceThreshold `k` against both the participant count and the
    /// largest ReconstructionThreshold across all domains. Layers the cross-domain rule
    /// `GovernanceThreshold >= max(ReconstructionThreshold)` on top of `validate_threshold`:
    /// the network must never be able to govern with fewer parties than are required to
    /// reconstruct any domain's key. Call this at every point where the GovernanceThreshold,
    /// a ReconstructionThreshold, or the participant set changes.
    pub fn validate_governance_against_reconstruction(
        num_participants: u64,
        governance: Threshold,
        max_reconstruction_threshold: Option<ReconstructionThreshold>,
    ) -> Result<(), Error> {
        Self::validate_threshold(num_participants, governance)?;
        if let Some(max_reconstruction_threshold) = max_reconstruction_threshold
            && governance.value() < max_reconstruction_threshold.inner()
        {
            return Err(InvalidThreshold::BelowReconstructionThreshold {
                reconstruction_threshold: max_reconstruction_threshold.inner(),
                governance_threshold: governance.value(),
            }
            .into());
        }
        Ok(())
    }
```

**File:** crates/contract/src/primitives/thresholds.rs (L222-228)
```rust
#[near(serializers=[borsh, json])]
#[derive(Debug, PartialEq, Eq, PartialOrd, Ord, Clone)]
pub struct ProposedThresholdParameters {
    parameters: ThresholdParameters,
    #[serde(default)]
    per_domain_thresholds: BTreeMap<DomainId, ReconstructionThreshold>,
}
```

**File:** crates/contract/src/state/resharing.rs (L35-38)
```rust
    /// Per-domain `ReconstructionThreshold` updates carried from the accepted
    /// proposal. Applied to the [`DomainRegistry`](crate::primitives::domain::DomainRegistry)
    /// when resharing completes; empty means "keep current per-domain thresholds".
    pub per_domain_thresholds: BTreeMap<DomainId, ReconstructionThreshold>,
```

**File:** crates/contract/src/state/resharing.rs (L562-638)
```rust
    /// Companion to the test above: two domains end the resharing with different
    /// thresholds. Only Frost is moved (to the GovernanceThreshold); CaitSith keeps 2.
    /// The pinned set keeps GovernanceThreshold >= 3 so the two values differ.
    #[expect(non_snake_case)]
    #[test]
    fn vote_reshared__final_transition__should_apply_distinct_thresholds_per_domain() {
        // Given CaitSith (index 0) and Frost (index 1) at default threshold 2, and a
        // proposal moving only Frost to the GovernanceThreshold.
        let mut env = Environment::new(Some(100), None, None);
        let mut running = gen_running_state_with_params(2, 5, 4);
        let current_params = running.parameters.clone();
        let caitsith_id = running.domains.domains()[0].id;
        let frost_id = running.domains.domains()[1].id;
        let default_threshold = running.domains.domains()[0].reconstruction_threshold;
        let frost_new_threshold = ReconstructionThreshold::new(current_params.threshold().value());
        assert_ne!(frost_new_threshold, default_threshold);
        let mut threshold_updates = BTreeMap::new();
        threshold_updates.insert(frost_id, frost_new_threshold);
        let proposal = ProposedThresholdParameters::new(current_params.clone(), threshold_updates);

        // Drive the proposal to acceptance via the real vote path.
        let prospective_epoch_id = running.prospective_epoch_id();
        let mut state = None;
        for (account, _, _) in proposal.participants().participants() {
            env.set_signer(account);
            state = running
                .vote_new_parameters(prospective_epoch_id, &proposal)
                .unwrap();
        }
        let mut state = state.expect("Should've transitioned into resharing");

        // When every domain is resharing-completed in order.
        let candidates: Vec<_> = state
            .resharing_key
            .proposed_parameters()
            .participants()
            .participants()
            .iter()
            .map(|(acc, _, _)| acc.clone())
            .collect();
        let num_domains = state.previous_running_state.domains.domains().len();
        let mut new_running = None;
        for i in 0..num_domains {
            let domain_id = state
                .previous_running_state
                .domains
                .get_domain_by_index(i)
                .unwrap()
                .id;
            let leader = find_leader(&state.resharing_key);
            env.set_signer(&leader.0);
            let key_event_id = KeyEventId {
                attempt_id: AttemptId::new(),
                domain_id,
                epoch_id: state.prospective_epoch_id(),
            };
            state.start(key_event_id, 0).unwrap();
            for account in &candidates {
                env.set_signer(account);
                new_running = state.vote_reshared(key_event_id).unwrap();
            }
        }

        // Then each domain carries its own threshold: CaitSith keeps 2, Frost holds the update.
        let new_running = new_running.expect("resharing should have transitioned to Running");
        let threshold_for = |id| {
            new_running
                .domains
                .domains()
                .iter()
                .find(|d| d.id == id)
                .unwrap()
                .reconstruction_threshold
        };
        assert_eq!(threshold_for(caitsith_id), default_threshold);
        assert_eq!(threshold_for(frost_id), frost_new_threshold);
    }
```

**File:** crates/node/src/config.rs (L24-67)
```rust
#[derive(Debug, Clone)]
pub struct MpcConfig {
    pub my_participant_id: ParticipantId,
    pub participants: ParticipantsConfig,
}

impl MpcConfig {
    /// Finds the participant ID of the local node from the participants config
    /// and constructs the MpcConfig. Returns None if the local node is not
    /// found in the participants config.
    pub fn from_participants_with_near_account_id(
        participants: ParticipantsConfig,
        my_near_account_id: &AccountId,
        my_p2p_public_key: &ed25519_dalek::VerifyingKey,
    ) -> Option<Self> {
        let my_participant_id =
            participants.get_participant_id_by_node_id(my_near_account_id, my_p2p_public_key)?;
        Some(Self {
            my_participant_id,
            participants,
        })
    }

    /// When performing a key generation or key resharing protocol, someone has to create a channel.
    /// Don't confuse with Leader Centric Computations.
    pub fn is_leader_for_key_event(&self) -> bool {
        let my_participant_id = self.my_participant_id;
        let participant_with_lowest_id = self
            .participants
            .participants
            .iter()
            .map(|p| p.id)
            .min()
            .expect("Participants list should not be empty");
        my_participant_id == participant_with_lowest_id
    }
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct ParticipantsConfig {
    /// The threshold for the MPC protocol.
    pub threshold: u64,
    pub participants: Vec<ParticipantInfo>,
}
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

**File:** crates/node/src/providers/robust_ecdsa/presign.rs (L207-234)
```rust
impl RobustEcdsaSignatureProvider {
    pub(super) async fn run_presignature_generation_follower(
        &self,
        channel: NetworkTaskChannel,
        id: UniqueId,
        domain_id: DomainId,
    ) -> anyhow::Result<()> {
        id.validate_owned_by(channel.sender().get_leader())?;
        let domain_data = self.domain_data(domain_id)?;

        let number_of_participants = self.mpc_config.participants.participants.len();
        let threshold = self.mpc_config.participants.threshold.try_into()?;
        let robust_ecdsa_threshold = translate_threshold(threshold, number_of_participants)?;

        FollowerPresignComputation {
            max_malicious: robust_ecdsa_threshold,
            keygen_out: domain_data.keyshare,
            out_presignature_store: domain_data.presignature_store,
            out_presignature_id: id,
        }
        .perform_leader_centric_computation(
            channel,
            Duration::from_secs(self.config.presignature.timeout_sec),
        )
        .await?;

        Ok(())
    }
```

**File:** crates/node/src/coordinator.rs (L396-408)
```rust
            // TODO(#3164): once each domain may declare its own
            // `reconstruction_threshold`, collect the distinct `t`s across all
            // CaitSith domains here instead of just the network-wide threshold.
            let triple_thresholds = vec![ReconstructionThreshold::new(
                running_state.participants.threshold,
            )];
            delete_stale_triples_and_presignatures(
                &secret_db,
                current_epoch_data,
                my_participant_id,
                all_domains,
                triple_thresholds,
            )?;
```

**File:** docs/design/domain-separation.md (L571-592)
```markdown
#### PR 7 — Update node to consume new contract types

**Scope**: `crates/node/src/coordinator.rs`, `crates/node/src/key_events.rs`, `crates/node/src/providers/`.

**Changes**:
- Node switches from `state()` to `state_v2()` for contract queries, with fallback to `state()` when `state_v2()` is not available (during Phase A of the rolling upgrade, before the contract is deployed). The fallback path constructs a synthetic `DistributedKeyConfig` from the old state:
  ```rust
  // Fallback: old contract, state() only
  let distributed_key = DistributedKeyConfig {
      id: old_domain_id,
      protocol: Protocol::from(old_scheme),  // infer protocol from old curve
      reconstruction_threshold: ReconstructionThreshold(global_threshold),
      purpose: old_purpose,
  };
  ```
- Coordinator reads per-key `DistributedKeyConfig` from contract state instead of using global threshold.
- Replace `translate_threshold()` hack in `robust_ecdsa.rs` with the `min_active_participants()` helper:
  ```rust
  // Node computes required active signers from DistributedKeyConfig
  let active_signers = min_active_participants(&dk.protocol, &dk.reconstruction_threshold);
  ```
  Note: `translate_threshold()` is still needed on the `state()` fallback path (it's effectively moved into the synthetic `DistributedKeyConfig` construction above). It can be fully removed once the old contract is guaranteed gone.
```
