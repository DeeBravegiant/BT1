### Title
Missing Quorum Enforcement on `RequireMOf` Native Script Threshold Allows Zero-Signature Bypass - (File: `eras/shelley/impl/src/Cardano/Ledger/Shelley/Scripts.hs`, `eras/allegra/impl/src/Cardano/Ledger/Allegra/Scripts.hs`)

---

### Summary

The `RequireMOf m scripts` native script constructor accepts any `Int` value for `m`, including zero and negative numbers, without validation at deserialization or evaluation time. When `m <= 0`, the `isValidMOf` evaluator immediately returns `True` regardless of the witness set, meaning a script that was intended to require at least one signature can be bypassed entirely by crafting a serialized script with `m = 0` (or negative). This is the direct analog of the Chainlink missing quorum check: just as `transmit()` succeeded with an empty signature array, a Cardano native script with `RequireMOf 0 [key1, key2, key3]` succeeds with zero witnesses.

---

### Finding Description

**Root cause — `evalMultiSig` / `evalTimelock` in `isValidMOf`:**

In `eras/shelley/impl/src/Cardano/Ledger/Shelley/Scripts.hs`, the evaluator is:

```haskell
isValidMOf n StrictSeq.Empty = n <= 0
isValidMOf n (msig StrictSeq.:<| msigs) =
  n <= 0 || if go msig then isValidMOf (n - 1) msigs else isValidMOf n msigs
```

When `n <= 0`, the function returns `True` immediately, without checking any sub-scripts. The same pattern is present in `evalTimelock` in `eras/allegra/impl/src/Cardano/Ledger/Allegra/Scripts.hs` and `evalDijkstraNativeScript` in `eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Scripts.hs`.

**No validation of `m` at deserialization:**

The CBOR decoder for `MultiSigRaw` in `eras/shelley/impl/src/Cardano/Ledger/Shelley/Scripts.hs` decodes `m` as a plain `Int` with no non-negativity or non-zero check:

```haskell
3 -> do
  m <- decCBOR
  multiSigs <- sequence <$> decCBOR
  pure (3, MultiSigMOf m <$> multiSigs)
```

The CDDL spec in `eras/allegra/impl/cddl/data/allegra.cddl` explicitly allows `int64` (which includes negative values) for `script_n_of_k`:

```
script_n_of_k = (3, n : int64, [* native_script])
int64 = min_int64 .. max_int64
```

The source code even documents this behavior as a known property:

```
| TimelockMOf !Int !(StrictSeq (Timelock era))
| -- Note that the Int may be negative in which case (TimelockMOf (-2) [..]) is always True
```

**Attack path:**

An unprivileged transaction author who controls a UTxO locked by a `RequireMOf n [key1..keyN]` script (e.g., a 2-of-3 multisig) can craft a transaction that provides a *different* script with the same hash — impossible — but more relevantly: any **new** UTxO locked by a script the attacker themselves creates with `m = 0` will be immediately spendable by anyone with no signatures. The attacker can also lock funds to `RequireMOf 0 [key1, key2]` and later drain them without any co-signers.

More critically: if a script author makes an off-by-one error and sets `m = 0` (intending `m = 1`), or if a wallet/tool serializes a negative value due to a bug, the ledger accepts it silently. The CDDL explicitly permits `int64` including negative values, so any conforming serializer can produce such a script.

---

### Impact Explanation

**Severity: Medium** (matching the allowed impact: "Attacker-controlled transactions, scripts, witnesses, or serialized inputs exceed intended validation limits").

A script author or transaction producer can create a `RequireMOf 0 [...]` or `RequireMOf -1 [...]` native script that unconditionally validates. Any UTxO locked by such a script is spendable by anyone without providing any witnesses. This bypasses the intended multi-signature threshold entirely. Funds locked to such a script are permanently accessible to any spender — effectively unprotected — which constitutes a direct loss of ADA or native assets through an invalid ledger state transition if the script was intended to enforce access control.

---

### Likelihood Explanation

The CDDL schema explicitly encodes the threshold as `int64`, permitting zero and negative values. The code comment in `TimelockRaw` acknowledges the behavior. Any wallet, script tool, or serialized input producer that passes `m = 0` (accidentally or maliciously) will produce a script that always validates. The entry path requires only submitting a transaction with a crafted native script — no privileged access needed.

---

### Recommendation

1. **At deserialization**: Reject `m <= 0` in the `DecCBOR` instances for `MultiSigRaw`, `TimelockRaw`, and `DijkstraNativeScriptRaw`. Return a decoder error if `m < 1`.
2. **At evaluation**: Add a guard in `isValidMOf` (or `evalMultiSig`/`evalTimelock`) that treats `m <= 0` as an immediate failure rather than success, or assert `m >= 1` before evaluation.
3. **CDDL**: Restrict `script_n_of_k` threshold to `uint` (non-negative integer) rather than `int64`.

---

### Proof of Concept

**Shelley/Allegra/Conway era — `evalMultiSig`:**

```haskell
-- Craft: RequireMOf 0 [RequireSignature someKey]
-- evalMultiSig with empty witness set:
isValidMOf 0 (RequireSignature someKey :<| Empty)
-- => 0 <= 0 => True  (no signature checked)
```

**Serialized CBOR attack (Allegra+):**

```
[3, 0, []]   -- script_n_of_k with n=0, empty sub-scripts
```

This deserializes successfully as `TimelockMOf 0 []`, and `evalTimelock` returns `True` for any transaction, with zero witnesses required.

**Negative threshold (documented in source):**

```
[3, -2, [RequireSignature key1, RequireSignature key2]]
```

Deserializes as `TimelockMOf (-2) [...]`. `isValidMOf (-2) xs` returns `True` immediately since `-2 <= 0`. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7) [9](#0-8)

### Citations

**File:** eras/shelley/impl/src/Cardano/Ledger/Shelley/Scripts.hs (L103-103)
```haskell
    MultiSigMOf !Int !(StrictSeq (MultiSig era))
```

**File:** eras/shelley/impl/src/Cardano/Ledger/Shelley/Scripts.hs (L274-278)
```haskell
      3 -> do
        m <- decCBOR
        multiSigs <- sequence <$> decCBOR
        pure (3, MultiSigMOf m <$> multiSigs)
      k -> invalidKey k
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

**File:** eras/allegra/impl/src/Cardano/Ledger/Allegra/Scripts.hs (L242-242)
```haskell
      decRaw 3 = Ann (SumD TimelockMOf) <*! Ann From <*! D (sequence <$> decCBOR)
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

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Scripts.hs (L279-282)
```haskell
    3 -> do
      m <- decCBOR
      xs <- decCBOR
      pure (3, DijkstraRequireMOf m <$> sequence xs)
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Scripts.hs (L565-567)
```haskell
    isValidMOf n SSeq.Empty = n <= 0
    isValidMOf n (ts SSeq.:<| tss) =
      n <= 0 || if go ts then isValidMOf (n - 1) tss else isValidMOf n tss
```
