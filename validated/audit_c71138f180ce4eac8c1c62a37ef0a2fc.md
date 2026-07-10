### Title
Caller-Identity-Free `VerifyForeignTransactionRequest` Key Enables Targeted Queue-Slot Hijacking DoS — (File: `crates/contract/src/lib.rs`)

---

### Summary

The `verify_foreign_transaction` endpoint stores pending requests under a key (`VerifyForeignTransactionRequest`) that contains **no caller identity**, unlike `sign` (which embeds a caller-derived `tweak`) and `request_app_private_key` (which embeds a caller-derived `app_id`). Because all callers submitting the same foreign-chain transaction share one queue slot, an unprivileged attacker can fill that slot to the hard cap of 128 (`MAX_PENDING_REQUEST_FAN_OUT`) with their own yield-resume promises, causing every subsequent legitimate submission for that transaction to be rejected with `PendingRequestQueueFull`. The attacker also receives the MPC signature as a side-effect, enabling potential front-running of the victim's bridge operation.

---

### Finding Description

**Asymmetric key design across the three request types:**

`SignatureRequest` is keyed by `(domain_id, payload, tweak)` where `tweak = derive_tweak(predecessor_id, path)`: [1](#0-0) 

`CKDRequest` is keyed by `(app_public_key, app_id, domain_id)` where `app_id = derive_app_id(predecessor_id, derivation_path)`: [2](#0-1) 

`VerifyForeignTransactionRequest` is keyed by only `(request, domain_id, payload_version)` — **no caller field**: [3](#0-2) 

The `verify_foreign_transaction` handler converts the user-supplied args directly to this caller-agnostic key and enqueues a yield under it: [4](#0-3) 

The fan-out queue is bounded at 128 entries per key. Once full, any further push panics: [5](#0-4) 

When `respond_verify_foreign_tx` is called, the entire queue is drained in one pass, resuming all 128 yields — including the attacker's: [6](#0-5) 

**Attack flow:**

1. Victim submits `verify_foreign_transaction({chain_tx: T, domain_id: D, payload_version: V1})`.
2. Attacker observes the mempool or the on-chain event and immediately submits the identical request 128 times (cost: 128 × 1 yoctoNEAR + gas).
3. The queue for key `(T, D, V1)` reaches `MAX_PENDING_REQUEST_FAN_OUT = 128`.
4. Victim's transaction panics with `PendingRequestQueueFull`; the victim's yield is never created.
5. MPC nodes call `respond_verify_foreign_tx`; all 128 attacker yields are resumed and the attacker receives 128 copies of the MPC signature.
6. Attacker can use the signature to front-run the victim's bridge redemption on the foreign chain (if the bridge contract does not enforce caller identity on the signature consumer side).
7. Attacker repeats from step 2 in the next block to sustain the DoS indefinitely.

---

### Impact Explanation

This is a **Medium** impact: request-lifecycle manipulation that breaks the production safety invariant that any user who pays the required deposit and gas can eventually have their `verify_foreign_transaction` request processed. The attacker can sustain the DoS at low cost (128 yoctoNEAR + gas per MPC response cycle). Additionally, the attacker receives the MPC signature for the foreign-chain transaction, which — depending on the consuming bridge contract's access-control design — may allow them to claim the victim's bridged assets, elevating the impact toward Critical in deployed bridge integrations. [4](#0-3) 

---

### Likelihood Explanation

The attack requires only:
- Knowledge of the target foreign-chain transaction ID (observable on the foreign chain or from the victim's mempool transaction).
- Ability to submit 128 NEAR transactions before the victim's transaction is included (or in the same block via batch submission).
- A deposit of 128 yoctoNEAR plus gas — negligible cost.

No privileged access, key material, or threshold collusion is required. Any unprivileged NEAR account can execute this. [7](#0-6) 

---

### Recommendation

Include the caller's account identity in the stored `VerifyForeignTransactionRequest` key, mirroring the pattern used by `sign` and `request_app_private_key`:

- For `sign`: add `tweak = derive_tweak(predecessor_id, path)` to the key.
- For `request_app_private_key`: add `app_id = derive_app_id(predecessor_id, derivation_path)` to the key.
- For `verify_foreign_transaction`: add a `tweak = derive_tweak(predecessor_id, derivation_path)` field (or simply the raw `predecessor_id`) to `VerifyForeignTransactionRequest` so that each caller's request occupies its own independent queue slot.

This ensures that an attacker filling their own 128-slot queue cannot block a different caller's slot. [8](#0-7) [9](#0-8) 

---

### Proof of Concept

```
// Pseudocode — executable as a NEAR sandbox test analogous to the Solidity PoC

let victim = "alice.near";
let attacker = "bob.near";
let target_tx = BitcoinRpcRequest { tx_id: [0xAA; 32], confirmations: 1, extractors: [BlockHash] };
let request_args = VerifyForeignTransactionRequestArgs {
    request: ForeignChainRpcRequest::Bitcoin(target_tx),
    domain_id: foreign_tx_domain_id,
    payload_version: ForeignTxPayloadVersion::V1,
};

// Attacker fills the queue to MAX_PENDING_REQUEST_FAN_OUT = 128
for _ in 0..128 {
    attacker.call(contract, "verify_foreign_transaction")
        .args_json({ "request": request_args })
        .deposit(1 yoctoNEAR)
        .max_gas()
        .transact_async();   // fire-and-forget; each creates one yield slot
}

// Victim's request is now rejected
let result = victim.call(contract, "verify_foreign_transaction")
    .args_json({ "request": request_args })
    .deposit(1 yoctoNEAR)
    .max_gas()
    .transact()
    .await;

// result.is_failure() == true, error: PendingRequestQueueFull { limit: 128 }
// Attacker receives 128 copies of the MPC signature when nodes respond.
``` [10](#0-9) [4](#0-3)

### Citations

**File:** crates/near-mpc-crypto-types/src/sign.rs (L111-125)
```rust
pub struct SignatureRequest {
    pub tweak: Tweak,
    pub payload: Payload,
    pub domain_id: DomainId,
}

impl SignatureRequest {
    pub fn new(domain: DomainId, payload: Payload, predecessor_id: &AccountId, path: &str) -> Self {
        let tweak = crate::kdf::derive_tweak(predecessor_id, path);
        SignatureRequest {
            domain_id: domain,
            tweak,
            payload,
        }
    }
```

**File:** crates/contract/src/primitives/ckd.rs (L10-31)
```rust
pub struct CKDRequest {
    /// The app ephemeral public key
    pub app_public_key: dtos::CKDAppPublicKey,
    pub app_id: dtos::CkdAppId,
    pub domain_id: DomainId,
}

impl CKDRequest {
    pub fn new(
        app_public_key: dtos::CKDAppPublicKey,
        domain_id: DomainId,
        predecessor_id: &AccountId,
        derivation_path: &str,
    ) -> Self {
        let app_id = derive_app_id(predecessor_id, derivation_path);
        Self {
            app_public_key,
            app_id,
            domain_id,
        }
    }
}
```

**File:** crates/near-mpc-contract-interface/src/types/foreign_chain.rs (L124-128)
```rust
pub struct VerifyForeignTransactionRequest {
    pub request: ForeignChainRpcRequest,
    pub domain_id: DomainId,
    pub payload_version: ForeignTxPayloadVersion,
}
```

**File:** crates/contract/src/lib.rs (L519-557)
```rust
    pub fn verify_foreign_transaction(&mut self, request: VerifyForeignTransactionRequestArgs) {
        log!(
            "verify_foreign_transaction: predecessor={:?}, request={:?}",
            env::predecessor_account_id(),
            request
        );

        self.check_request_preconditions(
            request.domain_id,
            DomainPurpose::ForeignTx,
            Gas::from_tgas(self.config.sign_call_gas_attachment_requirement_tera_gas),
            MINIMUM_SIGN_REQUEST_DEPOSIT,
        );

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

        let callback_gas = Gas::from_tgas(
            self.config
                .return_signature_and_clean_state_on_success_call_tera_gas,
        );

        let request = args_into_verify_foreign_tx_request(request);
        let callback_args = serde_json::to_vec(&(&request,)).unwrap();
        self.enqueue_yield_request(
            method_names::RETURN_VERIFY_FOREIGN_TX_AND_CLEAN_STATE_ON_SUCCESS,
            callback_args,
            callback_gas,
            move |this, id| this.add_verify_foreign_tx_request(request, id),
        );
    }
```

**File:** crates/contract/src/pending_requests.rs (L24-59)
```rust
/// Maximum number of concurrent yield-resume promises that can be queued for a single
/// request key (i.e. the number of duplicate submissions whose responses fan out from
/// one MPC reply).
///
/// The ceiling is needed because `respond*` drains the entire queue in one call: every
/// queued yield triggers a host-side `promise_yield_resume`, paid for out of the
/// responder's 300 TGas budget. Without a cap, an attacker could enqueue enough
/// duplicates to make `respond*` run out of gas and strand every queued caller.
///
/// 128 is validated empirically by the sandbox test
/// `test_contract_request_duplicate_requests_fan_out`, which fills the queue to this
/// cap across all four signature schemes and confirms `respond*` drains it inside its
/// 300 TGas budget.
pub const MAX_PENDING_REQUEST_FAN_OUT: u8 = 128;

/// Append a yield index to the pending-request fan-out queue for `request`.
///
/// Panics with `RequestError::PendingRequestQueueFull` if the resulting queue would
/// exceed `MAX_PENDING_REQUEST_FAN_OUT`.
pub(crate) fn push_pending_yield<K>(
    requests: &mut LookupMap<K, Vec<YieldIndex>>,
    request: K,
    data_id: CryptoHash,
) where
    K: BorshSerialize + BorshDeserialize + Clone + Ord,
{
    let queue = requests.entry(request).or_default();
    if queue.len() >= usize::from(MAX_PENDING_REQUEST_FAN_OUT) {
        env::panic_str(
            &RequestError::PendingRequestQueueFull {
                limit: MAX_PENDING_REQUEST_FAN_OUT,
            }
            .to_string(),
        );
    }
    queue.push(YieldIndex { data_id });
```

**File:** crates/contract/src/pending_requests.rs (L66-88)
```rust
pub(crate) fn resolve_yields_for<K>(
    requests: &mut LookupMap<K, Vec<YieldIndex>>,
    request: &K,
    response_bytes: Vec<u8>,
) -> Result<(), Error>
where
    K: BorshSerialize + BorshDeserialize + Clone + Ord,
{
    let resumed = requests
        .remove(request)
        .unwrap_or_default()
        .into_iter()
        .map(|YieldIndex { data_id }| {
            env::promise_yield_resume(&data_id, response_bytes.clone());
        })
        .count();

    if resumed > 0 {
        Ok(())
    } else {
        Err(InvalidParameters::RequestNotFound.into())
    }
}
```
