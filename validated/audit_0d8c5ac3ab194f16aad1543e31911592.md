### Title
Caller-Agnostic `verify_foreign_transaction` Request Key Enables JIT Front-Running of MPC-Signed Foreign-Chain Attestations - (`crates/contract/src/dto_mapping.rs`, `crates/near-mpc-contract-interface/src/types/foreign_chain.rs`)

---

### Summary

`verify_foreign_transaction` stores pending requests under a key that contains **no caller identity**. Any unprivileged actor who observes a pending request on-chain can submit the identical request and receive the same MPC-signed attestation (`VerifyForeignTransactionResponse`) when the nodes respond. Because the signed payload also contains no requester binding, the attestation is a globally transferable proof. An attacker can use it to claim the victim's inbound bridge funds on NEAR before the victim does.

---

### Finding Description

**Request key is caller-agnostic.**

`VerifyForeignTransactionRequest` is defined as:

```rust
pub struct VerifyForeignTransactionRequest {
    pub request: ForeignChainRpcRequest,
    pub domain_id: DomainId,
    pub payload_version: ForeignTxPayloadVersion,
}
```

No caller `AccountId`, no tweak, no derivation path. [1](#0-0) 

The conversion function `args_into_verify_foreign_tx_request` simply drops the predecessor entirely:

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
``` [2](#0-1) 

Contrast this with `sign`, which derives a `tweak` from `(predecessor_id, path)` and stores it in `SignatureRequest`, making every caller's request cryptographically distinct: [3](#0-2) 

**Fan-out drains all callers with one response.**

`respond_verify_foreign_tx` calls `resolve_yields_for`, which drains every queued yield under the single caller-agnostic key in one shot: [4](#0-3) 

This behavior is explicitly tested and confirmed:

```
// Then: both queued yields are drained from the single map entry.
``` [5](#0-4) 

**Signed attestation carries no requester binding.**

The payload the MPC network signs is `ForeignTxSignPayload::V1(ForeignTxSignPayloadV1 { request, values })` — `request` is the caller-agnostic struct above, and `values` are the extracted on-chain observations. Neither field encodes who submitted the request. The signature is verified against the **root** public key (no tweak/derivation): [6](#0-5) 

The resulting `VerifyForeignTransactionResponse { payload_hash, signature }` is therefore a globally transferable proof that a specific foreign transaction occurred, with no binding to the original requester.

**Design doc discrepancy.**

The design document for this feature explicitly included a `derivation_path: String` field in `VerifyForeignTransactionRequestArgs` to bind the signed output to the caller's key derivation path: [7](#0-6) 

The production implementation omits this field entirely, removing the only mechanism that would have made the attestation caller-specific.

---

### Impact Explanation

The primary use case for `verify_foreign_transaction` is inbound bridge flows (foreign chain → NEAR): a user sends assets on a foreign chain and submits a verification request to prove it, expecting to receive the MPC-signed attestation needed to claim the corresponding NEAR-side assets.

**Attack flow:**

1. Alice sends 1 ETH to a bridge contract on Ethereum and submits `verify_foreign_transaction` with the Ethereum `tx_id`.
2. Bob observes Alice's pending request on-chain (it is public) and immediately submits the **identical** `verify_foreign_transaction` request (cost: 1 yoctonear).
3. MPC nodes verify the Ethereum transaction and call `respond_verify_foreign_tx`. The fan-out mechanism delivers the **same** `VerifyForeignTransactionResponse` to both Alice and Bob.
4. Bob, now holding a valid MPC-signed attestation for Alice's Ethereum deposit, calls the NEAR bridge contract before Alice, claiming Alice's funds.
5. When Alice's transaction executes, the bridge contract either rejects it (double-spend guard) or has already been drained.

The attack is executable by any unprivileged NEAR account. No threshold collusion, no key material, no privileged access is required. The attacker's only cost is 1 yoctonear and a single NEAR transaction.

This matches the allowed impact: **High — cross-chain replay / participant-authorization bypass causing invalid bridge execution or double-spend conditions.**

---

### Likelihood Explanation

- All pending `verify_foreign_transaction` requests are visible on-chain the moment they are included in a block.
- The attacker's counter-transaction is a simple function call with a 1 yoctonear deposit; no special capability is needed.
- NEAR block times are ~1 second, giving the attacker ample time to observe and front-run within the same or next block.
- The fan-out behavior is an explicit, tested protocol feature, not an edge case.
- Any bridge contract that does not independently re-extract and verify the recipient address from the foreign transaction data is immediately vulnerable.

---

### Recommendation

1. **Bind the request key to the caller.** Include `predecessor_id` in `VerifyForeignTransactionRequest` (analogous to the `tweak` in `SignatureRequest`) so that two different callers submitting the same foreign-tx query produce distinct pending-request map entries and distinct signed payloads.

2. **Include the caller in the signed payload.** The `ForeignTxSignPayload` should encode the requester's NEAR account ID so that the MPC-signed attestation is cryptographically bound to the original submitter and cannot be reused by a different account.

3. **Restore the `derivation_path` field.** The design document already specified this field for exactly this purpose. Reinstating it and deriving a per-caller tweak (as `sign` does) would close the gap between the design intent and the implementation.

---

### Proof of Concept

The existing unit test already demonstrates the complete mechanism:

```rust
// caller alice submits the request
contract.verify_foreign_transaction(request_args.clone()); // alice

// caller bob submits the IDENTICAL request
contract.verify_foreign_transaction(request_args);         // bob

// single respond drains BOTH yields — bob receives alice's attestation
contract.respond_verify_foreign_tx(request.clone(), response)
    .expect("respond_verify_foreign_tx should succeed");

// both queued yields are drained from the single map entry
assert!(contract.pending_verify_foreign_tx_requests.get(&request).is_none());
``` [8](#0-7) 

The sandbox integration test confirms this end-to-end: Alice and Bob both receive the identical `VerifyForeignTransactionResponse` from a single MPC response: [9](#0-8) 

A bridge-exploit scenario requires only wrapping Bob's `verify_foreign_transaction` submission and the subsequent bridge-claim call in a single NEAR transaction (or two consecutive transactions within the same block), which is straightforward given NEAR's 1-second block time and the public visibility of pending requests.

### Citations

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

**File:** crates/near-mpc-crypto-types/src/sign.rs (L117-125)
```rust
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

**File:** crates/contract/src/lib.rs (L749-753)
```rust
        pending_requests::resolve_yields_for(
            &mut self.pending_verify_foreign_tx_requests,
            &request,
            serde_json::to_vec(&response).unwrap(),
        )
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

**File:** docs/foreign-chain-transactions.md (L98-111)
```markdown
pub struct VerifyForeignTransactionRequestArgs {
    pub request: ForeignChainRpcRequest,
    pub derivation_path: String, // Key derivation path
    pub domain_id: DomainId,
    pub payload_version: ForeignTxPayloadVersion,
}

pub struct VerifyForeignTransactionRequest {
    pub request: ForeignChainRpcRequest,
    pub tweak: Tweak,
    pub domain_id: DomainId,
    pub payload_version: ForeignTxPayloadVersion,
}
```
```

**File:** crates/contract/tests/sandbox/foreign_chain_request.rs (L104-194)
```rust
#[tokio::test]
async fn verify_foreign_transaction__should_fan_out_response_to_duplicates_from_different_callers()
{
    // Given
    let rpc_request = bitcoin_request();
    let extracted_values = bitcoin_extracted_values();
    let chain = rpc_request.chain();
    let setup = SandboxTestSetup::builder()
        .with_foreign_tx_domain()
        .build()
        .await;
    let foreign_tx_key = setup.foreign_tx_key();
    register_foreign_chain_configuration(chain, &setup.contract, &setup.mpc_signer_accounts).await;

    let alice = setup.worker.dev_create_account().await.unwrap();
    let bob = setup.worker.dev_create_account().await.unwrap();
    let domain_id = dtos::DomainId(foreign_tx_key.domain_id().0);
    let request_args = dtos::VerifyForeignTransactionRequestArgs {
        domain_id,
        payload_version: ForeignTxPayloadVersion::V1,
        request: rpc_request.clone(),
    };
    let verify_request = VerifyForeignTransactionRequest {
        domain_id,
        payload_version: ForeignTxPayloadVersion::V1,
        request: rpc_request,
    };

    // When
    let status_alice = alice
        .call(
            setup.contract.id(),
            method_names::VERIFY_FOREIGN_TRANSACTION,
        )
        .args_json(json!({ "request": request_args }))
        .deposit(NearToken::from_yoctonear(1))
        .max_gas()
        .transact_async()
        .await
        .unwrap();
    let status_bob = bob
        .call(
            setup.contract.id(),
            method_names::VERIFY_FOREIGN_TRANSACTION,
        )
        .args_json(json!({ "request": request_args }))
        .deposit(NearToken::from_yoctonear(1))
        .max_gas()
        .transact_async()
        .await
        .unwrap();
    await_pending_foreign_tx_request_observed_on_contract(&setup.contract, &verify_request).await;

    let (payload, response) = sign_foreign_tx_response(
        &verify_request.request,
        extracted_values,
        foreign_tx_key.as_secp256k1(),
    );
    let respond_result = setup.mpc_signer_accounts[0]
        .call(setup.contract.id(), method_names::RESPOND_VERIFY_FOREIGN_TX)
        .args_json(json!({
            "request": verify_request,
            "response": response,
        }))
        .max_gas()
        .transact()
        .await
        .unwrap()
        .into_result();

    // Then
    assert!(
        respond_result.is_ok(),
        "single respond_verify_foreign_tx should drain both queued yields: {respond_result:?}",
    );
    let expected_payload_hash = payload.compute_msg_hash().unwrap();

    let exec_alice = status_alice.await.unwrap().into_result().unwrap();
    let returned_alice: VerifyForeignTransactionResponse = exec_alice.json().unwrap();
    assert_eq!(
        returned_alice.payload_hash, expected_payload_hash,
        "alice's tx should receive the fanned-out response",
    );

    let exec_bob = status_bob.await.unwrap().into_result().unwrap();
    let returned_bob: VerifyForeignTransactionResponse = exec_bob.json().unwrap();
    assert_eq!(
        returned_bob.payload_hash, expected_payload_hash,
        "bob's tx should receive the same fanned-out response and not be displaced by alice",
    );
}
```
