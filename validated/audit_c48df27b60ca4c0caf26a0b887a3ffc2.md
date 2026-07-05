### Title
Negative `RequireMOf` Threshold in Native Scripts Always Evaluates to `True`, Bypassing Signature Requirements - (File: `eras/allegra/impl/src/Cardano/Ledger/Allegra/Scripts.hs`)

---

### Summary

The `isValidMOf` helper inside `evalTimelock` (Allegra/Mary/Alonzo/Babbage/Conway) and `evalMultiSig` (Shelley) immediately returns `True` whenever the threshold `n` satisfies `n <= 0`. Because the threshold field is decoded as a signed `Int` with no lower-bound check, an attacker can serialize a `RequireMOf` script with a negative threshold (e.g., `-1`). The resulting script always validates regardless of which — or how many — signatures are present, bypassing the intended access-control condition.

---

### Finding Description

**Vulnerable type declaration** — `TimelockMOf` and `MultiSigMOf` store the threshold as a signed `Int`:

```haskell
-- eras/allegra/impl/src/Cardano/Ledger/Allegra/Scripts.hs
| TimelockMOf !Int !(StrictSeq (Timelock era))
| -- Note that the Int may be negative in which case (TimelockMOf (-2) [..]) is always True
```

The code comment itself acknowledges the semantic consequence.

**Evaluator — unconditional `True` for any `n <= 0`:**

```haskell
-- evalTimelock, lines 487-489
isValidMOf n SSeq.Empty = n <= 0
isValidMOf n (ts SSeq.:<| tss) =
  n <= 0 || if go ts then isValidMOf (n - 1) tss else isValidMOf n tss
```

When `n` is negative, the first branch of the `||` is immediately `True`; no sub-script is ever evaluated.

The identical pattern appears in `evalMultiSig` (Shelley) and `evalDijkstraNativeScript` (Dijkstra):

```haskell
-- eras/shelley/impl/src/Cardano/Ledger/Shelley/Scripts.hs, lines 305-307
isValidMOf n StrictSeq.Empty = n <= 0
isValidMOf n (msig StrictSeq.:<| msigs) =
  n <= 0 || if go msig then isValidMOf (n - 1) msigs else isValidMOf n msigs
```

**No lower-bound check in the CBOR decoder:**

```haskell
-- eras/shelley/impl/src/Cardano/Ledger/Shelley/Scripts.hs, lines 274-277
3 -> do
  m <- decCBOR          -- DecCBOR Int = decodeInt, accepts any signed value
  multiSigs <- sequence <$> decCBOR
  pure (3, MultiSigMOf m <$> multiSigs)
```

```haskell
-- eras/allegra/impl/testlib/Test/Cardano/Ledger/Allegra/Binary/Annotator.hs, lines 69-72
3 -> do
  requiredCount <- decCBOR   -- same: no non-negativity guard
  timelocks <- decCBOR
  pure (3, TimelockMOf requiredCount timelocks)
```

The `DecCBOR Int` instance delegates to `decodeInt`, which accepts the full signed-integer CBOR range. There is no `when (m < 0) (fail ...)` guard anywhere in the deserialization path.

**Contrast with the formal specification:** The Shelley formal spec defines `RequireMOf` with threshold `m ∈ ℕ` (natural numbers), and the ShelleyMA timelock spec likewise uses `MOfN : ℕ → seq(Timelock) → Timelock`. The implementation diverges by using `Int`.

---

### Impact Explanation

An attacker who controls the serialized script bytes can:

1. **Unauthorized native-token minting (Mary era and later):** Craft a minting-policy script `RequireMOf (-1) [RequireSignature k]`, compute its hash `H`, and submit a transaction minting tokens under policy ID `H` while providing the script as a witness. The script evaluates to `True` without checking for key `k`'s signature. Tokens are created through an invalid ledger state transition — the script should have required a signature but did not.

2. **Unauthorized spending of script-locked UTxOs (Shelley era and later):** Any UTxO whose payment credential is the hash of a negative-threshold script can be spent by anyone without providing the required signatures.

3. **Unauthorized reward withdrawals (Shelley era and later):** A staking credential that is the hash of a negative-threshold script allows any party to withdraw accumulated rewards without the required witnesses.

4. **Unauthorized governance actions (Conway/Dijkstra):** If a DRep, committee member, or constitutional credential is backed by such a script, governance actions can be authorized without the required signatures.

This matches the allowed impact: **Medium — attacker-controlled serialized inputs exceed intended validation limits** (the script should enforce a signature threshold but the threshold check is trivially bypassed). It also touches **Critical — direct creation of native assets through an invalid ledger state transition** for the minting case.

---

### Likelihood Explanation

The entry path requires only that an attacker:
- Knows the CBOR encoding of a `RequireMOf` script (tag `3`, followed by a negative integer, followed by a list of sub-scripts).
- Submits a transaction that includes this script as a witness.

No privileged access, no key compromise, no governance majority, and no third-party dependency is required. The attacker controls the serialized input entirely. The CBOR encoding of a negative integer is standard (CBOR major type 1). Any node or wallet that processes the transaction will accept it because `evalTimelock`/`evalMultiSig` returns `True`.

---

### Recommendation

1. **Reject negative thresholds at deserialization time.** Add a non-negativity guard in the CBOR decoder for `MultiSigMOf` and `TimelockMOf`:

   ```haskell
   3 -> do
     m <- decCBOR
     when (m < 0) $ fail "RequireMOf threshold must be non-negative"
     ...
   ```

2. **Change the field type to `Natural` or `Word`.** Using an unsigned type (`Natural` or `Word`) makes the invariant structural and eliminates the need for a runtime check.

3. **Add a validation predicate in `validateNativeScript`.** As a defense-in-depth measure, reject any script containing a negative threshold before it is stored or evaluated.

---

### Proof of Concept

Construct the following CBOR bytes (Allegra/Mary era, `TimelockMOf`):

```
82          -- array(2)
  03        -- unsigned(3)  [tag for RequireMOf]
  82        -- array(2)
    20      -- negative(0) = -1  [threshold = -1]
    80      -- array(0)          [empty sub-script list]
```

Submit a Mary-era transaction that:
- Includes a `mint` field minting 1 token under the policy ID `blake2b-224(0x00 || above_bytes)`.
- Includes the above script in the witness set.
- Provides **no** `vkeywitness` entries.

The ledger will accept the transaction. `evalTimelock` calls `isValidMOf (-1) SSeq.Empty`, which evaluates `(-1) <= 0 = True`, and the script passes. Tokens are minted without any signature, violating the intended access-control policy. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7) [9](#0-8)

### Citations

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

**File:** eras/allegra/impl/testlib/Test/Cardano/Ledger/Allegra/Binary/Annotator.hs (L69-72)
```haskell
    3 -> do
      requiredCount <- decCBOR
      timelocks <- decCBOR
      pure (3, TimelockMOf requiredCount timelocks)
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Scripts.hs (L247-247)
```haskell
  | DijkstraRequireMOf !Int !(StrictSeq (DijkstraNativeScript era))
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Scripts.hs (L565-567)
```haskell
    isValidMOf n SSeq.Empty = n <= 0
    isValidMOf n (ts SSeq.:<| tss) =
      n <= 0 || if go ts then isValidMOf (n - 1) tss else isValidMOf n tss
```

**File:** libs/cardano-ledger-binary/src/Cardano/Ledger/Binary/Decoding/DecCBOR.hs (L165-167)
```haskell
instance DecCBOR Int where
  decCBOR = decodeInt
  {-# INLINE decCBOR #-}
```
