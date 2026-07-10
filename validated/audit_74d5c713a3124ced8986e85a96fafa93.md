### Title
Fragile Comma-Separator Encoding in `derive_from_path` Enables Cross-Account Tweak Collision and Unauthorized Signing - (File: `crates/near-mpc-crypto-types/src/kdf.rs`)

---

### Summary

The `derive_from_path` function in `crates/near-mpc-crypto-types/src/kdf.rs` constructs the SHA3-256 pre-image by simple string concatenation using a comma as a separator between `predecessor_id` and `path`. Because NEAR account IDs are validated to exclude commas but the `path` parameter is an unconstrained `String`, two distinct `(account_id, path)` pairs can produce an identical hash input, yielding the same tweak. An unprivileged attacker who registers a NEAR account that is a strict prefix of a victim's account ID can supply a crafted path containing a comma to collide with the victim's tweak, obtaining MPC signatures under the victim's derived key for arbitrary payloads.

---

### Finding Description

`derive_from_path` is the shared implementation behind both `derive_tweak` (used in `sign()`) and `derive_app_id` (used in `request_app_private_key()`):

```rust
fn derive_from_path(derivation_prefix: &str, predecessor_id: &AccountId, path: &str) -> [u8; 32] {
    // ',' is ACCOUNT_DATA_SEPARATOR from nearcore ...
    // Do not reuse this hash function on anything that isn't an account
    // ID or it'll be vulnerable to Hash Malleability/extension attacks.
    let derivation_path = format!("{derivation_prefix}{},{}", predecessor_id, path);
    let mut hasher = Sha3_256::new();
    hasher.update(derivation_path);
    let hash: [u8; 32] = hasher.finalize().into();
    hash
}
```

The code comment itself acknowledges the fragility: the comma separator is safe only because NEAR `AccountId` values are validated to exclude commas. However, `path` is a raw `String` with **no character restrictions** — the ABI schema declares it as `{"type": "string"}` with no pattern, and the contract test suite explicitly accepts empty and arbitrary paths.

This creates a structural collision: for any victim account `V` whose string representation has a non-empty prefix `P` (where `P` is itself a valid NEAR account ID), an attacker controlling account `P` can choose path `S + "," + victim_path` where `S` is the suffix of `V` not in `P`. The resulting format string is byte-for-byte identical:

```
prefix + P + "," + S + "," + victim_path   (attacker)
prefix + V + "," + victim_path             (victim, where V = P + S)
```

Both produce the same SHA3-256 output, hence the same 32-byte tweak.

**Concrete example:**
- Victim: account `alice.near`, path `"foo"`  
  → pre-image: `"near-mpc-recovery v0.1.0 epsilon derivation:alice.near,foo"`
- Attacker: account `alice.nea`, path `"r,foo"`  
  → pre-image: `"near-mpc-recovery v0.1.0 epsilon derivation:alice.nea,r,foo"`

Both strings are identical. Both `alice.near` and `alice.nea` are valid NEAR account IDs; `"r,foo"` is a valid path.

The same collision applies to `derive_app_id` (CKD requests), using prefix `"near-mpc v0.1.0 app_id derivation:"`.

---

### Impact Explanation

The tweak is the sole input that differentiates derived keys across accounts and paths. When two `(account, path)` pairs produce the same tweak, the MPC network derives and signs with the **same key** for both. An attacker who collides with victim `alice.near`'s tweak for path `"foo"` can submit arbitrary ECDSA or EdDSA payloads to `sign()` and receive valid signatures under `alice.near`'s derived key — without `alice.near`'s knowledge or consent. Those signatures are valid on any foreign chain (Bitcoin, Ethereum, Solana, etc.) where `alice.near`'s derived address holds funds, enabling direct theft. This satisfies the Critical impact class: **unauthorized transaction execution and threshold signature issuance without the required participant authorization**, and **theft of funds controlled by the MPC network**.

---

### Likelihood Explanation

- NEAR account registration is permissionless; any string matching the account-ID grammar is claimable.
- The victim's account ID and path are observable on-chain (submitted in `sign()` call arguments).
- The attacker only needs to register an account that is a strict prefix of the victim's account ID — a one-time, low-cost action.
- No threshold collusion, TEE break, or privileged access is required; a single unprivileged NEAR account suffices.
- The path `"r,foo"` (or any `suffix + "," + victim_path`) is accepted without error by the contract.

---

### Recommendation

Replace the fragile comma-separator concatenation with a collision-resistant encoding that length-prefixes each component before hashing, or use a proper KDF (e.g., HKDF) with structured inputs. The code already contains a `TODO` comment pointing to HKDF:

```rust
// TODO: Use a key derivation library instead of doing this manually.
// https://crates.io/crates/hkdf might be a good option?
```

A safe replacement:

```rust
fn derive_from_path(prefix: &str, predecessor_id: &AccountId, path: &str) -> [u8; 32] {
    let id_bytes = predecessor_id.as_str().as_bytes();
    let path_bytes = path.as_bytes();
    let mut hasher = Sha3_256::new();
    hasher.update(prefix.as_bytes());
    hasher.update((id_bytes.len() as u64).to_le_bytes());
    hasher.update(id_bytes);
    hasher.update((path_bytes.len() as u64).to_le_bytes());
    hasher.update(path_bytes);
    hasher.finalize().into()
}
```

Alternatively, enforce that `path` contains no commas at the contract entry point (`sign()`, `request_app_private_key()`), though this is a weaker mitigation that breaks existing callers using comma-containing paths and does not fix the structural encoding flaw.

---

### Proof of Concept

1. Victim `alice.near` submits `sign({ path: "foo", payload_v2: { "Ecdsa": "<hash_of_tx>" }, domain_id: 0 })`.  
   Tweak input: `"near-mpc-recovery v0.1.0 epsilon derivation:alice.near,foo"`.

2. Attacker registers NEAR account `alice.nea` (valid, permissionless).

3. Attacker submits `sign({ path: "r,foo", payload_v2: { "Ecdsa": "<hash_of_attacker_tx>" }, domain_id: 0 })` from account `alice.nea`.  
   Tweak input: `"near-mpc-recovery v0.1.0 epsilon derivation:alice.nea,r,foo"`.

4. Both strings are byte-identical → `derive_tweak` returns the same `[u8; 32]` for both.

5. MPC nodes sign `<hash_of_attacker_tx>` using the key derived from `alice.near`'s `"foo"` tweak.

6. The returned ECDSA signature is valid under `alice.near`'s derived public key for path `"foo"`, which the attacker can use to drain any foreign-chain address controlled by that key.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** crates/near-mpc-crypto-types/src/kdf.rs (L10-13)
```rust
pub fn derive_tweak(predecessor_id: &AccountId, path: &str) -> Tweak {
    let hash: [u8; 32] = derive_from_path(TWEAK_DERIVATION_PREFIX, predecessor_id, path);
    Tweak::new(hash)
}
```

**File:** crates/near-mpc-crypto-types/src/kdf.rs (L19-23)
```rust
pub fn derive_app_id(predecessor_id: &AccountId, derivation_path: &str) -> CkdAppId {
    let hash: [u8; 32] =
        derive_from_path(APP_ID_DERIVATION_PREFIX, predecessor_id, derivation_path);
    hash.into()
}
```

**File:** crates/near-mpc-crypto-types/src/kdf.rs (L25-38)
```rust
fn derive_from_path(derivation_prefix: &str, predecessor_id: &AccountId, path: &str) -> [u8; 32] {
    // TODO: Use a key derivation library instead of doing this manually.
    // https://crates.io/crates/hkdf might be a good option?
    //
    // ',' is ACCOUNT_DATA_SEPARATOR from nearcore that indicate the end
    // of the account id in the trie key. We reuse the same constant to
    // indicate the end of the account id in derivation path.
    // Do not reuse this hash function on anything that isn't an account
    // ID or it'll be vulnerable to Hash Malleability/extension attacks.
    let derivation_path = format!("{derivation_prefix}{},{}", predecessor_id, path);
    let mut hasher = Sha3_256::new();
    hasher.update(derivation_path);
    let hash: [u8; 32] = hasher.finalize().into();
    hash
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

**File:** crates/contract/src/lib.rs (L379-384)
```rust
        let request = SignatureRequest::new(
            request.domain_id,
            request.payload,
            &predecessor,
            &request.path,
        );
```
