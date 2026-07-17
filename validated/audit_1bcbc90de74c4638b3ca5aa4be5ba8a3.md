### Title
Missing G2 Subgroup Membership Check in `alt_bn128_pairing_check` Enables Cofactor Attack — (`runtime/near-vm-runner/src/logic/alt_bn128.rs`)

### Summary

`decode_g2` accepts any point on the BN254 twist curve without verifying prime-order subgroup membership. The `zeropool-bn` crate (`bn = { package = "zeropool-bn", version = "0.5.11" }`) does not perform a subgroup check in `AffineG2::new`. An attacker can supply a G2 point in the h₂-torsion (cofactor subgroup) that causes `bn::pairing_batch` to return `Gt::one()` after final exponentiation, making `pairing_check` return `true` for a pair that does not satisfy the pairing equation.

### Finding Description

`decode_g2` at line 131–142 calls `bn::AffineG2::new(x, y)`, which only validates that the point satisfies the twist curve equation Y² = X³ + b/ξ over Fq2. It does **not** multiply by the group order r to verify the point is in the r-torsion subgroup G2. [1](#0-0) 

`pairing_check` then passes these unvalidated points directly to `bn::pairing_batch`: [2](#0-1) 

There is no subgroup check anywhere in `alt_bn128.rs`. The `zeropool-bn` crate's Ate pairing implementation computes the Miller loop and final exponentiation without verifying that the G2 input is in the prime-order subgroup. [3](#0-2) 

The documented contract in `logic.rs` explicitly states that "point is not in the subgroup" should return `AltBn128InvalidInput`, but the implementation does not enforce this: [4](#0-3) 

The dependency is confirmed in the workspace manifest: [5](#0-4) 

### Impact Explanation

BN254's G2 twist group E'(Fq2) has order h₂ · r where h₂ is a large cofactor coprime to r. For a point T in the h₂-torsion (not in G2), the optimal Ate pairing satisfies e(P, T)^r = e(r·P, T) = e(O, T) = 1, so e(P, T) is an r-th root of unity. After the final exponentiation step `(p^12 - 1)/r`, the Miller loop result for a pure h₂-torsion point evaluates to `Gt::one()`. This means `pairing_check([(G1_gen, T)])` returns `true` (1) even though T is not a valid G2 element and the pairing equation is not satisfied.

A contract implementing a Groth16 or similar ZK-SNARK verifier that calls `alt_bn128_pairing_check` can be bypassed: the attacker substitutes a valid G2 proof element with a crafted h₂-torsion point, the host function returns 1, the contract writes `proof_valid=true` to storage, and the resulting state root differs from what a correct verifier would produce. All nodes execute identically, so the incorrect state root is accepted by consensus — the error is in the semantic correctness of the computation, not in node disagreement.

### Likelihood Explanation

The attack requires only:
1. Deploying a contract that uses `alt_bn128_pairing_check` for ZK-SNARK verification (a standard use case for this precompile).
2. Computing a point T on the BN254 twist in the h₂-torsion — this is straightforward: take any random point Q on the twist and compute T = r · Q. T has order dividing h₂ and is not in G2 (with overwhelming probability).
3. Submitting a transaction with T as the G2 component of the forged proof.

No validator privileges, no special access, and no cryptographic hardness assumption is required. The construction is deterministic and publicly known.

### Recommendation

Add an explicit subgroup membership check in `decode_g2` after curve-membership validation. The standard approach is to verify that r · P = O (the point at infinity). Alternatively, use a library that performs this check internally (e.g., the `blst` crate already used for BLS12-381, which calls `blst_p2_affine_in_g2` explicitly): [6](#0-5) 

For `alt_bn128`, after `bn::AffineG2::new` succeeds, multiply the resulting point by the scalar r and assert the result is the point at infinity before returning it.

### Proof of Concept

```
1. Compute T = r * Q for any random Q on the BN254 twist (T is in h₂-torsion, not G2).
2. Encode T as 128 bytes (two Fq2 coordinates, little-endian u256 each).
3. Encode G1_gen as 64 bytes.
4. Call alt_bn128_pairing_check with input = G1_gen_bytes || T_bytes (192 bytes total).
5. Observe return value = 1 (true), despite T not being a valid G2 element.
6. A ZK-SNARK verifier contract using this host function accepts the forged proof and
   writes proof_valid=true to storage, producing an incorrect state root.
```

### Citations

**File:** runtime/near-vm-runner/src/logic/alt_bn128.rs (L77-93)
```rust
pub(crate) fn pairing_check(
    elements: &[[u8; PAIRING_CHECK_ELEMENT_SIZE]],
) -> Result<bool, InvalidInput> {
    let elements: Vec<(bn::G1, bn::G2)> = elements
        .iter()
        .map(|chunk| {
            let (g1, g2) = stdx::split_array(chunk);
            let g1 = decode_g1(g1)?;
            let g2 = decode_g2(g2)?;
            Ok((g1, g2))
        })
        .collect::<Result<Vec<_>, InvalidInput>>()?;

    let res = bn::pairing_batch(&elements) == bn::Gt::one();

    Ok(res)
}
```

**File:** runtime/near-vm-runner/src/logic/alt_bn128.rs (L131-142)
```rust
fn decode_g2(raw: &[u8; 2 * POINT_SIZE]) -> Result<bn::G2, InvalidInput> {
    let (x, y) = stdx::split_array(raw);
    let x = decode_fq2(x)?;
    let y = decode_fq2(y)?;
    if x.is_zero() && y.is_zero() {
        Ok(bn::G2::zero())
    } else {
        bn::AffineG2::new(x, y)
            .map_err(|_err| InvalidInput::new("invalid g2", raw))
            .map(bn::G2::from)
    }
}
```

**File:** runtime/near-vm-runner/src/logic/logic.rs (L1112-1114)
```rust
    /// If point coordinates are not on curve, point is not in the subgroup, scalar
    /// is not in the field or data are wrong serialized, for example,
    /// `value.len()%192!=0`, the function returns `AltBn128InvalidInput`.
```

**File:** Cargo.toml (L155-155)
```text
bn = { package = "zeropool-bn", version = "0.5.11", default-features = false }
```

**File:** runtime/near-vm-runner/src/logic/bls12381.rs (L369-372)
```rust
        let g2_check = unsafe { blst::blst_p2_affine_in_g2(&blst_g2_list[i]) };
        if g2_check == false {
            return Ok(1);
        }
```
