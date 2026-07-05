### Title
`RequireMOf` Native Script Evaluator Accepts Negative Threshold, Trivially Bypassing Signature Validation - (`File: eras/shelley/impl/src/Cardano/Ledger/Shelley/Scripts.hs`)

---

### Summary

The `RequireMOf` native script constructor accepts a signed integer (`Int`) for its threshold parameter `m`. Neither the CBOR decoder nor the script evaluator validates that `m >= 0`. Because the evaluator's base case is `n <= 0`, any negative value of `m` causes the script to trivially pass without requiring any signatures. An attacker can craft a `RequireMOf (-1) [sig1, sig2]` script that appears to be a 2-of-2 multisig but actually requires zero signatures to satisfy.

---

### Finding Description

**Root cause — CBOR decoder accepts negative `m`:**

The `MultiSigRaw` decoder reads `m` as a plain `Int` with no non-negativity check:

```haskell
3 -> do
  m <- decCBOR
  multiSigs <- sequence <$> decCBOR
  pure (3, MultiSigMOf m <$> multiSigs)
``` [1](#0-0) 

The same pattern is present in the Dijkstra native script decoder: [2](#0-1) 

**Root cause — evaluator base case trivially passes for any `n <= 0`:**

```haskell
isValidMOf n StrictSeq.Empty = n <= 0
isValidMOf n (msig StrictSeq.:<| msigs) =
  n <= 0 || if go msig then isValidMOf (n - 1) msigs else isValidMOf n msigs
``` [3](#0-2) 

When `m = -1`, the very first call `isValidMOf (-1) xs` evaluates `(-1) <= 0 = True` and returns immediately, regardless of what signatures are present. The same logic is replicated in `evalTimelock` for Allegra/Alonzo/Babbage/Conway: [4](#0-3) 

And in `evalDijkstraNativeScript`: [5](#0-4) 

**Discrepancy with the formal specification:**

The formal spec defines `RequireMOf m ts` with `m ∈ ℕ` (natural numbers), meaning `m` must be non-negative. However, the CDDL wire format uses `int64` (signed):

```
script_n_of_k = (3, n : int64, [* native_script])
```

The formal spec itself even notes the missing check in a comment: *"n.b., should also check that this is >= 0"*. [6](#0-5) 

The Haskell type `MultiSigMOf !Int` stores a signed integer, and no validation is performed at deserialization time. [7](#0-6) 

---

### Impact Explanation

An attacker who crafts a `RequireMOf (-1) [sig1, sig2]` script and convinces a victim to lock ADA or native assets at the corresponding script hash can spend those funds in a transaction that provides **zero** key-hash witnesses. The UTXOW rule calls `evalMultiSig` / `evalTimelock`, which returns `True` unconditionally for any negative threshold, so the transaction is accepted as valid. This constitutes a direct, attacker-controlled loss of ADA or native assets through an invalid ledger state transition — matching the **Critical** impact class.

---

### Likelihood Explanation

The attack requires social engineering: the victim must be convinced to send funds to the script hash of the attacker's crafted script (e.g., presented as a legitimate 2-of-2 multisig). The script contains the expected key hashes, so a casual inspection of the script body looks plausible; only careful inspection of the threshold field reveals the negative value. This is directly analogous to the LSP6 finding where `0x0000` in the allowed-data-keys list was the non-obvious backdoor. The social engineering requirement reduces likelihood but does not eliminate it.

---

### Recommendation

1. **Decoder-level guard**: In `DecCBOR (Annotator (MultiSigRaw era))` and `DecCBOR (Annotator (DijkstraNativeScriptRaw era))`, after decoding `m`, fail if `m < 0`:
   ```haskell
   3 -> do
     m <- decCBOR
     when (m < 0) $ fail "RequireMOf: threshold must be non-negative"
     ...
   ```
2. **CDDL correction**: Change `n : int64` to `n : uint` in `script_n_of_k` across all era CDDL files to align with the formal specification's `m ∈ ℕ`.
3. **Evaluator guard** (defense-in-depth): Add `| m < 0 = False` as a guard in `isValidMOf` so that even a script that bypasses deserialization cannot trivially pass.

---

### Proof of Concept

Construct the CBOR encoding of `RequireMOf (-1) [RequireSignature kh]` (tag `3`, integer `-1`, list of one sub-script). Submit a transaction spending a UTxO locked by the hash of this script with an empty witness set. The ledger accepts the transaction because `isValidMOf (-1) [RequireSignature kh]` evaluates `(-1) <= 0 = True` immediately, returning `True` without checking `kh ∈ vhks`.

### Citations

**File:** eras/shelley/impl/src/Cardano/Ledger/Shelley/Scripts.hs (L102-103)
```haskell
  | -- | Require M of the given sub-terms to be satisfied.
    MultiSigMOf !Int !(StrictSeq (MultiSig era))
```

**File:** eras/shelley/impl/src/Cardano/Ledger/Shelley/Scripts.hs (L274-277)
```haskell
      3 -> do
        m <- decCBOR
        multiSigs <- sequence <$> decCBOR
        pure (3, MultiSigMOf m <$> multiSigs)
```

**File:** eras/shelley/impl/src/Cardano/Ledger/Shelley/Scripts.hs (L305-312)
```haskell
    isValidMOf n StrictSeq.Empty = n <= 0
    isValidMOf n (msig StrictSeq.:<| msigs) =
      n <= 0 || if go msig then isValidMOf (n - 1) msigs else isValidMOf n msigs
    go = \case
      RequireSignature hk -> Set.member hk vhks
      RequireAllOf msigs -> all go msigs
      RequireAnyOf msigs -> any go msigs
      RequireMOf m msigs -> isValidMOf m msigs
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Scripts.hs (L279-282)
```haskell
    3 -> do
      m <- decCBOR
      xs <- decCBOR
      pure (3, DijkstraRequireMOf m <$> sequence xs)
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Scripts.hs (L565-574)
```haskell
    isValidMOf n SSeq.Empty = n <= 0
    isValidMOf n (ts SSeq.:<| tss) =
      n <= 0 || if go ts then isValidMOf (n - 1) tss else isValidMOf n tss
    go = \case
      RequireTimeStart lockStart -> lockStart `lteNegInfty` txStart
      RequireTimeExpire lockExp -> txExp `ltePosInfty` lockExp
      RequireSignature hash -> hash `Set.member` keyHashes
      RequireAllOf xs -> all go xs
      RequireAnyOf xs -> any go xs
      RequireMOf m xs -> isValidMOf m xs
```

**File:** eras/allegra/impl/src/Cardano/Ledger/Allegra/Scripts.hs (L487-496)
```haskell
    isValidMOf n SSeq.Empty = n <= 0
    isValidMOf n (ts SSeq.:<| tss) =
      n <= 0 || if go ts then isValidMOf (n - 1) tss else isValidMOf n tss
    go = \case
      RequireTimeStart lockStart -> lockStart `lteNegInfty` txStart
      RequireTimeExpire lockExp -> txExp `ltePosInfty` lockExp
      RequireSignature hash -> hash `Set.member` vhks
      RequireAllOf xs -> all go xs
      RequireAnyOf xs -> any go xs
      RequireMOf m xs -> isValidMOf m xs
```

**File:** eras/shelley/formal-spec/multi-sig.tex (L751-754)
```tex
                -- ^ Minimum number of signatures required to unlock
                --   the output (should not exceed @length signatories@)
                --   n.b., should also check that this is >= 0
                }
```
