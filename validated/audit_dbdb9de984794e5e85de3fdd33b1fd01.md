### Title
Sequential `UpdateId` Counter Enables Re-Org-Based Vote Misdirection in Contract Upgrade Governance - (File: `crates/contract/src/update.rs`)

---

### Summary

`UpdateId` is a monotonically incrementing `u64` counter, not a content-addressed hash of the proposal. A participant who submits `vote_update(N)` is committing to a numeric slot, not to the specific contract code or config they reviewed. A NEAR short-fork (re-org) that drops the original `propose_update` transaction allows an attacker to race in their own proposal and claim the same numeric ID, causing the honest participant's already-submitted vote to land on the attacker's proposal instead.

---

### Finding Description

`UpdateId` is defined as a plain `u64` wrapper with a simple increment generator:

```rust
// crates/contract/src/update.rs
pub struct UpdateId(pub(crate) u64);

impl UpdateId {
    pub fn generate(&mut self) -> Self {
        let id = self.0;
        self.0 += 1;
        Self(id)
    }
}
```

`ProposedUpdates::propose` assigns the next sequential ID to every new proposal:

```rust
pub fn propose(&mut self, update: Update) -> UpdateId {
    let id = self.id.generate();
    self.entries.insert(id, UpdateEntry { update, bytes_used });
    id
}
```

`vote_update` accepts a bare `UpdateId` integer and records the vote against whatever proposal currently occupies that slot:

```rust
pub fn vote_update(&mut self, id: UpdateId) -> Result<bool, Error> {
    ...
    if self.proposed_updates.vote(&id, voter).is_none() {
        return Err(InvalidParameters::UpdateNotFound.into());
    }
    ...
}
```

There is no binding between the numeric ID and the content of the proposal. A voter who calls `vote_update(5)` is voting for "whatever proposal has ID 5 at execution time," not for the specific code or config they reviewed off-chain.

**Attack scenario (NEAR short-fork):**

1. Honest participant A calls `propose_update(P_legit)` → lands in block B₁, receives `UpdateId = N`.
2. Honest participant B reviews P_legit off-chain and submits `vote_update(N)` → lands in block B₂ (B₂ > B₁).
3. A NEAR short-fork orphans B₁ (and consequently B₂). Both transactions are evicted from the canonical chain and returned to the mempool.
4. Attacker (a participant) immediately submits `propose_update(P_malicious)` → lands first in the new chain, receives `UpdateId = N` (the counter was rolled back with the state).
5. B's `vote_update(N)` transaction is re-broadcast from the mempool (NEAR clients automatically retry evicted transactions) and is included in the new chain → it now votes for `P_malicious`.
6. If the attacker casts their own vote and one additional redirected vote meets the governance threshold, `do_update` deploys `P_malicious`.

The README itself acknowledges the counter-collision concern in a different context: *"The update ID counter is preserved across migrations … to avoid race conditions where multiple participants might propose updates with colliding IDs immediately after an upgrade."* This confirms the developers are aware that numeric IDs can collide, but the re-org vector was not addressed.

Contrast with every other governance vote in the codebase, which uses content-addressed identifiers:
- `vote_new_parameters` → `ProposalHash` (Borsh hash of the full proposal)
- `vote_pk` / `vote_reshared` → `KeyEventId` (epoch + attempt + domain)
- `vote_code_hash` → the actual image hash is the key
- `vote_tee_verifier_change` → `ProposalHash` derived from `(candidate_account_id, expected_code_hash)`

`vote_update` is the sole outlier that uses a sequential integer.

---

### Impact Explanation

`do_update` either deploys arbitrary contract bytecode or replaces the live configuration:

```rust
Update::Contract(code) => {
    promise = promise.deploy_contract(code).function_call(
        method_names::MIGRATE, ...
    );
}
```

A successfully misdirected vote that reaches threshold causes the MPC contract itself to be replaced with attacker-controlled code. The MPC contract controls all threshold-signature issuance, key-share coordination, and fund flows for every chain the network supports. Deploying a malicious contract is equivalent to a complete takeover: the attacker can drain all funds, issue unauthorized signatures, or permanently freeze the network.

**Impact: Critical** — unauthorized contract execution / bypass of threshold-signature governance.

---

### Likelihood Explanation

- NEAR Nightshade does produce short forks (1–2 block depth) under normal operation; they are infrequent but documented and observed on mainnet.
- The attacker must be a current participant (required by `voter_or_panic`), which is a meaningful prerequisite but not an insurmountable one — participants are known entities who could turn adversarial.
- The attacker needs to win the race between the orphaned `propose_update` being evicted and their own `propose_update` being included before B's `vote_update` is re-applied. On NEAR, transaction retry is automatic and near-immediate, so the race window is narrow but real.
- The attack becomes easier when the governance threshold is low (e.g., 2-of-N) because fewer redirected votes are needed.
- The attacker can increase success probability by monitoring the mempool and pre-signing their `propose_update` transaction to broadcast it the instant a fork is detected.

**Likelihood: Low-Medium** — requires a re-org and a racing participant, but the consequence is catastrophic enough to warrant treatment as a critical risk.

---

### Recommendation

Replace the sequential `UpdateId` counter with a content-addressed identifier derived from the proposal payload, mirroring the pattern already used by every other governance vote in the contract:

```rust
// Derive UpdateId as a hash of the proposal content
impl ProposedUpdates {
    pub fn propose(&mut self, update: Update) -> UpdateId {
        let hash = env::sha256(&borsh::to_vec(&update).expect("borsh"));
        let id = UpdateId(u64::from_le_bytes(hash[..8].try_into().unwrap()));
        self.entries.insert(id, UpdateEntry { update, bytes_used });
        id
    }
}
```

Or, more idiomatically, adopt the existing `ProposalHash` primitive already used by `Votes<V>` throughout the codebase, so that `vote_update(id)` binds the voter to the exact bytes they reviewed rather than to a mutable numeric slot.

---

### Proof of Concept

**State before re-org (canonical chain fork A):**

| Block | Transaction | Contract state after |
|---|---|---|
| B₁ | A: `propose_update(P_legit)` | `entries[5] = P_legit`, `id.0 = 6` |
| B₂ | B: `vote_update(5)` | `vote_by_participant[B] = 5` |

**State after re-org (canonical chain fork B, B₁ and B₂ orphaned):**

| Block | Transaction | Contract state after |
|---|---|---|
| B₁′ | Attacker: `propose_update(P_malicious)` | `entries[5] = P_malicious`, `id.0 = 6` |
| B₂′ | B: `vote_update(5)` (re-broadcast) | `vote_by_participant[B] = 5` → votes for `P_malicious` |
| B₃′ | Attacker: `vote_update(5)` | `vote_by_participant[Attacker] = 5` → threshold met → `do_update(P_malicious)` executes |

The root cause is in: [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** crates/contract/src/update.rs (L44-49)
```rust
impl UpdateId {
    pub fn generate(&mut self) -> Self {
        let id = self.0;
        self.0 += 1;
        Self(id)
    }
```

**File:** crates/contract/src/update.rs (L167-174)
```rust
    pub fn propose(&mut self, update: Update) -> UpdateId {
        let bytes_used = bytes_used(&update);

        let id = self.id.generate();
        self.entries.insert(id, UpdateEntry { update, bytes_used });

        id
    }
```

**File:** crates/contract/src/lib.rs (L1343-1388)
```rust
    pub fn vote_update(&mut self, id: UpdateId) -> Result<bool, Error> {
        log!(
            "vote_update: signer={}, id={:?}",
            env::signer_account_id(),
            id,
        );

        let ProtocolContractState::Running(running_state) = &self.protocol_state else {
            env::panic_str("protocol must be in running state");
        };

        let threshold = self.threshold()?;

        let voter = self.voter_or_panic();
        if self.proposed_updates.vote(&id, voter).is_none() {
            return Err(InvalidParameters::UpdateNotFound.into());
        }

        // Filter votes to only count current participants voting for this specific update.
        // This ensures correctness even if the cleanup promise in MpcContract::vote_reshared() fails.
        let valid_votes_count = running_state
            .parameters
            .participants()
            .participants()
            .iter()
            .filter(|(account_id, _, _)| {
                self.proposed_updates
                    .vote_by_participant
                    .get(account_id)
                    .is_some_and(|voted_id| *voted_id == id)
            })
            .count();

        // Not enough votes from current participants, wait for more.
        if (valid_votes_count as u64) < threshold.value() {
            return Ok(false);
        }

        let update_gas_deposit = Gas::from_tgas(self.config.contract_upgrade_deposit_tera_gas);

        let Some(_promise) = self.proposed_updates.do_update(&id, update_gas_deposit) else {
            return Err(InvalidParameters::UpdateNotFound.into());
        };

        Ok(true)
    }
```
