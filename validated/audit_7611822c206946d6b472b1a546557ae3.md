### Title
Caller-Identity-Free `verify_foreign_transaction` Allows Any Unprivileged Account to Receive the Same MPC Threshold Signature as the Legitimate Bridge User - (File: crates/contract/src/lib.rs)

### Summary

`verify_foreign_transaction` does not bind the pending-request queue key to the caller's NEAR account identity. Any unprivileged account can submit the identical foreign-chain transaction request and receive the same MPC threshold signature that was intended exclusively for the legitimate bridge initiator. This is the direct analog of the flash-loan griefing pattern: just as an arbitrary address could be passed as `receiverAddress` to execute a flash loan on behalf of another user, here any arbitrary NEAR account can piggyback on another user's foreign-chain verification request and receive the resulting signature.

---

### Finding Description

**Root cause — `verify_foreign_transaction` discards the caller identity and stores a caller-agnostic request key.**

In `sign` and `request_app_private_key`, the caller's `predecessor` account ID is captured from `check_request_preconditions` and baked into the request key via a cryptographic tweak or app-id derivation:

```rust
// sign — predecessor IS bound into the key
let (domain_config, predecessor) = self.check_request_preconditions(...);
let request = SignatureRequest::new(
    request.domain_id, request.payload,
    &predecessor,       // ← tweak = H(predecessor || path)
    &request.path,
);
``` [1](#0-0) 

```rust
// request_app_private_key — predecessor IS bound into the key
let (_, predecessor) = self.check_request_preconditions(...);
let request = CKDRequest::new(
    request.app_public_key, domain_id,
    &predecessor,           // ← app_id = H(predecessor || derivation_path)
    &request.derivation_path,
);
``` [2](#0-1) 

`verify_foreign_transaction` calls the same precondition helper but **silently discards its return value**, including the `predecessor`:

```rust
pub fn verify_foreign_transaction(&mut self, request: VerifyForeignTransactionRequestArgs) {
    self.check_request_preconditions(   // ← return value (DomainConfig, AccountId) is dropped
        request.domain_id,
        DomainPurpose::ForeignTx,
        ...
    );
    // predecessor is never used again
    let request = args_into_verify_foreign_tx_request(request);
    self.enqueue_yield_request(..., move |this, id| this.add_verify_foreign_tx_request(request, id));
}
``` [3](#0-2) 

The resulting `VerifyForeignTransactionRequest` struct — which is the map key in `pending_verify_foreign_tx_requests` — contains no caller field:

```rust
pub struct VerifyForeignTransactionRequest {
    pub request: ForeignChainRpcRequest,
    pub domain_id: DomainId,
    pub payload_version: ForeignTxPayloadVersion,
    // ← no tweak, no predecessor, no caller binding
}
``` [4](#0-3) 

The `args_into_verify_foreign_tx_request` mapping function confirms no caller identity is injected:

```rust
pub fn args_into_verify_foreign_tx_request(
    args: dtos::VerifyForeignTransactionRequestArgs,
) -> dtos::VerifyForeignTransactionRequest {
    dtos::VerifyForeignTransactionRequest {
        domain_id: args.domain_id,
        request: args.request,
        payload_version: args.payload_version,
        // ← predecessor never appears
    }
}
``` [5](#0-4) 

**The signature is verified against the root public key with no tweak.** `respond_verify_foreign_tx` verifies the MPC response against `secp_pk` (the raw root key), not a caller-derived key:

```rust
// Check the signature is correct against the root public key
near_mpc_signature_verifier::verify_ecdsa_signature(
    signature_response,
    &payload_hash,
    &secp_pk,   // ← root key, no tweak, no caller binding
)
``` [6](#0-5) 

**The existing test explicitly documents the caller-agnostic fan-out behavior:**

```rust
// And: caller bob submits the identical request — a different account would today
// be blocked from receiving a response by alice's submission.
contract.verify_foreign_transaction(request_args);

// Then: both yields are queued under the single (caller-agnostic) request key.
assert_eq!(
    contract.pending_verify_foreign_tx_requests.get(&request).map(|q| q.len()),
    Some(2),
    "duplicate foreign-tx requests from different callers should fan out",
);
``` [7](#0-6) 

**The design documentation acknowledges the missing tweak derivation.** `docs/foreign-chain-transactions.md` describes a planned `derivation_path` field and a `derive_foreign_tx_tweak(predecessor_id, derivation_path)` function — but neither exists in the current `VerifyForeignTransactionRequestArgs` struct or the contract implementation: [8](#0-7) 

---

### Impact Explanation

An attacker who observes (or front-runs) a victim's `verify_foreign_transaction` call submits the identical `{ tx_id, domain_id, payload_version }` with only 1 yoctonear deposit. Both yields are queued under the same caller-agnostic key. When MPC nodes call `respond_verify_foreign_tx`, `resolve_yields_for` drains the entire queue and delivers the **same threshold signature** to every queued caller — including the attacker. [9](#0-8) 

The delivered signature is a valid MPC threshold signature over the foreign-chain transaction's payload hash, produced under the network's root key. Any bridge or application layer that uses this signature to authorize a NEAR-side action (e.g., releasing bridged funds, minting wrapped tokens, or crediting an account) without independently verifying that the presenter is the original depositor will process the attacker's claim identically to the victim's. This constitutes unauthorized threshold signature issuance to an unprivileged caller and enables invalid bridge execution or double-spend conditions.

---

### Likelihood Explanation

- The foreign chain transaction ID is public on-chain; the attacker needs no privileged information.
- The cost to the attacker is 1 yoctonear plus gas (~10 Tgas), negligible on NEAR.
- The attacker can monitor the NEAR mempool or the foreign chain and submit the identical request in the same or the next block.
- No threshold collusion, TEE compromise, or operator access is required — a single unprivileged NEAR account suffices.

---

### Recommendation

Bind the `VerifyForeignTransactionRequest` key to the caller's identity, mirroring the pattern used by `sign` and `request_app_private_key`:

1. Add a `derivation_path: String` field to `VerifyForeignTransactionRequestArgs` (as already described in `docs/foreign-chain-transactions.md`).
2. Capture `predecessor` from `check_request_preconditions` in `verify_foreign_transaction`.
3. Derive a caller-specific tweak (using a distinct prefix, e.g. `"near-mpc-recovery v0.1.0 foreign-tx epsilon derivation:"`) and store it in `VerifyForeignTransactionRequest`.
4. In `respond_verify_foreign_tx`, verify the signature against the **derived** public key (applying the stored tweak), not the root key.

This ensures each caller's request is keyed and signed independently, so an attacker submitting the same foreign-chain tx ID receives a signature under a different derived key — one that cannot be used to impersonate the victim.

---

### Proof of Concept

```
1. Alice calls verify_foreign_transaction({
       request: Bitcoin { tx_id: [0xAB; 32], confirmations: 6, extractors: [BlockHash] },
       domain_id: 0,
       payload_version: V1,
   }) with 1 yoctonear attached.
   → Alice's yield Y_alice is stored under key K = { request, domain_id, payload_version }.

2. Attacker Bob calls verify_foreign_transaction with the identical arguments.
   → Bob's yield Y_bob is appended to the same queue under K.
   → pending_verify_foreign_tx_requests[K] = [Y_alice, Y_bob]

3. MPC nodes verify the Bitcoin transaction and call respond_verify_foreign_tx(K, σ).
   → resolve_yields_for drains [Y_alice, Y_bob], delivering σ to both.

4. Bob now holds σ — a valid MPC threshold signature over the Bitcoin tx payload hash,
   produced under the network root key — identical to Alice's.

5. If the bridge contract releases funds to whoever presents σ, Bob claims Alice's deposit.
```

The test `verify_foreign_transaction__should_queue_duplicates_from_different_callers` in `crates/contract/src/lib.rs` already demonstrates steps 1–3 passing without error. [10](#0-9)

### Citations

**File:** crates/contract/src/lib.rs (L352-384)
```rust
        let (domain_config, predecessor) = self.check_request_preconditions(
            request.domain_id,
            DomainPurpose::Sign,
            Gas::from_tgas(self.config.sign_call_gas_attachment_requirement_tera_gas),
            MINIMUM_SIGN_REQUEST_DEPOSIT,
        );

        // ensure the signer sent a valid signature request
        // It's important we fail here because the MPC nodes will fail in an identical way.
        // This allows users to get the error message
        match domain_config.protocol {
            Protocol::CaitSith | Protocol::DamgardEtAl => {
                let hash = *request.payload.as_ecdsa().expect("Payload is not Ecdsa");
                k256::Scalar::from_repr(hash.into())
                    .into_option()
                    .expect("Ecdsa payload cannot be converted to Scalar");
            }
            Protocol::Frost => {
                request.payload.as_eddsa().expect("Payload is not EdDSA");
            }
            Protocol::ConfidentialKeyDerivation => {
                env::panic_str(
                    "ConfidentialKeyDerivation is not supported for signature responses",
                );
            }
        }

        let request = SignatureRequest::new(
            request.domain_id,
            request.payload,
            &predecessor,
            &request.path,
        );
```

**File:** crates/contract/src/lib.rs (L477-498)
```rust
        let (_, predecessor) = self.check_request_preconditions(
            domain_id,
            DomainPurpose::CKD,
            Gas::from_tgas(self.config.ckd_call_gas_attachment_requirement_tera_gas),
            MINIMUM_CKD_REQUEST_DEPOSIT,
        );

        match &request.app_public_key {
            dtos::CKDAppPublicKey::AppPublicKey(_) => {}
            dtos::CKDAppPublicKey::AppPublicKeyPV(pk) => {
                if !app_public_key_check(pk) {
                    env::panic_str("app public key check failed")
                }
            }
        }

        let request = CKDRequest::new(
            request.app_public_key,
            domain_id,
            &predecessor,
            &request.derivation_path,
        );
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

**File:** crates/contract/src/lib.rs (L728-734)
```rust
                // Check the signature is correct against the root public key
                near_mpc_signature_verifier::verify_ecdsa_signature(
                    signature_response,
                    &payload_hash,
                    &secp_pk,
                )
                .is_ok()
```

**File:** crates/contract/src/lib.rs (L3208-3298)
```rust
    #[test]
    fn verify_foreign_transaction__should_queue_duplicates_from_different_callers() {
        // Given: two different callers will submit the same foreign-tx verification request.
        let mut rng = rand::rngs::StdRng::from_seed([42u8; 32]);
        let (context, mut contract, secret_key) =
            basic_setup_with_protocol(Protocol::CaitSith, DomainPurpose::ForeignTx, &mut rng);
        register_supported_chains(&mut contract, [dtos::ForeignChain::Bitcoin]);
        let SharedSecretKey::Secp256k1(secret_key) = secret_key else {
            unreachable!();
        };

        let request_args = VerifyForeignTransactionRequestArgs {
            domain_id: DomainId::default().0.into(),
            payload_version: ForeignTxPayloadVersion::V1,
            request: dtos::ForeignChainRpcRequest::Bitcoin(BitcoinRpcRequest {
                tx_id: [7u8; 32].into(),
                confirmations: 2.into(),
                extractors: vec![BitcoinExtractor::BlockHash],
            }),
        };
        let request = args_into_verify_foreign_tx_request(request_args.clone());

        // When: caller alice submits the request.
        let alice = AccountId::from_str("alice.near").unwrap();
        testing_env!(
            VMContextBuilder::new()
                .signer_account_id(alice.clone())
                .predecessor_account_id(alice)
                .current_account_id(context.current_account_id.clone())
                .attached_deposit(NearToken::from_yoctonear(1))
                .build()
        );
        contract.verify_foreign_transaction(request_args.clone());

        // And: caller bob submits the identical request — a different account would today
        // be blocked from receiving a response by alice's submission.
        let bob = AccountId::from_str("bob.near").unwrap();
        testing_env!(
            VMContextBuilder::new()
                .signer_account_id(bob.clone())
                .predecessor_account_id(bob)
                .current_account_id(context.current_account_id.clone())
                .attached_deposit(NearToken::from_yoctonear(1))
                .build()
        );
        contract.verify_foreign_transaction(request_args);

        // Then: both yields are queued under the single (caller-agnostic) request key.
        assert_eq!(
            contract
                .pending_verify_foreign_tx_requests
                .get(&request)
                .map(|q| q.len()),
            Some(2),
            "duplicate foreign-tx requests from different callers should fan out",
        );

        // When: a single valid response is delivered.
        let payload = ForeignTxSignPayload::V1(ForeignTxSignPayloadV1 {
            request: request.request.clone(),
            values: vec![ExtractedValue::BitcoinExtractedValue(
                BitcoinExtractedValue::BlockHash([42u8; 32].into()),
            )],
        });
        let payload_hash_arr = payload.compute_msg_hash().unwrap().0;
        let secret_key_ec: elliptic_curve::SecretKey<Secp256k1> =
            elliptic_curve::SecretKey::from_bytes(&secret_key.to_bytes()).unwrap();
        let signing_key = SigningKey::from_bytes(&secret_key_ec.to_bytes()).unwrap();
        let (signature, recovery_id) = signing_key
            .sign_prehash_recoverable(&payload_hash_arr)
            .unwrap();
        let response = VerifyForeignTransactionResponse {
            payload_hash: payload.compute_msg_hash().unwrap(),
            signature: dtos::SignatureResponse::Secp256k1(
                dtos::K256Signature::from_ecdsa_recoverable(&signature, recovery_id),
            ),
        };

        with_active_participant_and_attested_context(&contract);
        contract
            .respond_verify_foreign_tx(request.clone(), response)
            .expect("respond_verify_foreign_tx should succeed");

        // Then: both queued yields are drained from the single map entry.
        assert!(
            contract
                .pending_verify_foreign_tx_requests
                .get(&request)
                .is_none()
        );
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

**File:** docs/foreign-chain-transactions.md (L254-286)
```markdown
## Tweak Derivation (Sign vs ForeignTx)

`verify_foreign_transaction()` uses a **different tweak derivation prefix** than `sign()` so the same
`(predecessor_id, derivation_path)` can never yield the same derived key across the two purposes.

Design:

* Keep the existing sign tweak derivation prefix unchanged.
* Introduce a foreign-tx-specific prefix and derive the tweak from the same `(predecessor_id, derivation_path)`
  input using the same hash construction.
* The contract derives the tweak internally from `request.derivation_path` (callers do not submit raw tweaks).

Example:

```rust
const SIGN_TWEAK_DERIVATION_PREFIX: &str =
    "near-mpc-recovery v0.1.0 epsilon derivation:";
const FOREIGN_TX_TWEAK_DERIVATION_PREFIX: &str =
    "near-mpc-recovery v0.1.0 foreign-tx epsilon derivation:";

pub fn derive_sign_tweak(predecessor_id: &AccountId, path: &str) -> Tweak {
    let hash: [u8; 32] = derive_from_path(SIGN_TWEAK_DERIVATION_PREFIX, predecessor_id, path);
    Tweak::new(hash)
}

pub fn derive_foreign_tx_tweak(predecessor_id: &AccountId, path: &str) -> Tweak {
    let hash: [u8; 32] = derive_from_path(FOREIGN_TX_TWEAK_DERIVATION_PREFIX, predecessor_id, path);
    Tweak::new(hash)
}
```

This ensures key material used for validated foreign transactions is **always** distinct from
general-purpose `sign()` keys, even if the same account and derivation path are reused.
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
