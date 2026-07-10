### Title
`verify_foreign_transaction` Discards Caller Identity from Request Key, Enabling Cross-Chain Response Theft and Double-Spend - (`crates/contract/src/lib.rs`)

---

### Summary

`verify_foreign_transaction` calls `check_request_preconditions`, which returns the caller's `predecessor_account_id`, but **silently discards it**. The resulting `VerifyForeignTransactionRequest` key stored in `pending_verify_foreign_tx_requests` contains no caller identity. Any unprivileged account can submit the identical request, be queued under the same key, and receive the same MPC-signed response — a response signed with the **root key** (no caller-specific tweak). This enables cross-chain replay and forged foreign-chain verification that causes invalid bridge execution or double-spend conditions.

---

### Finding Description

In `sign()` and `request_app_private_key()`, the caller's `predecessor` is explicitly bound into the request key and into the key-derivation tweak:

```rust
// sign() — lib.rs:379-384
let request = SignatureRequest::new(
    request.domain_id,
    request.payload,
    &predecessor,      // ← caller identity bound into key
    &request.path,
);
```

```rust
// request_app_private_key() — lib.rs:493-498
let request = CKDRequest::new(
    request.app_public_key,
    domain_id,
    &predecessor,      // ← caller identity bound into key
    &request.derivation_path,
);
```

In `verify_foreign_transaction()`, `check_request_preconditions` returns `(domain_config, predecessor)`, but the `predecessor` is **thrown away**:

```rust
// lib.rs:526-531
self.check_request_preconditions(
    request.domain_id,
    DomainPurpose::ForeignTx,
    Gas::from_tgas(self.config.sign_call_gas_attachment_requirement_tera_gas),
    MINIMUM_SIGN_REQUEST_DEPOSIT,
);   // ← return value (domain_config, predecessor) discarded entirely
```

The request is then converted with `args_into_verify_foreign_tx_request`, which produces a struct containing only `{request, domain_id, payload_version}` — no caller field:

```rust
// dto_mapping.rs:840-848
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

The `VerifyForeignTransactionRequest` struct itself confirms no caller field exists:

```rust
// near-mpc-contract-interface/src/types/foreign_chain.rs:124-128
pub struct VerifyForeignTransactionRequest {
    pub request: ForeignChainRpcRequest,
    pub domain_id: DomainId,
    pub payload_version: ForeignTxPayloadVersion,
}
```

In `respond_verify_foreign_tx`, the signature is verified against the **root public key** with no tweak — confirming the response is not caller-specific:

```rust
// lib.rs:729-734
near_mpc_signature_verifier::verify_ecdsa_signature(
    signature_response,
    &payload_hash,
    &secp_pk,   // ← root key, no derivation, no caller tweak
)
.is_ok()
```

The codebase itself acknowledges the caller-agnostic fan-out behavior in a test comment:

> "a different account would today be blocked from receiving a response by alice's submission" — `lib.rs:3242-3243`

This confirms that any caller submitting the same `{tx_id, chain, domain_id, payload_version}` tuple is queued under the same key and receives the same root-key-signed response.

---

### Impact Explanation

The MPC network produces a signature over `(ForeignChainRpcRequest, extracted_values)` using the **root key** with no caller-specific derivation. This signature is not bound to the account that submitted the request.

**Attack scenario (bridge double-spend)**:
1. Alice sends 1 ETH to a bridge contract on Ethereum, specifying her NEAR account as the recipient.
2. Alice calls `verify_foreign_transaction` on NEAR to obtain an MPC-signed attestation of the Ethereum transfer.
3. Attacker observes Alice's pending request on-chain (all NEAR transactions are public).
4. Attacker submits the identical `verify_foreign_transaction` request (same `tx_id`, chain, `domain_id`, `payload_version`) with 1 yoctoNEAR.
5. Both Alice and the attacker are queued under the same `VerifyForeignTransactionRequest` key.
6. MPC nodes call `respond_verify_foreign_tx`; `resolve_yields_for` fans the response out to **both** yields.
7. Both Alice and the attacker receive the same root-key-signed `VerifyForeignTransactionResponse`.
8. Attacker uses the response to claim Alice's bridge transfer before Alice does.

This is a **forged foreign-chain verification** enabling **invalid bridge execution** and **double-spend conditions**, matching the allowed High impact scope.

---

### Likelihood Explanation

- All NEAR transactions are public; observing a pending request requires no special access.
- Submitting the same request costs only 1 yoctoNEAR.
- No threshold collusion, privileged role, or leaked key is required.
- The attacker only needs to front-run Alice's claim on the destination bridge contract after both have received the same signature.

Likelihood is **High**.

---

### Recommendation

Bind the caller's `predecessor_account_id` into the `VerifyForeignTransactionRequest` key, exactly as `sign()` and `request_app_private_key()` do. Add a `requester: AccountId` field to `VerifyForeignTransactionRequest` and populate it from the `predecessor` returned by `check_request_preconditions`. Additionally, include the requester identity in the signed payload (via a tweak or explicit field) so the MPC signature is cryptographically bound to the original caller.

---

### Proof of Concept

```
// Step 1: Alice submits a verify_foreign_transaction request
alice.call(mpc_contract, "verify_foreign_transaction", {
    request: { Bitcoin: { tx_id: X, confirmations: 2, extractors: [BlockHash] } },
    domain_id: D,
    payload_version: V1
}, deposit=1_yoctoNEAR)

// Step 2: Attacker observes Alice's pending request on-chain (public)
// pending_verify_foreign_tx_requests[{Bitcoin{tx_id:X,...}, D, V1}] = [alice_yield]

// Step 3: Attacker submits the identical request
attacker.call(mpc_contract, "verify_foreign_transaction", {
    request: { Bitcoin: { tx_id: X, confirmations: 2, extractors: [BlockHash] } },
    domain_id: D,
    payload_version: V1
}, deposit=1_yoctoNEAR)

// pending_verify_foreign_tx_requests[{Bitcoin{tx_id:X,...}, D, V1}] = [alice_yield, attacker_yield]

// Step 4: MPC node calls respond_verify_foreign_tx with valid signature
// resolve_yields_for drains BOTH yields with the same response

// Step 5: Both Alice and Attacker receive identical VerifyForeignTransactionResponse
// { payload_hash: H(Bitcoin{tx_id:X,...}), signature: root_key_sig }

// Step 6: Attacker uses the response to claim Alice's bridge transfer
bridge_contract.claim(response)  // attacker wins the race
```

**Root cause lines**:
- `crates/contract/src/lib.rs:526-531` — `predecessor` discarded
- `crates/contract/src/lib.rs:549` — `args_into_verify_foreign_tx_request` omits caller
- `crates/near-mpc-contract-interface/src/types/foreign_chain.rs:124-128` — `VerifyForeignTransactionRequest` has no caller field
- `crates/contract/src/lib.rs:729-734` — root key used, no caller tweak [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** crates/contract/src/lib.rs (L379-384)
```rust
        let request = SignatureRequest::new(
            request.domain_id,
            request.payload,
            &predecessor,
            &request.path,
        );
```

**File:** crates/contract/src/lib.rs (L493-498)
```rust
        let request = CKDRequest::new(
            request.app_public_key,
            domain_id,
            &predecessor,
            &request.derivation_path,
        );
```

**File:** crates/contract/src/lib.rs (L526-531)
```rust
        self.check_request_preconditions(
            request.domain_id,
            DomainPurpose::ForeignTx,
            Gas::from_tgas(self.config.sign_call_gas_attachment_requirement_tera_gas),
            MINIMUM_SIGN_REQUEST_DEPOSIT,
        );
```

**File:** crates/contract/src/lib.rs (L596-608)
```rust
                    .as_affine();
                let expected_public_key =
                    derive_key_secp256k1(&affine, &request.tweak).map_err(RespondError::from)?;

                let payload_hash = request.payload.as_ecdsa().expect("Payload is not ECDSA");

                // Check the signature is correct
                near_mpc_signature_verifier::verify_ecdsa_signature(
                    signature_response,
                    payload_hash,
                    &expected_public_key,
                )
                .is_ok()
```

**File:** crates/contract/src/lib.rs (L729-734)
```rust
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
