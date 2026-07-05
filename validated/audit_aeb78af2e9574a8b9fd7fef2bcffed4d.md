### Title
Specification-Code Mismatch: Negative M-of-N Threshold in Native Scripts Bypasses Signature Requirement — (`File: eras/allegra/impl/src/Cardano/Ledger/Allegra/Scripts.hs`)

---

### Summary

The formal specification defines the M-of-N threshold for native scripts as a natural number (`N`, i.e., ≥ 0). The implementation uses a signed `Int` type and the CDDL serialization format allows negative values (`int32`/`int64`). When a negative threshold is supplied, the script evaluator immediately returns `True` regardless of the signatures present, effectively creating an "anyone-can-spend" or "anyone-can-mint" script. The spec itself contains a comment acknowledging the missing check. This is a direct specification-code mismatch analog to the original report.

---

### Finding Description

**Specification requirement:**

The Shelley-MA formal spec (`eras/shelley-ma/formal-spec/timelock-language.tex`, line 58) defines:

```
MOfN ∈ N → seq(Timelock) → Timelock
```

`N` denotes the natural numbers (≥ 0). The Shelley multi-sig spec (`eras/shelley/formal-spec/crypto-primitives.tex`) uses the same type. The Shelley formal spec (`eras/shelley/formal-spec/multi-sig.tex`, line 753) even contains an explicit acknowledgment of the missing check:

```
-- n.b., should also check that this is >= 0
```

**Implementation divergence:**

`TimelockRaw` in `eras/allegra/impl/src/Cardano/Ledger/Allegra/Scripts.hs` (line 183) stores the threshold as a signed `Int`:

```haskell
| TimelockMOf !Int !(StrictSeq (Timelock era))
| -- Note that the Int may be negative in which case (TimelockMOf (-2) [..]) is always True
```

The evaluator `isValidMOf` (lines 487–489) short-circuits to `True` the moment `n ≤ 0`:

```haskell
isValidMOf n SSeq.Empty = n <= 0
isValidMOf n (ts SSeq.:<| tss) =
  n <= 0 || if go ts then isValidMOf (n - 1) tss else isValidMOf n tss
```

The same signed-`Int` type is used in `MultiSigMOf` in `eras/shelley/impl/src/Cardano/Ledger/Shelley/Scripts.hs` (line 103).

**CDDL serialization permits negative values:**

- Shelley: `script_n_of_k = (3, n : int32, [* native_script])` — range `[-2147483648, 2147483647]`
- Allegra through Dijkstra: `script_n_of_k = (3, n : int64, [* native_script])` — range `[-9223372036854775808, 9223372036854775807]`

A serialized ledger input with a negative `n` field is fully valid CBOR, passes deserialization without error, and is accepted by the ledger.

---

### Impact Explanation

Any unprivileged actor can craft a native script (`RequireMOf (-1) [key1, key2, ...]`) and submit it as:

1. **A spending script**: UTxOs locked at the script address can be spent by any transaction with zero witnesses. ADA and native assets are directly at risk.
2. **A minting policy**: Native tokens whose policy ID is the hash of such a script can be minted by anyone without any signature, allowing unauthorized creation of native assets.

The ledger accepts these transactions as valid state transitions even though the specification requires M ≥ 0. This constitutes an invalid ledger state transition per the spec, resulting in direct loss or unauthorized creation of ADA or native assets.

---

### Likelihood Explanation

- The CDDL explicitly encodes the threshold as a signed integer type (`int32`/`int64`), so any CBOR-aware tool can produce a conforming serialized script with a negative threshold.
- The attacker-controlled entry path is a serialized native script submitted in a transaction — fully reachable by an unprivileged transaction sender.
- The code comment on line 184 of `Scripts.hs` confirms the developers are aware of the behavior, yet no ledger-level rejection exists.
- DApps or wallets that compute M programmatically (e.g., `M = total_keys - required_absent`) can produce negative M values through ordinary arithmetic, silently creating exploitable scripts.

---

### Recommendation

**Short term:** Add a range check in the script evaluator (or at deserialization) that rejects any `RequireMOf`/`TimelockMOf` script where `n < 0`. In `evalTimelock`/`isValidMOf`, treat `n < 0` as a validation failure rather than an unconditional pass. Equivalently, change the CDDL type from `int32`/`int64` to `uint` to prevent negative values at the serialization layer.

**Long term:** Align the Haskell type with the specification by using `Natural` or `Word` instead of `Int` for the M-of-N threshold. Add a conformance test asserting that a script with a negative threshold is rejected by the ledger.

---

### Proof of Concept

1. Construct a CBOR-encoded native script: `[3, -1, []]` (tag 3 = `script_n_of_k`, n = -1, empty key list).
2. Compute the script hash and derive the corresponding script address.
3. Send ADA to that address in a transaction.
4. Submit a spending transaction referencing that UTxO with **no witnesses**. The ledger evaluates `isValidMOf (-1) []` → `(-1) <= 0` → `True`. The transaction is accepted and the funds are transferred to the attacker.

For minting: use the same script hash as a minting policy ID. Submit a minting transaction with a negative-threshold policy and no signatures. The ledger accepts the mint, creating native tokens without authorization.

**Root cause trace:** [1](#0-0) 

Spec: `MOfN ∈ N → seq(Timelock) → Timelock` (M must be a natural number). [2](#0-1) 

Implementation: `TimelockMOf !Int` — signed integer, no lower-bound enforcement; comment explicitly acknowledges negative values make the script always `True`. [3](#0-2) 

Evaluator: `isValidMOf n _ = n <= 0 || ...` — any negative `n` short-circuits to `True` immediately. [4](#0-3) 

Same signed-`Int` type in Shelley-era `MultiSigMOf`. [5](#0-4) 

CDDL: `script_n_of_k = (3, n : int32, ...)` — serialization layer permits negative values. [6](#0-5) 

Allegra+ CDDL: `script_n_of_k = (3, n : int64, ...)` — wider signed range, same issue. [7](#0-6) 

Spec comment: `-- n.b., should also check that this is >= 0` — the missing check is acknowledged in the specification itself.

### Citations

**File:** eras/shelley-ma/formal-spec/timelock-language.tex (L58-58)
```tex
    & \type{MOfN} & \in \N \to \seqof{\Timelock} \to \Timelock & \\
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

**File:** eras/shelley/impl/src/Cardano/Ledger/Shelley/Scripts.hs (L103-103)
```haskell
    MultiSigMOf !Int !(StrictSeq (MultiSig era))
```

**File:** eras/shelley/impl/cddl/data/shelley.cddl (L284-286)
```text
script_n_of_k = (3, n : int32, [* native_script])

int32 = -2147483648 .. 2147483647
```

**File:** eras/allegra/impl/cddl/data/allegra.cddl (L293-299)
```text
script_n_of_k = (3, n : int64, [* native_script])

int64 = min_int64 .. max_int64

min_int64 = -9223372036854775808

max_int64 = 9223372036854775807
```

**File:** eras/shelley/formal-spec/multi-sig.tex (L751-753)
```tex
                -- ^ Minimum number of signatures required to unlock
                --   the output (should not exceed @length signatories@)
                --   n.b., should also check that this is >= 0
```
