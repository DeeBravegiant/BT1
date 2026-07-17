### Title
DelegateAction (Meta-Transaction) Signing Payload Lacks Chain-ID Binding, Enabling Cross-Chain Replay - (File: core/primitives/src/action/delegate.rs)

### Summary
The `DelegateAction` signing scheme (NEP-366 meta transactions) constructs a signed payload that contains no chain identifier. A `SignedDelegateAction` produced for one NEAR network (e.g., testnet) is cryptographically valid on any other NEAR network (e.g., mainnet) where the same account exists with the same key and a compatible nonce. Any unprivileged observer who sees the action on-chain can replay it on a different network.

### Finding Description
`DelegateAction::get_nep461_hash()` builds the signed digest by serializing a `SignableMessage` that wraps only a NEP discriminant and the action body:

```rust
pub fn get_nep461_hash(&self) -> CryptoHash {
    let signable = SignableMessage::new(&self, SignableMessageType::DelegateAction);
    let bytes = borsh::to_vec(&signable).expect("Failed to deserialize");
    hash(&bytes)
}
``` [1](#0-0) 

The `SignableMessage` struct carries only a `MessageDiscriminant` (a NEP number) and the message body — no chain ID, no genesis hash, no network identifier:

```rust
pub struct SignableMessage<'a, T> {
    pub discriminant: MessageDiscriminant,
    pub msg: &'a T,
}
``` [2](#0-1) 

The `DelegateAction` body itself contains `sender_id`, `receiver_id`, `actions`, `nonce`, `max_block_height`, and `public_key` — none of which are chain-specific: [3](#0-2) 

Crucially, `max_block_height` is a plain integer (not a block hash), so it provides no implicit chain binding. By contrast, a regular `SignedTransaction` includes a `block_hash` field that implicitly binds the signature to a specific chain because block hashes diverge from genesis. `DelegateAction` has no equivalent.

`SignedDelegateAction::verify()` therefore accepts any signature that is valid over the chain-agnostic hash, regardless of which network the action was originally intended for: [4](#0-3) 

The same pattern applies to `VersionedSignedDelegateAction` (NEP-611 / `DelegateActionV2`): [5](#0-4) 

### Impact Explanation
An attacker who observes a `SignedDelegateAction` on chain A (it is embedded in a public `SignedTransaction` and visible to all) can wrap the identical `SignedDelegateAction` in a fresh outer transaction and submit it on chain B. If the user's account exists on chain B with the same key and the nonce is still valid, the runtime will accept the signature and execute the inner actions. Possible consequences include:

- Unauthorized NEAR token transfers (`Action::Transfer`)
- Unauthorized access-key additions or deletions (`Action::AddKey` / `Action::DeleteKey`)
- Unauthorized contract calls (`Action::FunctionCall`)

The corrupted protocol values are the account balance, access-key set, and nonce on chain B — all modified without the user's consent for that chain.

### Likelihood Explanation
Many users maintain accounts with the same name and key pair on both mainnet and testnet. Nonces on testnet are often lower than on mainnet, making a mainnet-signed action valid on testnet. The attacker needs only to monitor the public chain for `SignedDelegateAction` objects and submit them on the target network — no privileged access is required.

### Recommendation
Include the chain's genesis hash or a canonical chain-ID string in the signed payload of `DelegateAction` and `DelegateActionV2`. The simplest approach is to add a `chain_id: String` field to `DelegateAction` / `DelegateActionV2` (or to `SignableMessage`) and require that it match the executing node's chain ID during `apply_delegate_action`. Alternatively, replace `max_block_height` with a `recent_block_hash` (as regular transactions use), which provides implicit chain binding.

### Proof of Concept
1. Alice has account `alice.near` on both mainnet and testnet, with the same ED25519 key pair and nonce `N` on testnet.
2. Alice signs a `DelegateAction` for testnet: transfer 10 NEAR to `bob.near`, nonce `N+1`, `max_block_height = 1_000_000`.
3. A relayer submits it on testnet; the action executes. The `SignedDelegateAction` is now visible in the testnet transaction history.
4. An attacker extracts the `SignedDelegateAction` bytes from the testnet transaction.
5. The attacker constructs a new `SignedTransaction` on mainnet, addressed to `alice.near`, containing the extracted `SignedDelegateAction` as its action payload.
6. `SignedDelegateAction::verify()` is called during `apply_delegate_action` on mainnet: [6](#0-5) 
7. The hash is recomputed from the chain-agnostic payload — it matches Alice's signature. The action executes on mainnet, draining 10 NEAR from Alice's mainnet account without her consent.

### Citations

**File:** core/primitives/src/action/delegate.rs (L46-64)
```rust
pub struct DelegateAction {
    /// Signer of the delegated actions
    pub sender_id: AccountId,
    /// Receiver of the delegated actions.
    pub receiver_id: AccountId,
    /// List of actions to be executed.
    ///
    /// With the meta transactions MVP defined in NEP-366, nested
    /// DelegateActions are not allowed. A separate type is used to enforce it.
    pub actions: Vec<NonDelegateAction>,
    /// Nonce to ensure that the same delegate action is not sent twice by a
    /// relayer and should match for given account's `public_key`.
    /// After this action is processed it will increment.
    pub nonce: Nonce,
    /// The maximal height of the block in the blockchain below which the given DelegateAction is valid.
    pub max_block_height: BlockHeight,
    /// Public key used to sign this delegated action.
    pub public_key: PublicKey,
}
```

**File:** core/primitives/src/action/delegate.rs (L83-95)
```rust
impl SignedDelegateAction {
    pub fn verify(&self) -> bool {
        let delegate_action = &self.delegate_action;
        let hash = delegate_action.get_nep461_hash();
        let public_key = &delegate_action.public_key;

        self.signature.verify(hash.as_ref(), public_key)
    }

    pub fn sign(singer: &Signer, delegate_action: DelegateAction) -> Self {
        let signature = singer.sign(delegate_action.get_nep461_hash().as_bytes());
        Self { delegate_action, signature }
    }
```

**File:** core/primitives/src/action/delegate.rs (L210-219)
```rust
impl VersionedSignedDelegateAction {
    pub fn verify(&self) -> bool {
        let hash = self.delegate_action.get_nep461_hash();
        self.signature.verify(hash.as_ref(), self.delegate_action.public_key())
    }

    pub fn sign(signer: &Signer, delegate_action: VersionedDelegateActionPayload) -> Self {
        let signature = signer.sign(delegate_action.get_nep461_hash().as_bytes());
        Self { delegate_action, signature }
    }
```

**File:** core/primitives/src/action/delegate.rs (L353-357)
```rust
    pub fn get_nep461_hash(&self) -> CryptoHash {
        let signable = SignableMessage::new(&self, SignableMessageType::DelegateAction);
        let bytes = borsh::to_vec(&signable).expect("Failed to deserialize");
        hash(&bytes)
    }
```

**File:** core/primitives/src/signable_message.rs (L61-65)
```rust
#[derive(BorshSerialize)]
pub struct SignableMessage<'a, T> {
    pub discriminant: MessageDiscriminant,
    pub msg: &'a T,
}
```
