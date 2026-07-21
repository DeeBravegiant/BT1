### Title
`ValidResourceBounds` Protobuf Deserialization Produces Wrong Transaction Hash for V3 Transactions with Zero L2/Data Gas - (`File: crates/apollo_protobuf/src/converters/transaction.rs`)

### Summary

The protobuf deserializer for `ValidResourceBounds` uses the *values* of `l2_gas` and `l1_data_gas` to decide which enum variant to construct. When both are zero it silently produces `ValidResourceBounds::L1Gas`, but `get_tip_resource_bounds_hash` hashes `L1Gas` and `AllResources` differently (different number of Poseidon inputs). A V3 transaction that was admitted and hashed as `AllResources({l1_gas, l2_gas:0, l1_data_gas:0})` therefore gets a different transaction hash after a round-trip through protobuf serialization/deserialization, breaking the transaction commitment tree, block hash, and any RPC view that re-derives the hash from stored data.

### Finding Description

`ValidResourceBounds` is a two-variant enum:

```
L1Gas(ResourceBounds)          // pre-0.13.3: only L1 gas
AllResources(AllResourceBounds) // 0.13.3+: L1 + L2 + L1-data gas
``` [1](#0-0) 

`get_tip_resource_bounds_hash` hashes these two variants with a **different number of Poseidon inputs**:

- `L1Gas` → `Poseidon(tip, concat(l1_gas, L1_GAS), concat(zero, L2_GAS))` — **2 resource felts**
- `AllResources` → `Poseidon(tip, concat(l1_gas, L1_GAS), concat(zero, L2_GAS), concat(zero, L1_DATA_GAS))` — **3 resource felts** [2](#0-1) 

The protobuf deserializer decides which variant to construct by inspecting the decoded values:

```rust
Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
    ValidResourceBounds::L1Gas(l1_gas)
} else {
    ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
})
``` [3](#0-2) 

A post-0.13.3 V3 transaction submitted with `l2_gas = 0` and `l1_data_gas = 0` (valid — the user simply does not consume those resources) is admitted by the gateway as `AllResources` and hashed as **H1** (3-input Poseidon). When that transaction is later serialized to protobuf and deserialized — during P2P block sync or any storage round-trip that goes through the protobuf layer — the deserializer produces `L1Gas` and the hash recomputed from the stored data is **H2** (2-input Poseidon). H1 ≠ H2.

The same ambiguity exists in the RPC v0.8 layer:

```rust
impl From<ResourceBoundsMapping> for ValidResourceBounds {
    fn from(value: ResourceBoundsMapping) -> Self {
        if value.l1_data_gas.is_zero() && value.l2_gas.is_zero() {
            Self::L1Gas(value.l1_gas)
        } else { ... }
    }
}
``` [4](#0-3) 

The gateway and consensus paths use `AllResourceBounds` directly (never `ValidResourceBounds`), so the hash is computed correctly at admission time: [5](#0-4) 

But the P2P sync path for historical blocks uses `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds`, which triggers the wrong variant selection.

### Impact Explanation

**High — RPC/sync view returns an authoritative-looking wrong value.**

1. The proposer builds a block containing a V3 transaction with `AllResources({l1_gas, l2_gas:0, l1_data_gas:0})`. The transaction commitment tree leaf is keyed on H1.
2. A syncing node receives the block over P2P. The protobuf deserializer produces `L1Gas`, so the node recomputes H2 ≠ H1.
3. The syncing node's transaction commitment tree diverges from the canonical one, causing block hash verification to fail or the node to store the wrong hash.
4. Any RPC call that re-derives the transaction hash from stored data (`starknet_getTransactionByHash`, `starknet_getTransactionReceipt`, fee estimation, simulation) returns a wrong or missing result.

### Likelihood Explanation

A V3 transaction with zero L2 gas and zero L1 data gas is syntactically valid and passes all gateway admission checks (the stateless validator accepts zero resource bounds). Any user or contract that submits a V3 invoke with only an L1 gas bound and leaves L2/data gas at zero triggers this path. No special privilege is required.

### Recommendation

The variant selection must not be inferred from the values of the fields. Instead:

1. Add an explicit version/type tag to the protobuf `ResourceBounds` message (or use a separate `oneof` for `L1Gas` vs `AllResources`), so the deserializer can reconstruct the correct variant without inspecting the values.
2. Until the wire format is updated, reject (or canonicalize) any `AllResources` with zero L2 and zero L1-data gas at the point of hash computation, so that only one canonical representation exists.
3. Add a test that round-trips a V3 transaction with zero L2/data gas through protobuf serialization and asserts that the transaction hash is preserved.

### Proof of Concept

```rust
use starknet_api::transaction::fields::{
    AllResourceBounds, ResourceBounds, ValidResourceBounds, GasAmount, GasPrice,
};
use starknet_api::transaction_hash::get_tip_resource_bounds_hash;
use starknet_api::transaction::Tip;

let l1_gas = ResourceBounds {
    max_amount: GasAmount(1000),
    max_price_per_unit: GasPrice(1),
};

// Variant 1: AllResources with zero L2 and data gas (post-0.13.3 admission path)
let all_resources = ValidResourceBounds::AllResources(AllResourceBounds {
    l1_gas,
    l2_gas: ResourceBounds::default(),   // zero
    l1_data_gas: ResourceBounds::default(), // zero
});

// Variant 2: L1Gas (what the protobuf deserializer produces for the same wire bytes)
let l1_gas_only = ValidResourceBounds::L1Gas(l1_gas);

let tip = Tip(0);
let h1 = get_tip_resource_bounds_hash(&all_resources, &tip).unwrap();
let h2 = get_tip_resource_bounds_hash(&l1_gas_only, &tip).unwrap();

// h1 != h2 — same logical bounds, different transaction hashes
assert_ne!(h1, h2, "BUG: same resource bounds produce different hashes");
```

The `TryFrom<protobuf::ResourceBounds> for ValidResourceBounds` conversion at line 431 maps the `AllResources` wire encoding to `L1Gas` whenever `l2_gas` and `l1_data_gas` are zero, so any transaction that went through the proposer as `AllResources` will be re-hashed as `L1Gas` on the syncing node, producing H2 instead of H1. [6](#0-5) [7](#0-6)

### Citations

**File:** crates/starknet_api/src/transaction/fields.rs (L363-367)
```rust
#[derive(Clone, Copy, Debug, Eq, PartialEq, Hash, Ord, PartialOrd)]
pub enum ValidResourceBounds {
    L1Gas(ResourceBounds), // Pre 0.13.3. Only L1 gas. L2 bounds are signed but never used.
    AllResources(AllResourceBounds),
}
```

**File:** crates/starknet_api/src/transaction_hash.rs (L187-211)
```rust
// An implementation of the SNIP: https://github.com/EvyatarO/SNIPs/blob/snip-8/SNIPS/snip-8.md
pub fn get_tip_resource_bounds_hash(
    resource_bounds: &ValidResourceBounds,
    tip: &Tip,
) -> Result<Felt, StarknetApiError> {
    let l1_resource_bounds = resource_bounds.get_l1_bounds();
    let l2_resource_bounds = resource_bounds.get_l2_bounds();

    // L1 and L2 gas bounds always exist.
    // Old V3 txs always have L2 gas bounds of zero, but they exist.
    let mut resource_felts = vec![
        get_concat_resource(&l1_resource_bounds, L1_GAS)?,
        get_concat_resource(&l2_resource_bounds, L2_GAS)?,
    ];

    // For new V3 txs, need to also hash the data gas bounds.
    resource_felts.extend(match resource_bounds {
        ValidResourceBounds::L1Gas(_) => vec![],
        ValidResourceBounds::AllResources(all_resources) => {
            vec![get_concat_resource(&all_resources.l1_data_gas, L1_DATA_GAS)?]
        }
    });

    Ok(HashChain::new().chain(&tip.0.into()).chain_iter(resource_felts.iter()).get_poseidon_hash())
}
```

**File:** crates/apollo_protobuf/src/converters/transaction.rs (L417-436)
```rust
impl TryFrom<protobuf::ResourceBounds> for ValidResourceBounds {
    type Error = ProtobufConversionError;
    fn try_from(value: protobuf::ResourceBounds) -> Result<Self, Self::Error> {
        let Some(l1_gas) = value.l1_gas else {
            return Err(missing("ResourceBounds::l1_gas"));
        };
        let Some(l2_gas) = value.l2_gas else {
            return Err(missing("ResourceBounds::l2_gas"));
        };
        // TODO(Shahak): Assert data gas is not none once we remove support for 0.13.2.
        let l1_data_gas = value.l1_data_gas.unwrap_or_default();
        let l1_gas: ResourceBounds = l1_gas.try_into()?;
        let l2_gas: ResourceBounds = l2_gas.try_into()?;
        let l1_data_gas: ResourceBounds = l1_data_gas.try_into()?;
        Ok(if l1_data_gas.is_zero() && l2_gas.is_zero() {
            ValidResourceBounds::L1Gas(l1_gas)
        } else {
            ValidResourceBounds::AllResources(AllResourceBounds { l1_gas, l2_gas, l1_data_gas })
        })
    }
```

**File:** crates/apollo_rpc/src/v0_8/transaction.rs (L188-199)
```rust
impl From<ResourceBoundsMapping> for ValidResourceBounds {
    fn from(value: ResourceBoundsMapping) -> Self {
        if value.l1_data_gas.is_zero() && value.l2_gas.is_zero() {
            Self::L1Gas(value.l1_gas)
        } else {
            Self::AllResources(AllResourceBounds {
                l1_gas: value.l1_gas,
                l1_data_gas: value.l1_data_gas,
                l2_gas: value.l2_gas,
            })
        }
    }
```

**File:** crates/starknet_api/src/rpc_transaction.rs (L636-638)
```rust
impl InvokeTransactionV3Trait for InternalRpcInvokeTransactionV3 {
    fn resource_bounds(&self) -> ValidResourceBounds {
        ValidResourceBounds::AllResources(self.resource_bounds)
```
