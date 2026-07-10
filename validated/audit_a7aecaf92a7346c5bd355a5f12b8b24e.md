### Title
EVM Log `data` Field Lacks Hex Normalization, Causing Cross-Node Payload Hash Divergence and Permanent Bridge Execution Failure - (File: crates/foreign-chain-rpc-interfaces/src/evm.rs)

---

### Summary

The `Log.data` field in the EVM inspector pipeline is stored and propagated as a raw `String` without any hex-case normalization. Different RPC providers legitimately return the same log data in different hex cases (e.g., `"0xDEADBEEF"` vs `"0xdeadbeef"`). Because this string is included verbatim in the Borsh-serialized `ForeignTxSignPayload`, nodes querying different providers produce different `payload_hash` values. This prevents the threshold of nodes from agreeing on a single signed payload, permanently blocking the `verify_foreign_transaction` request and locking the user's bridge deposit.

---

### Finding Description

The `Log` struct used by the EVM inspector pipeline contains a `data: String` field that is deserialized directly from the JSON-RPC response and passed through to the contract-level `EvmLog` DTO without normalization:

```rust
// crates/foreign-chain-rpc-interfaces/src/evm.rs
pub struct Log {
    pub address: H160,
    pub data: String,   // ← raw hex string, case not normalized
    pub topics: Vec<H256>,
    ...
}
``` [1](#0-0) 

The conversion to the contract-level DTO passes `data` verbatim:

```rust
// crates/foreign-chain-inspector/src/contract_interface_conversions.rs
fn log_to_evm_log(value: Log) -> dtos::EvmLog {
    dtos::EvmLog {
        data: value.data,   // ← no normalization
        ...
    }
}
``` [2](#0-1) 

The contract-level `EvmLog` also stores `data` as a raw `String`: [3](#0-2) 

This `EvmLog` is embedded in `EvmExtractedValue::Log`, which is included in `ForeignTxSignPayloadV1.values`. The payload hash is computed by Borsh-serializing the entire struct and SHA-256 hashing it: [4](#0-3) 

The `FanOut` inspector compares extracted values using `PartialEq`. Since `String` comparison is byte-exact, `"0xDEADBEEF" != "0xdeadbeef"`, causing `InspectorResponseMismatch` when a node's providers disagree on hex case: [5](#0-4) 

By contrast, the Aptos inspector explicitly normalizes its string fields — `normalize_type_tag()` lowercases addresses and `normalize_event_data()` sorts JSON keys — precisely to prevent this class of divergence: [6](#0-5) 

The EVM inspector has no equivalent normalization for `Log.data`.

---

### Impact Explanation

**Impact: Medium** — Request-lifecycle and bridge execution-flow invariant broken.

When a `verify_foreign_transaction` request targets an EVM log whose `data` field is returned in different hex cases by different RPC providers:

1. **Per-node FanOut failure**: A node whose configured providers disagree on `data` case receives `InspectorResponseMismatch` and produces no signature share.
2. **Cross-node hash divergence**: Even if each node's FanOut succeeds (single provider per node), nodes querying different providers produce different `payload_hash` values. The contract receives `respond_verify_foreign_tx` calls with different hashes; no single hash reaches the signing threshold.
3. **Permanent request failure**: The `verify_foreign_transaction` yield is never resolved. The user's bridge deposit on the foreign chain is locked with no recourse.

This breaks the production safety invariant that a valid, finalized foreign-chain transaction can always be attested by the MPC network.

---

### Likelihood Explanation

**Likelihood: Medium** — The Ethereum JSON-RPC specification does not mandate hex case for the `data` field. Major providers (Alchemy, Infura, QuickNode, Ankr) have historically returned hex data in different cases. Any bridge transaction whose log `data` is returned in mixed case across the node operator's provider set triggers this failure. The failure is silent from the user's perspective and permanent.

---

### Recommendation

Normalize the `log.data` field to canonical lowercase hex (with `0x` prefix) in `log_to_evm_log` before it is stored in `EvmLog`, mirroring the normalization already applied in the Aptos inspector:

```rust
fn log_to_evm_log(value: Log) -> dtos::EvmLog {
    dtos::EvmLog {
        data: normalize_hex_string(&value.data),
        ...
    }
}

fn normalize_hex_string(s: &str) -> String {
    let hex = s.strip_prefix("0x").unwrap_or(s);
    format!("0x{}", hex.to_ascii_lowercase())
}
```

This should be applied at the point of extraction in `EvmExtractor::extract_value`, before the `Log` is returned as an `EvmExtractedValue`, so that both the per-node FanOut comparison and the cross-node payload hash are computed over a canonical form.

---

### Proof of Concept

1. Deploy the MPC network with two nodes, each configured with a single EVM RPC provider.
2. Submit `verify_foreign_transaction` for an EVM transaction containing a log with non-empty `data`.
3. Configure Node A's provider to return `data: "0xDEADBEEF"` and Node B's provider to return `data: "0xdeadbeef"` (both are valid per the JSON-RPC spec).
4. Node A computes `payload_hash = SHA256(borsh(ForeignTxSignPayloadV1 { ..., data: "0xDEADBEEF" }))`.
5. Node B computes `payload_hash = SHA256(borsh(ForeignTxSignPayloadV1 { ..., data: "0xdeadbeef" }))`.
6. The two hashes differ. Each node calls `respond_verify_foreign_tx` with its own hash. Neither hash reaches the signing threshold of 2.
7. The yield promise is never resolved; the user's bridge deposit is permanently locked.

The root cause is confirmed at:
- `crates/foreign-chain-rpc-interfaces/src/evm.rs` line 98 (`data: String` field, no normalization on deserialization)
- `crates/foreign-chain-inspector/src/contract_interface_conversions.rs` lines 67 (`data: value.data`, verbatim copy)
- `crates/near-mpc-contract-interface/src/types/foreign_chain.rs` line 871 (`data: String` in `EvmLog`, Borsh-serialized into the signed payload)

### Citations

**File:** crates/foreign-chain-rpc-interfaces/src/evm.rs (L90-100)
```rust
pub struct Log {
    pub removed: bool,
    pub log_index: U64,
    pub transaction_index: U64,
    pub transaction_hash: H256,
    pub block_hash: H256,
    pub block_number: U64,
    pub address: H160,
    pub data: String,
    pub topics: Vec<H256>,
}
```

**File:** crates/foreign-chain-inspector/src/contract_interface_conversions.rs (L58-74)
```rust
fn log_to_evm_log(value: Log) -> dtos::EvmLog {
    dtos::EvmLog {
        removed: value.removed,
        log_index: value.log_index.as_u64(),
        transaction_index: value.transaction_index.as_u64(),
        transaction_hash: dtos::Hash256(value.transaction_hash.0),
        block_hash: dtos::Hash256(value.block_hash.0),
        block_number: value.block_number.as_u64(),
        address: dtos::Hash160(value.address.0),
        data: value.data,
        topics: value
            .topics
            .into_iter()
            .map(|t| dtos::Hash256(t.0))
            .collect(),
    }
}
```

**File:** crates/near-mpc-contract-interface/src/types/foreign_chain.rs (L863-873)
```rust
pub struct EvmLog {
    pub removed: bool,
    pub log_index: u64,
    pub transaction_index: u64,
    pub transaction_hash: Hash256,
    pub block_hash: Hash256,
    pub block_number: u64,
    pub address: Hash160,
    pub data: String,
    pub topics: Vec<Hash256>,
}
```

**File:** crates/near-mpc-contract-interface/src/types/foreign_chain.rs (L1504-1509)
```rust
impl ForeignTxSignPayload {
    pub fn compute_msg_hash(&self) -> std::io::Result<Hash256> {
        let mut hasher = sha2::Sha256::new();
        borsh::BorshSerialize::serialize(self, &mut hasher)?;
        Ok(Hash256(hasher.finalize().into()))
    }
```

**File:** crates/foreign-chain-inspector/src/lib.rs (L37-57)
```rust
/// Combines multiple inspectors that target the same chain into a single inspector.
///
/// All inner inspectors are queried concurrently. The fan-out treats every
/// non-transient outcome (success or non-transient error, see
/// [`ForeignChainInspectionError::is_transient`]) as a substantive verdict that must
/// agree across inspectors. Transient errors (network issues, finality not yet reached,
/// etc.) are tolerated so that a single unavailable RPC does not take the whole node
/// out of signing.
///
/// Outcomes:
/// * All substantive verdicts are `Ok` with the same extracted values → returns those values.
/// * All substantive verdicts are non-transient errors of the same variant → returns one of
///   them (e.g. all inspectors agree the transaction failed).
/// * Substantive verdicts disagree (`Ok` vs. non-transient error, two different non-transient
///   error variants, or two different success values) → returns
///   [`ForeignChainInspectionError::InspectorResponseMismatch`].
/// * Every inspector returned a transient error → the first such error is propagated.
///
/// Variant-level comparison is used for non-transient errors, so inspectors that all report
/// the same failure mode (e.g. `NonCanonicalBlock`) are considered to agree even if the
/// inner fields differ.
```

**File:** crates/foreign-chain-inspector/src/aptos/inspector.rs (L174-193)
```rust
/// Rewrites every address inside a Move struct tag to the canonical hex-literal form the
/// Aptos API emits — lowercase with leading zeros trimmed — so providers that return
/// long-form addresses converge to the same signed payload.
///
/// Examples:
/// - `0x0000…0001::coin::Coin` → `0x1::coin::Coin`
/// - `0xDEADbeef::bridge::InitTransfer` → `0xdeadbeef::bridge::InitTransfer`
/// - `0x1::coin::CoinStore<0x000a::lp::LP<0x0B::x::Y, u64>>` →
///   `0x1::coin::CoinStore<0xa::lp::LP<0xb::x::Y, u64>>`
///
/// Addresses appear at the start of the tag or of a generic type argument — that is, right
/// after `<`, `,` or a space — and are always followed by `::`. Splitting on those delimiters
/// (keeping them) yields pieces that each begin at a potential address position, so only a
/// leading `0x<hex>::` of a piece is rewritten; anything else (identifiers that merely contain
/// `0x` such as a module named `m0x01`, primitive type args, …) is copied verbatim.
fn normalize_type_tag(tag: &str) -> String {
    tag.split_inclusive(['<', ',', ' '])
        .map(normalize_leading_address)
        .collect()
}
```
