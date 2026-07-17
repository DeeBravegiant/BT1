### Title
Missing Chain-Specific Binding in `DelegateAction` Signing Domain Enables Cross-Fork Signature Replay - (File: `core/primitives/src/action/delegate.rs`)

### Summary

`DelegateAction` (NEP-366 meta-transactions) and `DelegateActionV2` (NEP-611) compute their signing hash over a payload that contains no chain-specific identifier. The signing domain is a fixed NEP discriminant constant combined with the borsh-serialized action body. Neither the discriminant nor any field in the action body binds the signature to a specific NEAR chain. A `SignedDelegateAction` produced on mainnet is cryptographically valid on any fork that shares the same account state at the fork point, allowing an attacker to replay the user's inner-action authorization on the fork chain without the user's consent.

### Finding Description

`DelegateAction::get_nep461_hash()` computes the signing hash as:

```
SHA-256( u32_le(MIN_ON_CHAIN_DISCRIMINANT + NEP_366) || borsh(DelegateAction) )
```

The `DelegateAction` struct contains `sender_id`, `receiver_id`, `actions`, `nonce`, `max_block_height`, and `public_key`. None of these fields are chain-specific. The discriminant is the constant `(1 << 30) + 366`, fixed at compile time. [1](#0-0) 

The `SignableMessage` / `MessageDiscriminant` infrastructure in `signable_message.rs` provides message-type separation (on-chain vs off-chain, NEP number) but deliberately omits any chain or network identifier: [2](#0-1) 

By contrast, the outer `SignedTransaction` that carries the `DelegateAction` does include a `block_hash` field, which the chain validates via `check_transaction_validity_period` to ensure the block is an ancestor of the current chain head — providing chain-specific binding for the relayer's outer signature. The user's inner `SignedDelegateAction` has no equivalent binding. [3](#0-2) 

The `DelegateActionV2` / `VersionedDelegateActionPayload` path has the same structural absence: [4](#0-3) 

### Impact Explanation

On a hard fork where both chains share the same account state up to the fork point:

1. Alice signs a `SignedDelegateAction` on mainnet (e.g., authorizing a token transfer).
2. The fork occurs; both chains have identical account nonces and balances at the fork height.
3. An attacker who obtained Alice's `SignedDelegateAction` (transmitted off-chain to a relayer) wraps it in a new outer `SignedTransaction` referencing a valid block hash on the fork chain.
4. The fork chain's runtime calls `SignedDelegateAction::verify()`, which recomputes `get_nep461_hash()` — identical on both chains — and accepts the signature.
5. The inner actions execute on the fork chain: Alice's balance is debited and the receiver's balance is credited, without Alice having authorized any action on the fork chain.

The corrupted protocol values are: Alice's account **balance** (debited on the fork chain), the **nonce** of her access key (incremented on the fork chain), and the resulting **state root** of the shard. [5](#0-4) 

### Likelihood Explanation

NEAR has a production `fork-network` tool and has undergone protocol upgrades. Any scenario where two NEAR chains share a common history (hard fork, testnet reset to mainnet state, or fork-network snapshot) creates the replay window. The `max_block_height` field provides temporal expiry but not chain binding; users commonly set it hundreds of blocks in the future, leaving a wide replay window. The `nonce` prevents replay within the same chain but not across chains that share the same nonce state at the fork point. The `SignedDelegateAction` is transmitted off-chain to relayers, making interception realistic. Likelihood is **Medium** — requires a fork scenario, but NEAR's ecosystem tooling makes this a realistic operational condition.

### Recommendation

Include a chain-specific identifier in the `DelegateAction` signing payload. The most direct fix is to add the genesis block hash or the `chain_id` string (e.g., `"mainnet"`, `"testnet"`) to the `SignableMessage` or to the `DelegateAction` struct itself, so that the signing hash is unique per chain. Alternatively, add a `block_hash` field to `DelegateAction` (analogous to `SignedTransaction`) and validate it against the chain's block ancestry at execution time, providing both chain binding and a freshness window.

### Proof of Concept

```
# On mainnet (or any NEAR chain A):
alice_delegate = DelegateAction {
    sender_id: "alice.near",
    receiver_id: "token.near",
    actions: [ft_transfer("eve", 1000)],
    nonce: 42,
    max_block_height: 200_000_000,  # far future
    public_key: alice_pubkey,
}
hash_A = SHA256(u32_le(0x4000016E) || borsh(alice_delegate))
sig_A  = alice_sk.sign(hash_A)

# On fork chain B (same account state at fork point):
hash_B = SHA256(u32_le(0x4000016E) || borsh(alice_delegate))
# hash_B == hash_A  (no chain binding)
# sig_A.verify(hash_B, alice_pubkey) == true

# Attacker wraps sig_A in a new outer tx referencing a chain-B block hash:
outer_tx_B = SignedTransaction {
    ...,
    block_hash: <valid chain-B block hash>,
    actions: [Delegate(SignedDelegateAction { alice_delegate, sig_A })],
}
# Chain B accepts it: Alice's tokens are transferred on chain B without her consent.
```

The root cause is in `DelegateAction::get_nep461_hash()` at `core/primitives/src/action/delegate.rs` lines 353–357 and the equivalent `VersionedDelegateActionPayload::get_nep461_hash()` at lines 180–184, both of which produce a chain-agnostic digest. [6](#0-5) [7](#0-6)

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

**File:** core/primitives/src/action/delegate.rs (L180-184)
```rust
    pub fn get_nep461_hash(&self) -> CryptoHash {
        let signable = SignableMessage::new(&self, SignableMessageType::DelegateActionV2);
        let bytes = borsh::to_vec(&signable).expect("failed to serialize");
        hash(&bytes)
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
