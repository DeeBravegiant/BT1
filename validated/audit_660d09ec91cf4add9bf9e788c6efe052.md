### Title
Duplicate Sub-Script Counting in `RequireMOf` Native Script Evaluation Allows M-of-N Threshold Bypass — (File: `eras/shelley/impl/src/Cardano/Ledger/Shelley/Scripts.hs`)

---

### Summary

The `evalMultiSig` function evaluates `RequireMOf m msigs` by iterating over a `StrictSeq` (ordered sequence, not a set) of sub-scripts and counting each element independently. Because the sub-script list permits duplicate entries, a script author can craft a `RequireMOf m` script containing the same `RequireSignature kh` repeated `m` times. A single key holder then satisfies the full threshold alone, bypassing the intended M-of-N multi-party requirement. The same pattern is present in `evalTimelock` (Allegra+) and `evalDijkstraNativeScript` (Dijkstra era).

---

### Finding Description

`evalMultiSig` in `eras/shelley/impl/src/Cardano/Ledger/Shelley/Scripts.hs` implements the `RequireMOf` case via the helper `isValidMOf`:

```haskell
isValidMOf n StrictSeq.Empty = n <= 0
isValidMOf n (msig StrictSeq.:<| msigs) =
  n <= 0 || if go msig then isValidMOf (n - 1) msigs else isValidMOf n msigs
``` [1](#0-0) 

Each element of the `StrictSeq` is evaluated independently and decrements the counter by 1 when it evaluates to `True`. The sub-script list is typed as `StrictSeq (MultiSig era)` — an ordered sequence that structurally allows duplicate entries: [2](#0-1) 

The CBOR decoder for `MultiSigMOf` decodes a plain list with no uniqueness check: [3](#0-2) 

A script author can therefore serialize and submit a script such as:

```
RequireMOf 2 [RequireSignature kh_alice, RequireSignature kh_alice]
```

When `kh_alice` is present in the witness set `vhks`, `isValidMOf` decrements the counter twice (once per list position), returning `True` with only one distinct signer. The same flaw is present in `evalTimelock`: [4](#0-3) 

and in `evalDijkstraNativeScript`: [5](#0-4) 

Note that the transaction witness set itself is a `Set (WitVKey Witness)`, so a transaction submitter cannot introduce duplicate witnesses at submission time. The duplication must be embedded in the script's sub-script list at script-creation time, making the script author the attacker.

The formal specification in `eras/shelley/formal-spec/multi-sig.tex` defines the `RequireMOf` evaluation using set cardinality (`card { t s.t. t ← ts ∧ evalMultiSigScript t vhks }`), which would deduplicate identical sub-scripts and prevent this bypass: [6](#0-5) 

The implementation diverges from this specification by counting list positions rather than distinct satisfied sub-scripts.

---

### Impact Explanation

A malicious script author (e.g., Alice) can create a `RequireMOf 2 [RequireSignature alice, RequireSignature alice]` script and present it to a co-signer (Bob) as a genuine 2-of-2 multisig. Bob, observing a script with `m=2` and two entries, sends ADA to the derived address. Alice can then spend those funds unilaterally with only her own signature, satisfying both list positions. This constitutes **direct loss of ADA through a ledger state transition that the co-signer did not authorize**, matching the Critical impact class. The same mechanism applies to stake credential scripts (unauthorized delegation) and reward account scripts (unauthorized withdrawal).

---

### Likelihood Explanation

The attacker must be the script author and must persuade a victim to fund an address without independently verifying the script's sub-script list for duplicates. This is a realistic scenario in wallet-to-wallet multisig arrangements, shared custody setups, or any context where one party generates the script on behalf of others. No privileged access, consensus majority, or key compromise is required — only the ability to author and publish a script, which is available to any unprivileged transaction sender.

---

### Recommendation

1. **Decoder-level:** Reject `MultiSigMOf` / `TimelockMOf` scripts during CBOR deserialization if the sub-script list contains structurally duplicate entries (compare by memoized bytes).
2. **Evaluator-level:** Align `isValidMOf` with the formal spec by deduplicating the sub-script sequence before counting, e.g., converting to a set of distinct sub-scripts before the threshold check.
3. Apply the same fix to `evalTimelock` (Allegra/Mary/Alonzo/Babbage/Conway) and `evalDijkstraNativeScript` (Dijkstra).

---

### Proof of Concept

```haskell
-- Attacker constructs a script that appears to be 2-of-2 but is actually 1-of-1
maliciousScript :: MultiSig ShelleyEra
maliciousScript =
  RequireMOf 2 (StrictSeq.fromList [RequireSignature kh_alice, RequireSignature kh_alice])

-- Evaluation with vhks = {kh_alice}:
-- isValidMOf 2 [RequireSignature kh_alice, RequireSignature kh_alice]
--   => kh_alice ∈ vhks → True → isValidMOf 1 [RequireSignature kh_alice]
--   => kh_alice ∈ vhks → True → isValidMOf 0 []
--   => 0 <= 0 → True
-- Script passes with only Alice's signature.
```

Bob sends ADA to `hash(maliciousScript)` believing it is a 2-of-2 multisig. Alice submits a transaction spending that UTxO with only her own `WitVKey`. The UTXOW rule calls `validateMultiSig`, which calls `evalMultiSig {kh_alice} maliciousScript`, returns `True`, and the transaction is accepted — Bob's funds are lost. [7](#0-6)

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

**File:** eras/shelley/impl/src/Cardano/Ledger/Shelley/Scripts.hs (L305-307)
```haskell
    isValidMOf n StrictSeq.Empty = n <= 0
    isValidMOf n (msig StrictSeq.:<| msigs) =
      n <= 0 || if go msig then isValidMOf (n - 1) msigs else isValidMOf n msigs
```

**File:** eras/shelley/impl/src/Cardano/Ledger/Shelley/Scripts.hs (L316-322)
```haskell
validateMultiSig ::
  (ShelleyEraScript era, EraTx era, NativeScript era ~ MultiSig era) =>
  Tx t era ->
  NativeScript era ->
  Bool
validateMultiSig tx =
  evalMultiSig $ Set.map witVKeyHash (tx ^. witsTxL . addrTxWitsL)
```

**File:** eras/allegra/impl/src/Cardano/Ledger/Allegra/Scripts.hs (L487-489)
```haskell
    isValidMOf n SSeq.Empty = n <= 0
    isValidMOf n (ts SSeq.:<| tss) =
      n <= 0 || if go ts then isValidMOf (n - 1) tss else isValidMOf n tss
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Scripts.hs (L565-567)
```haskell
    isValidMOf n SSeq.Empty = n <= 0
    isValidMOf n (ts SSeq.:<| tss) =
      n <= 0 || if go ts then isValidMOf (n - 1) tss else isValidMOf n tss
```

**File:** eras/shelley/formal-spec/multi-sig.tex (L807-813)
```tex
    \fun{evalMultiSigScript} & ~(\type{RequireMOf}~m~ts)~\var{vhks} = \\
                             & m \leq \Sigma
                               (\textrm{card} \{ t s.t. t \leftarrow ts \wedge \fun{evalMultiSigScript}~\var{t}~\var{vhks}
%                               \left(
%                               [\textrm{if}~(\fun{evalMultiSigScript}~\var{t}~\var{vhks})~
%                               \textrm{then}~1~\textrm{else}~0\vert t \leftarrow ts]
%                               \right)
```
