### Title
Cross-Network `DelegateAction` Signature Replay via Missing Chain Binding in Signed Hash — (File: `core/primitives/src/action/delegate.rs`)

### Summary
The `DelegateAction` (meta-transaction) signature scheme does not include any chain/network identifier in the signed hash. A `DelegateAction` signed on one NEAR network (e.g., testnet) is cryptographically valid on another network (e.g., mainnet) if the same account and key pair exist on both. An unprivileged attacker acting as a relayer can replay the observed testnet `DelegateAction` on mainnet, executing the inner actions (token transfers, key additions/deletions) without the victim's consent.

### Finding Description
`DelegateAction::get_nep461_hash()` computes the signed hash as:

```rust
let signable = SignableMessage::new(&self, SignableMessageType::DelegateAction);
let bytes = borsh::to_vec(&signable).expect("Failed to deserialize");
hash(&bytes)
``` [1](#0-0) 

`SignableMessage` wraps the `DelegateAction` with a fixed discriminant derived from the NEP number (366):

```rust
SignableMessageType::DelegateAction => {
    MessageDiscriminant::new_on_chain(NEP_366_META_TRANSACTIONS).unwrap()
}
``` [2](#0-1) 

The discriminant is a constant integer (`1 << 30 + 366`), not network-specific. The `DelegateAction` struct itself contains `sender_id`, `receiver_id`, `actions`, `nonce`, `max_block_height`, and `public_key` — but **no genesis hash, chain ID, or any network-scoped identifier**: [3](#0-2) 

By contrast, regular `SignedTransaction` includes `block_hash`, which is chain-specific and prevents cross-network replay: [4](#0-3) 

`DelegateAction` has no equivalent binding. The verification path in `SignedDelegateAction::verify()` only checks the signature against the chain-agnostic hash: [5](#0-4) 

The runtime validation in `apply_delegate_action` checks signature, nonce, `max_block_height`, and key existence — none of which are network-scoped: [3](#0-2) 

### Impact Explanation
An attacker who runs a testnet node observes a `DelegateAction` broadcast in a testnet transaction. They extract it and wrap it in a new mainnet `SignedTransaction` (the attacker is the relayer and pays gas). If the victim's mainnet access key nonce is lower than the `DelegateAction` nonce and `max_block_height` has not expired on mainnet, the inner actions execute on mainnet without the victim's authorization. Corrupted protocol values:

- **Account balance**: unauthorized token transfer drains victim's mainnet funds
- **Access key nonce**: incremented on mainnet, consuming a nonce the victim never intended to use on mainnet
- **Access key set**: if the inner action is `AddKey`/`DeleteKey`, the victim's mainnet key set is altered

These are concrete, irreversible state changes to the mainnet trie.

### Likelihood Explanation
Medium-Low. Four conditions must hold simultaneously:
1. Victim has the same `AccountId` on both networks — **common** (NEAR account IDs are human-readable and reused across networks by developers and users)
2. Same key pair registered on both networks — **common** (wallets and tooling often reuse keys)
3. Victim's mainnet access key nonce < `DelegateAction` nonce — **realistic** for users who are more active on testnet than mainnet, or for fresh mainnet accounts
4. `max_block_height` not expired on mainnet — **limited window** (~100 blocks ≈ 100 seconds for typical relayer-set values, but some relayers set much larger windows)

The attacker needs only a testnet node (free) and a mainnet account with minimal NEAR for gas.

### Recommendation
Include a network-scoped identifier in the `DelegateAction` signed payload — specifically the genesis hash (`GenesisId.hash`) or a dedicated chain ID field — analogous to EIP-712's `chainId` and `verifyingContract`. This would make a testnet signature cryptographically invalid on mainnet even if all other fields match. The `SignableMessage` discriminant scheme in `core/primitives/src/signable_message.rs` should be extended to incorporate a genesis-hash binding for on-chain message types. [6](#0-5) 

### Proof of Concept

1. Alice has `alice.near` on both mainnet and testnet with the same ED25519 key pair `K`.
2. Alice signs a testnet `DelegateAction` (nonce=5, max\_block\_height=T+200, actions=[Transfer 10 NEAR to bob.near]) via a testnet relayer.
3. Attacker's testnet node receives the transaction containing the `SignedDelegateAction` over P2P.
4. Attacker extracts the `SignedDelegateAction` and constructs a mainnet `SignedTransaction` wrapping it (attacker signs the outer tx with their own mainnet key as relayer).
5. Attacker submits to mainnet RPC (`broadcast_tx_commit`).
6. Mainnet runtime calls `signed_delegate_action.verify()`:
   - Computes `hash(NEP366_discriminant || delegate_action)` — **identical hash** to testnet, since no chain ID is included
   - Verifies against `alice.near`'s mainnet public key `K.public` — **passes**, same key
7. Mainnet checks: alice's mainnet nonce=3 < 5 → valid; mainnet height < T+200 → valid (if within window).
8. Inner `Transfer` executes: 10 NEAR deducted from `alice.near` on mainnet, credited to `bob.near` on mainnet — **without Alice's knowledge or consent**. [5](#0-4) [7](#0-6)

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

**File:** core/primitives/src/action/delegate.rs (L83-96)
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
}
```

**File:** core/primitives/src/action/delegate.rs (L349-357)
```rust
    /// Delegate action hash used for NEP-461 signature scheme which tags
    /// different messages before hashing
    ///
    /// For more details, see: [NEP-461](https://github.com/near/NEPs/pull/461)
    pub fn get_nep461_hash(&self) -> CryptoHash {
        let signable = SignableMessage::new(&self, SignableMessageType::DelegateAction);
        let bytes = borsh::to_vec(&signable).expect("Failed to deserialize");
        hash(&bytes)
    }
```

**File:** core/primitives/src/signable_message.rs (L18-25)
```rust
const MIN_ON_CHAIN_DISCRIMINANT: u32 = 1 << 30;
const MAX_ON_CHAIN_DISCRIMINANT: u32 = (1 << 31) - 1;
const MIN_OFF_CHAIN_DISCRIMINANT: u32 = 1 << 31;
const MAX_OFF_CHAIN_DISCRIMINANT: u32 = u32::MAX;

// NEPs currently included in the scheme
const NEP_366_META_TRANSACTIONS: u32 = 366;
const NEP_611_GAS_KEYS: u32 = 611;
```

**File:** core/primitives/src/signable_message.rs (L97-108)
```rust
impl<'a, T: BorshSerialize> SignableMessage<'a, T> {
    pub fn new(msg: &'a T, ty: SignableMessageType) -> Self {
        let discriminant = ty.into();
        Self { discriminant, msg }
    }

    pub fn sign(&self, signer: &Signer) -> Signature {
        let bytes = borsh::to_vec(&self).expect("Failed to deserialize");
        let hash = hash(&bytes);
        signer.sign(hash.as_bytes())
    }
}
```

**File:** core/primitives/src/signable_message.rs (L221-223)
```rust
            SignableMessageType::DelegateAction => {
                MessageDiscriminant::new_on_chain(NEP_366_META_TRANSACTIONS).unwrap()
            }
```

**File:** core/primitives/src/transaction.rs (L119-137)
```rust
pub struct TransactionV1 {
    /// An account on which behalf transaction is signed
    pub signer_id: AccountId,
    /// A public key of the access key which was used to sign an account.
    /// Access key holds permissions for calling certain kinds of actions.
    pub public_key: PublicKey,
    /// Nonce is used to determine order of transaction in the pool.
    /// It increments for a combination of `signer_id` and `public_key`,
    /// and for gas key it also includes a `nonce_index`.
    pub nonce: TransactionNonce,
    /// Receiver account for this transaction
    pub receiver_id: AccountId,
    /// The hash of the block in the blockchain on top of which the given transaction is valid
    pub block_hash: CryptoHash,
    /// A list of actions to be applied
    pub actions: Vec<Action>,
    /// Controls nonce validation mode (monotonic or strict sequential).
    pub nonce_mode: NonceMode,
}
```
