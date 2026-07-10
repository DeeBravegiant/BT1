### Title
Cross-Chain Replay: `verify_foreign_transaction` Lacks Replay Protection, Enabling Double-Spend via Re-Submission of the Same Foreign Tx ID - (File: `crates/contract/src/lib.rs`, `crates/near-mpc-contract-interface/src/types/foreign_chain.rs`)

### Summary
The `verify_foreign_transaction` endpoint in the MPC contract does not prevent re-submission of the same foreign transaction after it has already been verified and signed. The `VerifyForeignTransactionRequest` key used to index pending requests contains no nonce, no caller identity, and no unique submission identifier. After `respond_verify_foreign_tx` drains the queue for a given `tx_id`, the same `tx_id` can be immediately re-submitted by any unprivileged NEAR account, causing MPC nodes to produce a second valid threshold signature over the same foreign transaction. Bridge contracts that rely on the MPC network as a trusted oracle and do not independently track consumed `tx_id`s are exposed to double-spend.

### Finding Description

The `sign()` method binds the request key to the caller's identity by computing a `tweak` from `(predecessor_id, path)`:

```rust
// crates/near-mpc-crypto-types/src/sign.rs:118-125
pub fn new(domain: DomainId, payload: Payload, predecessor_id: &AccountId, path: &str) -> Self {
    let tweak = crate::kdf::derive_tweak(predecessor_id, path);
    SignatureRequest { domain_id: domain, tweak, payload }
}
```

By contrast, `verify_foreign_transaction` constructs its request key via `args_into_verify_foreign_tx_request`, which is a direct field copy with **no caller identity and no nonce**:

```rust
// crates/contract/src/dto_mapping.rs:840-848
pub fn args_into_verify_foreign_tx_request(
    args: dtos::VerifyForeignTransactionRequestArgs,
) -> dtos::VerifyForeignTransactionRequest {
    dtos::VerifyForeignTransactionRequest {
        domain_id: args.domain_id,
        request: args.request,
        payload_version: args.payload_version,
    }
}
```

The resulting `VerifyForeignTransactionRequest` struct contains only `(request, domain_id, payload_version)`:

```rust
// crates/near-mpc-contract-interface/src/types/foreign_chain.rs:124-128
pub struct VerifyForeignTransactionRequest {
    pub request: ForeignChainRpcRequest,
    pub domain_id: DomainId,
    pub payload_version: ForeignTxPayloadVersion,
}
```

This struct is used as the map key in `pending_verify_foreign_tx_requests`. When `respond_verify_foreign_tx` is called, `resolve_yields_for` removes the entry entirely:

```rust
// crates/contract/src/pending_requests.rs:74-81
let resumed = requests
    .remove(request)
    .unwrap_or_default()
    .into_iter()
    .map(|YieldIndex { data_id }| {
        env::promise_yield_resume(&data_id, response_bytes.clone());
    })
    .count();
```

After the entry is removed, the contract holds **no record** that this `(tx_id, domain_id, payload_version)` tuple was ever processed. The `MpcContract` state has no "processed requests" set. A subsequent call to `verify_foreign_transaction` with the same arguments is accepted unconditionally, enqueues a new yield, and MPC nodes treat it as a fresh request (each NEAR receipt gets a unique `receipt_id` and thus a unique node-side `VerifyForeignTxId`).

The codebase itself acknowledges the caller-agnostic nature of this key in a test comment:

```
// crates/contract/src/lib.rs:3255
// Then: both yields are queued under the single (caller-agnostic) request key.
```

The `verify_foreign_transaction` function performs no replay check before enqueuing:

```rust
// crates/contract/src/lib.rs:519-557
pub fn verify_foreign_transaction(&mut self, request: VerifyForeignTransactionRequestArgs) {
    self.check_request_preconditions(...);  // gas, deposit, domain, accept_requests only
    // no check: has this tx_id already been verified?
    let request = args_into_verify_foreign_tx_request(request);
    self.enqueue_yield_request(..., move |this, id| this.add_verify_foreign_tx_request(request, id));
}
```

### Impact Explanation

The primary use case for `verify_foreign_transaction` is the Omnibridge inbound flow: a bridge service submits a foreign-chain deposit `tx_id`, MPC nodes verify it on-chain and return a threshold signature, and the bridge contract uses that signature to mint or release tokens on NEAR. If the same `tx_id` can be re-submitted after the first response, MPC nodes produce a second valid threshold signature over the same foreign transaction. Any bridge contract that does not independently track consumed `tx_id`s will accept this second signature and release tokens a second time, constituting a direct double-spend of bridged funds.

The signature in `VerifyForeignTransactionResponse` is verified against the **root public key** (not a per-caller derived key), making it universally verifiable by any bridge contract:

```rust
// crates/contract/src/lib.rs:728-734
near_mpc_signature_verifier::verify_ecdsa_signature(
    signature_response,
    &payload_hash,
    &secp_pk,  // root public key, not caller-derived
)
.is_ok()
```

This matches the allowed impact: **High — Cross-chain replay that causes invalid bridge execution or double-spend conditions.**

### Likelihood Explanation

The attacker path requires no privileged access, no threshold collusion, and no key material:

1. Observe a legitimate `verify_foreign_transaction` call for a deposit `tx_id` complete successfully.
2. Re-submit `verify_foreign_transaction` with the identical `(tx_id, domain_id, payload_version)` arguments. Only a 1-yoctoNEAR deposit and sufficient gas are required.
3. MPC nodes process the re-submission as a new request (new `receipt_id`, new node-side `VerifyForeignTxId`) and produce a second valid threshold signature.
4. Submit the second signature to the bridge contract to claim tokens again.

Any unprivileged NEAR account can execute steps 1–4. The only external dependency is that the bridge contract does not independently enforce `tx_id` uniqueness — a reasonable assumption for contracts that treat the MPC network as the authoritative replay-prevention layer.

### Recommendation

Add a `processed_verify_foreign_tx_requests: LookupSet<VerifyForeignTransactionRequest>` (or a set keyed on a canonical hash of the request) to `MpcContract` state. In `respond_verify_foreign_tx`, after a successful response, insert the request key into this set. In `verify_foreign_transaction`, before enqueuing, check whether the request key is already in the processed set and panic with a typed error if so.

Alternatively, include the NEAR receipt ID (available via `env::current_account_id()` context or passed explicitly) in the `VerifyForeignTransactionRequest` key so that each submission is inherently unique, while still allowing the bridge contract to correlate responses to specific submissions.

### Proof of Concept

```
1. Alice (bridge service) calls verify_foreign_transaction({tx_id: 0xABCD..., domain_id: 0, payload_version: V1}).
   → MPC nodes verify tx 0xABCD on Bitcoin, call respond_verify_foreign_tx with a valid signature.
   → resolve_yields_for removes the entry from pending_verify_foreign_tx_requests.
   → Alice's bridge contract receives the signature and releases 1 BTC worth of tokens.

2. Attacker calls verify_foreign_transaction({tx_id: 0xABCD..., domain_id: 0, payload_version: V1}).
   → No replay check exists; push_pending_yield creates a new queue entry.
   → MPC nodes see a new receipt_id, treat it as a fresh VerifyForeignTxRequest, verify the same tx again.
   → respond_verify_foreign_tx is called with a second valid signature over the same payload_hash.
   → Attacker submits this second signature to the bridge contract and receives another 1 BTC worth of tokens.

Root cause: verify_foreign_transaction (lib.rs:519) performs no check against a processed-requests set.
             VerifyForeignTransactionRequest (foreign_chain.rs:124) contains no nonce or caller identity.
             resolve_yields_for (pending_requests.rs:74) removes the pending entry with no tombstone.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** crates/contract/src/lib.rs (L149-173)
```rust
#[near(contract_state)]
#[derive(Debug)]
pub struct MpcContract {
    protocol_state: ProtocolContractState,
    pending_signature_requests: LookupMap<SignatureRequest, Vec<YieldIndex>>,
    pending_ckd_requests: LookupMap<CKDRequest, Vec<YieldIndex>>,
    pending_verify_foreign_tx_requests: LookupMap<VerifyForeignTransactionRequest, Vec<YieldIndex>>,
    proposed_updates: ProposedUpdates,
    // TODO(#3475): drop this once we upgrade the contract and nodes start using
    // the new API.
    node_foreign_chain_support: SupportedForeignChainsByNode,
    config: Config,
    tee_state: TeeState,
    accept_requests: bool,
    node_migrations: NodeMigrations,
    // TODO(#2937): Remove via state migration.
    metrics: Metrics,
    foreign_chains: Lazy<ForeignChainsMetadata>,
    /// The verifier contract account trusted for DCAP verification, or [`None`]
    /// until participants vote one in. Not yet used to dispatch verification.
    // TODO(#3639): once participants have voted a verifier in, make this
    // non-optional via a migration that requires it be set.
    tee_verifier_account_id: Option<AccountId>,
    tee_verifier_votes: TeeVerifierVotes,
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

**File:** crates/contract/src/lib.rs (L718-734)
```rust
        let signature_is_valid = match (&response.signature, public_key) {
            (
                dtos::SignatureResponse::Secp256k1(signature_response),
                PublicKeyExtended::Secp256k1 { near_public_key },
            ) => {
                let secp_pk = dtos::Secp256k1PublicKey::try_from(&near_public_key)
                    .expect("Secp256k1 variant always has a secp256k1 key");

                let payload_hash: [u8; 32] = response.payload_hash.0;

                // Check the signature is correct against the root public key
                near_mpc_signature_verifier::verify_ecdsa_signature(
                    signature_response,
                    &payload_hash,
                    &secp_pk,
                )
                .is_ok()
```

**File:** crates/contract/src/dto_mapping.rs (L840-848)
```rust
pub fn args_into_verify_foreign_tx_request(
    args: dtos::VerifyForeignTransactionRequestArgs,
) -> dtos::VerifyForeignTransactionRequest {
    dtos::VerifyForeignTransactionRequest {
        domain_id: args.domain_id,
        request: args.request,
        payload_version: args.payload_version,
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
