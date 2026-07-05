### Title
Negative Threshold in `RequireMOf` / `TimelockMOf` Native Script Always Validates, Bypassing Intended Signature Requirements — (File: `eras/shelley/impl/src/Cardano/Ledger/Shelley/Scripts.hs`, `eras/allegra/impl/src/Cardano/Ledger/Allegra/Scripts.hs`)

---

### Summary

The `RequireMOf` native script constructor (both `MultiSigMOf` in Shelley and `TimelockMOf` in Allegra/Mary/Alonzo/Babbage/Conway/Dijkstra) stores its threshold as a signed `Int` and the on-chain CDDL encoding uses `int64`, which permits negative values. The `isValidMOf` evaluator immediately returns `True` whenever `n <= 0`, so any script with a negative threshold unconditionally validates regardless of the signatures present. This diverges from the formal specification, which defines the threshold as `m ∈ ℕ` (natural numbers), and allows a script author to craft a minting policy or spending/staking script that appears to require signatures but actually requires none.

---

### Finding Description

**Root cause — `isValidMOf` short-circuits on any non-positive `n`:**

In `eras/shelley/impl/src/Cardano/Ledger/Shelley/Scripts.hs`, `evalMultiSig` contains:

```haskell
isValidMOf n StrictSeq.Empty = n <= 0
isValidMOf n (msig StrictSeq.:<| msigs) =
  n <= 0 || if go msig then isValidMOf (n - 1) msigs else isValidMOf n msigs
```

The base case `n <= 0` is `True` for any negative `n`, so `isValidMOf (-1) [RequireSignature alice]` returns `True` immediately without evaluating any sub-scripts.

The identical pattern appears in `evalTimelock` in `eras/allegra/impl/src/Cardano/Ledger/Allegra/Scripts.hs` and `evalDijkstraNativeScript` in `eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Scripts.hs`.

The code itself acknowledges this:

```haskell
| TimelockMOf !Int !(StrictSeq (Timelock era))
| -- Note that the Int may be negative in which case (TimelockMOf (-2) [..]) is always True
```

**On-chain encoding permits negative values:**

The CDDL specification in `eras/allegra/impl/cddl/data/allegra.cddl` encodes the threshold as `int64`:

```
script_n_of_k = (3, n : int64, [* native_script])
int64 = min_int64 .. max_int64
min_int64 = -9223372036854775808
```

The deserialization path in `eras/shelley/impl/src/Cardano/Ledger/Shelley/Scripts.hs` decodes `m` with a plain `decCBOR` into `Int` with no non-negativity check:

```haskell
3 -> do
  m <- decCBOR
  multiSigs <- sequence <$> decCBOR
  pure (3, MultiSigMOf m <$> multiSigs)
```

**Formal spec divergence:**

The formal specification (`eras/shelley-ma/formal-spec/timelock-language.tex`) defines the constructor type as `MOfN ∈ ℕ → [Timelock] → Timelock`, restricting `m` to natural numbers. The implementation accepts the full `int64` range without enforcing `m ≥ 0`.

**Exploit path:**

A script author submits a transaction containing a minting policy or spending script of the form `RequireMOf (-1) [RequireSignature alice]`. The ledger deserializes the script, hashes it to produce the policy/script ID, and when the script is evaluated, `isValidMOf (-1) [...]` returns `True` immediately. No witness for `alice` is required. The transaction is accepted as valid.

---

### Impact Explanation

**Impact: Medium** — Attacker-controlled scripts exceed intended validation limits.

A script author can deploy a minting policy with a negative threshold that unconditionally validates. This allows:

1. **Unlimited token minting without required signatures**: A policy `RequireMOf (-1) [RequireSignature alice]` lets any transaction mint tokens under that policy ID without Alice's signature, bypassing the intended authorization requirement.
2. **Unguarded spending scripts**: A UTxO locked by `RequireMOf (-1) [RequireSignature alice]` can be spent by any transaction without providing Alice's signature.
3. **Staking credential bypass**: A staking credential backed by such a script can be delegated or used for withdrawals without the intended signature.

This matches the allowed impact: *"Attacker-controlled transactions, blocks, certificates, votes, proposals, scripts, witnesses, or serialized inputs exceed intended validation limits."*

---

### Likelihood Explanation

**Likelihood: Medium.**

- Any unprivileged user can author a native script; no privileged access is required.
- The CDDL encoding explicitly permits `int64`, so a crafted CBOR payload with a negative threshold is accepted by the deserializer without error.
- Tooling bugs (e.g., integer overflow when converting a large positive threshold) can silently produce negative thresholds, creating insecure scripts without the author's awareness.
- The ledger performs no non-negativity check at deserialization or validation time.

---

### Recommendation

1. **Short term**: Add a non-negativity guard in the `DecCBOR` instance for `MultiSigRaw` and `TimelockRaw`. Reject any `RequireMOf` / `TimelockMOf` script where the decoded threshold `m < 0` with a deserialization error.
2. **Short term**: Add the same guard in `evalMultiSig` / `evalTimelock` / `evalDijkstraNativeScript` as a defense-in-depth check: treat `m < 0` as a script failure rather than an unconditional pass.
3. **Long term**: Align the CDDL specification (`script_n_of_k`) to use `uint` instead of `int64` for the threshold field, matching the formal specification's `m ∈ ℕ` constraint.
4. **Long term**: Add property-based tests that verify `RequireMOf n scripts` with `n < 0` is rejected at deserialization and/or evaluates to `False`.

---

### Proof of Concept

**Step 1**: Craft a native script with a negative threshold:
```
RequireMOf (-1) [RequireSignature <alice_keyhash>]
```
Encoded in CBOR as `[3, -1, [[0, <alice_keyhash>]]]`.

**Step 2**: Derive the script hash (policy ID) from this script.

**Step 3**: Submit a transaction that mints tokens under this policy ID, providing **no witness for alice**. Include the script in the transaction witnesses.

**Step 4**: The ledger calls `evalMultiSig` (or `evalTimelock`) on the script. `isValidMOf (-1) [RequireSignature alice]` evaluates `(-1) <= 0` → `True` immediately. The script validates.

**Step 5**: The transaction is accepted. Tokens are minted without Alice's signature, violating the intended authorization requirement.

**Relevant code locations**:
- `isValidMOf` short-circuit: [1](#0-0) 
- `TimelockMOf` negative-n comment: [2](#0-1) 
- `evalTimelock` `isValidMOf`: [3](#0-2) 
- CDDL `int64` encoding: [4](#0-3) 
- Deserialization without non-negativity check: [5](#0-4) 
- Formal spec type constraint `m ∈ ℕ`: [6](#0-5)

### Citations

**File:** eras/shelley/impl/src/Cardano/Ledger/Shelley/Scripts.hs (L274-277)
```haskell
      3 -> do
        m <- decCBOR
        multiSigs <- sequence <$> decCBOR
        pure (3, MultiSigMOf m <$> multiSigs)
```

**File:** eras/shelley/impl/src/Cardano/Ledger/Shelley/Scripts.hs (L305-307)
```haskell
    isValidMOf n StrictSeq.Empty = n <= 0
    isValidMOf n (msig StrictSeq.:<| msigs) =
      n <= 0 || if go msig then isValidMOf (n - 1) msigs else isValidMOf n msigs
```

**File:** eras/allegra/impl/src/Cardano/Ledger/Allegra/Scripts.hs (L183-184)
```haskell
  | TimelockMOf !Int !(StrictSeq (Timelock era))
  | -- Note that the Int may be negative in which case (TimelockMOf (-2) [..]) is always True
```

**File:** eras/allegra/impl/src/Cardano/Ledger/Allegra/Scripts.hs (L487-489)
```haskell
    isValidMOf n SSeq.Empty = n <= 0
    isValidMOf n (ts SSeq.:<| tss) =
      n <= 0 || if go ts then isValidMOf (n - 1) tss else isValidMOf n tss
```

**File:** eras/allegra/impl/cddl/data/allegra.cddl (L293-299)
```text
script_n_of_k = (3, n : int64, [* native_script])

int64 = min_int64 .. max_int64

min_int64 = -9223372036854775808

max_int64 = 9223372036854775807
```

**File:** eras/shelley-ma/formal-spec/timelock-language.tex (L58-58)
```tex
    & \type{MOfN} & \in \N \to \seqof{\Timelock} \to \Timelock & \\
```
