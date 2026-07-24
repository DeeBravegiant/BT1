### Title
MPC Prover Accepts `EvmFinality::Latest` Signatures for Abstract Chain, Enabling Reorg-Based Unauthorized Minting - (File: `near/omni-prover/mpc-omni-prover/src/lib.rs`)

---

### Summary

The `mpc-omni-prover` is hardcoded to accept proofs for the Abstract chain (`ChainKind::Abs`) at `EvmFinality::Latest`. Because `Latest` blocks are not finalized and can be reorged away, an MPC signature obtained for an `InitTransfer` event in a `Latest` block remains cryptographically valid even after that block is reorganized out of the canonical chain. A relayer can then submit this stale-but-valid signature to the NEAR bridge, causing tokens to be minted on NEAR with no corresponding lock on the Abstract chain — creating unbacked supply.

---

### Finding Description

In `near/omni-prover/mpc-omni-prover/src/lib.rs`, the `init` function hardcodes the finality for `ChainKind::Abs` as `MpcFinality::Evm(EvmFinality::Latest)`: [1](#0-0) 

The `verify_proof` function enforces that the submitted `sign_payload`'s finality matches this configured value: [2](#0-1) 

It then calls the MPC network to read and sign the transaction at `Latest` finality: [3](#0-2) 

In the callback, the only check is that `SHA-256(borsh(sign_payload)) == mpc_response.payload_hash`: [4](#0-3) 

Once the MPC network has signed the payload, the signature is permanently valid. There is no mechanism to invalidate it if the underlying block is later reorganized away. The NEAR bridge's `fin_transfer_callback` then processes the transfer based solely on the MPC-signed `ProverResult`: [5](#0-4) 

By contrast, the `evm-prover` explicitly uses `block_hash_safe`, which returns `None` for blocks not yet in the safe canonical chain, providing reorg protection: [6](#0-5) [7](#0-6) 

The `mpc-omni-prover` has no equivalent protection for the Abstract chain.

---

### Impact Explanation

**Critical.** If an `InitTransfer` event is signed by the MPC network at `Latest` finality and the block is subsequently reorganized away:

1. The user's tokens are **not** locked/burned on the Abstract chain (the transaction no longer exists on the canonical chain).
2. The MPC signature remains valid.
3. A relayer submits the proof to NEAR.
4. The NEAR bridge mints tokens to the recipient.

The result is minted tokens on NEAR with no backing on the Abstract chain — unbacked supply and direct loss to the bridge's reserves. This matches the allowed impact: *"Unauthorized creation, release, withdrawal, or custody escape of native, locked, or wrapped bridge assets through settlement, deployment, or verification failure."*

The `finalised_transfers` set prevents replay of the same `TransferId`, but the **first** use succeeds and the damage is done. [8](#0-7) 

---

### Likelihood Explanation

**Medium.** The Abstract chain is an EVM-compatible L2. At `EvmFinality::Latest`, blocks are produced by a sequencer and are not yet proven on L1. L2 sequencers can and do reorganize `Latest` blocks (e.g., to reorder transactions, fix errors, or under adversarial conditions). A user who is also a sequencer operator can cause a targeted reorg. Even without sequencer access, natural reorgs at `Latest` finality are a documented property of EVM L2s. The window between MPC signing and NEAR finalization is sufficient for a reorg to occur.

---

### Recommendation

Change the configured finality for `ChainKind::Abs` from `EvmFinality::Latest` to `EvmFinality::Finalized` (or at minimum `EvmFinality::Safe`):

```rust
// near/omni-prover/mpc-omni-prover/src/lib.rs
finalities.insert(ChainKind::Abs, MpcFinality::Evm(EvmFinality::Finalized));
```

`EvmFinality::Finalized` ensures the MPC network only signs events from blocks that have been finalized on L1 and cannot be reorganized. This mirrors the protection the `evm-prover` achieves via `block_hash_safe`, which explicitly rejects non-canonical blocks. [1](#0-0) 

---

### Proof of Concept

1. Attacker sends `initTransfer(token, amount=1_000_000, recipient="attacker.near")` on the Abstract chain. Transaction is included in block B at `Latest` finality.
2. Relayer observes the event and calls `mpc_prover.verify_proof(sign_payload)` where `sign_payload` encodes the `InitTransfer` log with `EvmFinality::Latest`.
3. The MPC network reads the transaction from block B, verifies the log, and signs `SHA-256(borsh(sign_payload))`. The signed response is returned.
4. The Abstract chain sequencer reorganizes block B (removing the `initTransfer` transaction). The attacker's tokens are **not** locked.
5. The relayer calls `omni_bridge.fin_transfer(prover_args=mpc_signed_payload)` on NEAR.
6. `fin_transfer_callback` receives `ProverResult::InitTransfer` with `amount=1_000_000`, verifies the factory address, and mints 1,000,000 tokens to `attacker.near`.
7. Attacker holds 1,000,000 tokens on NEAR; the Abstract chain bridge holds zero locked tokens for this transfer. Bridge reserves are drained by the unbacked mint. [9](#0-8) [10](#0-9) [5](#0-4)

### Citations

**File:** near/omni-prover/mpc-omni-prover/src/lib.rs (L57-67)
```rust
    pub fn init(mpc_contract_id: AccountId) -> Self {
        let mut finalities = HashMap::new();
        finalities.insert(ChainKind::Abs, MpcFinality::Evm(EvmFinality::Latest));
        finalities.insert(
            ChainKind::Strk,
            MpcFinality::Starknet(StarknetFinality::AcceptedOnL2),
        );
        finalities.insert(
            ChainKind::Aptos,
            MpcFinality::Aptos(AptosFinality::Committed),
        );
```

**File:** near/omni-prover/mpc-omni-prover/src/lib.rs (L85-121)
```rust
    pub fn verify_proof(&self, #[serializer(borsh)] input: Vec<u8>) -> Promise {
        let args = MpcVerifyProofArgs::try_from_slice(&input).near_expect(ProverError::ParseArgs);

        let sign_payload = ForeignTxSignPayload::try_from_slice(&args.sign_payload)
            .near_expect(ProverError::ParseArgs);

        let ForeignTxSignPayload::V1(ref payload_v1) = sign_payload;

        let chain_kind = Self::request_to_chain_kind(&payload_v1.request)
            .near_expect(ProverError::UnsupportedChain);

        let finality = self
            .finalities
            .get(&chain_kind)
            .near_expect(ProverError::UnsupportedChain);

        require!(
            Self::request_matches_finality(&payload_v1.request, finality),
            ProverError::FinalityMismatch.as_ref()
        );

        let request_args = VerifyForeignTransactionRequestArgs {
            request: payload_v1.request.clone(),
            domain_id: DomainId(FOREIGN_TX_DOMAIN_ID),
            payload_version: ForeignTxPayloadVersion::V1,
        };

        ext_mpc_contract::ext(self.mpc_contract_id.clone())
            .with_static_gas(VERIFY_FOREIGN_TX_GAS)
            .with_attached_deposit(ONE_YOCTO)
            .verify_foreign_transaction(request_args)
            .then(
                Self::ext(near_sdk::env::current_account_id())
                    .with_static_gas(VERIFY_CALLBACK_GAS)
                    .verify_callback(args.proof_kind, args.sign_payload, chain_kind),
            )
    }
```

**File:** near/omni-prover/mpc-omni-prover/src/lib.rs (L142-148)
```rust
        let expected_hash = sign_payload
            .compute_msg_hash()
            .map_err(|_| ProverError::InvalidPayloadHash.to_string())?;

        if expected_hash != mpc_response.payload_hash {
            return Err(ProverError::InvalidPayloadHash.to_string());
        }
```

**File:** near/omni-bridge/src/lib.rs (L709-717)
```rust
        let Ok(ProverResult::InitTransfer(init_transfer)) = Self::decode_prover_result(0) else {
            env::panic_str(BridgeError::InvalidProofMessage.to_string().as_str())
        };
        require!(
            self.factories
                .get(&init_transfer.emitter_address.get_chain())
                == Some(init_transfer.emitter_address),
            BridgeError::UnknownFactory.as_ref()
        );
```

**File:** near/omni-bridge/src/lib.rs (L2231-2236)
```rust
    fn add_fin_transfer(&mut self, transfer_id: &TransferId) -> NearToken {
        let storage_usage = env::storage_usage();
        require!(
            self.finalised_transfers.insert(transfer_id),
            BridgeError::TransferAlreadyFinalised.as_ref()
        );
```

**File:** near/omni-prover/evm-prover/src/lib.rs (L85-99)
```rust
        Ok(evm_client::ext(self.light_client.clone())
            .with_static_gas(BLOCK_HASH_SAFE_GAS)
            .block_hash_safe(header.number.as_u64())
            .then(
                Self::ext(env::current_account_id())
                    .with_static_gas(VERIFY_PROOF_CALLBACK_GAS)
                    .verify_proof_callback(
                        args.proof_kind,
                        evm_proof.log_entry_data,
                        header
                            .hash
                            .ok_or_else(|| ProverError::HashNotSet.to_string())?
                            .0,
                    ),
            ))
```

**File:** near/omni-prover/evm-prover/src/lib.rs (L117-120)
```rust
    ) -> Result<ProverResult, String> {
        if block_hash != Some(expected_block_hash) {
            return Err(ProverError::InvalidBlockHash.to_string());
        }
```
