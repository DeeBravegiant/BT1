### Title
Caller Identity Not Bound in `verify_foreign_transaction` Request Key, Enabling Front-Running Signature Theft — (File: `crates/contract/src/lib.rs`)

---

### Summary

`verify_foreign_transaction` discards the caller's `predecessor` account when constructing the `VerifyForeignTransactionRequest` key stored in contract state. Any unprivileged contract can submit an identical request and receive the same MPC-issued signature, enabling a front-running attacker to obtain a valid bridge-verification signature for a foreign-chain transaction they did not initiate.

---

### Finding Description

The `sign()` and `request_app_private_key()` methods both bind the caller's identity into the stored request key by passing `predecessor` into the key-derivation step:

`sign()` — caller identity is baked into the tweak: [1](#0-0) 

`request_app_private_key()` — same pattern: [2](#0-1) 

`verify_foreign_transaction()` — the `predecessor` returned by `check_request_preconditions` is **silently discarded** (bound to `_`), and `args_into_verify_foreign_tx_request` receives only the raw request args with no caller binding: [3](#0-2) 

The resulting `VerifyForeignTransactionRequest` is keyed only on the foreign-chain transaction data. The signed payload is derived from `ForeignTxSignPayloadV1 { request, values }` — no caller account appears anywhere: [4](#0-3) 

When `respond_verify_foreign_tx` is called by MPC nodes, `resolve_yields_for` drains **all** pending yields registered under the same request key: [5](#0-4) 

This means every contract that enqueued the same `VerifyForeignTransactionRequest` — regardless of who they are — receives the identical MPC signature in their callback.

---

### Impact Explanation

**High — invalid bridge execution / double-spend.**

A bridge contract (Alice) calls `verify_foreign_transaction` to prove that a specific Ethereum deposit transaction occurred, intending to mint or release NEAR-side assets. Eve's malicious contract submits the identical `VerifyForeignTransactionRequest` (same chain, same tx-id, same extractors). Both yields are queued under the same map key. A single MPC response satisfies both. Eve's contract receives a fully valid MPC signature attesting to the same foreign-chain transaction and can use it to claim the NEAR-side assets that should belong to Alice's depositor — a direct theft of bridged funds.

---

### Likelihood Explanation

**Medium-High.** The attack requires only:
1. Observing a pending `verify_foreign_transaction` call in the NEAR transaction pool (public mempool).
2. Submitting an identical call from an attacker-controlled contract with a higher gas price.

No privileged access, key material, or threshold collusion is required. The attacker pays only the `MINIMUM_SIGN_REQUEST_DEPOSIT`. The attack is profitable whenever the bridged asset value exceeds that deposit.

---

### Recommendation

Pass `predecessor` into `args_into_verify_foreign_tx_request` and include it in `VerifyForeignTransactionRequest`, mirroring the pattern used by `sign()` and `request_app_private_key()`. The caller account should be incorporated into the request key (and ideally into the signed payload hash) so that:

- Two different callers submitting the same foreign-chain transaction produce **different** request keys.
- The MPC signature is only deliverable to the contract that initiated the verification.

```rust
// In verify_foreign_transaction:
let (_, predecessor) = self.check_request_preconditions(...);
let request = args_into_verify_foreign_tx_request(request, &predecessor);
//                                                          ^^^^^^^^^^^^ add caller binding
```

---

### Proof of Concept

1. **Alice's bridge contract** calls `verify_foreign_transaction({ chain: Ethereum, tx_id: 0xABC, ... })` with deposit `D`. Her yield ID `Y_alice` is stored under key `K = hash(Ethereum, 0xABC, ...)`.

2. **Eve's malicious contract** observes the mempool and calls `verify_foreign_transaction({ chain: Ethereum, tx_id: 0xABC, ... })` with a higher gas price. Her yield ID `Y_eve` is also stored under the same key `K`.

3. MPC nodes index both requests (identical key `K`), process once, and call `respond_verify_foreign_tx(K, sig)`.

4. `resolve_yields_for(&mut self.pending_verify_foreign_tx_requests, &K, sig)` drains **both** `Y_alice` and `Y_eve` — both callbacks receive the valid MPC signature. [5](#0-4) 

5. Eve's contract callback receives the MPC signature attesting to Ethereum tx `0xABC` and uses it to claim the NEAR-side assets (e.g., minted tokens or released collateral) that Alice's depositor is entitled to.

The root cause — `predecessor` discarded, no caller binding in the request key — is confirmed at: [6](#0-5)

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

**File:** crates/contract/src/lib.rs (L526-556)
```rust
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
```

**File:** crates/contract/src/lib.rs (L749-753)
```rust
        pending_requests::resolve_yields_for(
            &mut self.pending_verify_foreign_tx_requests,
            &request,
            serde_json::to_vec(&response).unwrap(),
        )
```

**File:** crates/near-mpc-contract-interface/src/types/foreign_chain.rs (L1-18)
```rust
// allow deprecation for module, since macro decorators don't work
// when applied directly on struct.
#![expect(deprecated, reason = "ForeignChainConfiguration is being deprecated")]

use borsh::{BorshDeserialize, BorshSerialize};
use near_mpc_bounded_collections::{EmptyBoundedVec, NonEmptyBTreeMap, NonEmptyBTreeSet};
use serde::{Deserialize, Serialize};
use serde_with::{hex::Hex, serde_as};
use sha2::Digest;
use std::collections::{BTreeMap, BTreeSet};

use crate::types::primitives::{AccountId, DomainId};
use crate::types::{Ed25519PublicKey, SignatureResponse};

/// Maximum number of significant data bits a TON Cell may hold.
///
/// See <https://docs.ton.org/foundations/serialization/cells#standard-cell-representation-and-its-hash>.
pub const TON_CELL_MAX_DATA_BITS: u16 = 1023;
```
