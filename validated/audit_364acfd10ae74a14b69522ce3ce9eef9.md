Let me look at the SUBLEDGERS and SubLedger rules to check if they have their own ref script size validation.

Now I have all the evidence needed. Let me analyze the complete call chain.

### Title
`validateAllRefScriptSize` Skipped on `IsValid False` Top-Level Tx, Allowing Sub-Transaction Reference Script Size Limit Bypass — (`eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Ledger.hs`)

---

### Summary

In `dijkstraLedgerTransition`, the call to `validateAllRefScriptSize` — which enforces the `ppMaxRefScriptSizePerTx` limit across the top-level transaction and all its sub-transactions — is guarded inside the `IsValid True` branch. The SUBLEDGERS rule is invoked unconditionally before this branch. Neither SUBLEDGER nor SUBUTXOW contains any ref-script-size check. An unprivileged attacker can therefore craft a top-level `DijkstraTx` with `isValid = IsValid False` and embed sub-transactions whose combined reference-script footprint exceeds `ppMaxRefScriptSizePerTx`, and the ledger will accept the batch without raising `DijkstraTxRefScriptsSizeTooBig`.

---

### Finding Description

`dijkstraLedgerTransition` executes in this order:

1. **SUBLEDGERS (unconditional)** — all sub-transactions are processed.
2. **`if tx ^. isValidTxL == IsValid True`** — only in this branch is `validateAllRefScriptSize` called.
3. **`else`** — the `IsValid False` path returns immediately with no size check. [1](#0-0) [2](#0-1) 

`validateAllRefScriptSize` delegates to `batchNonDistinctRefScriptsSize`, which sums the reference-script sizes of the top-level tx **and every sub-transaction**: [3](#0-2) [4](#0-3) 

The SUBLEDGER rule has **no** ref-script-size predicate failure constructor, and the codebase explicitly marks any attempt to inject `ConwayTxRefScriptsSizeTooBig` into SUBLEDGER as `error "Impossible"`: [5](#0-4) [6](#0-5) 

SUBUTXOW is called unconditionally inside SUBLEDGER regardless of `topIsValid`, and its predicate-failure type contains no ref-script-size constructor either: [7](#0-6) [8](#0-7) 

A grep across all Dijkstra rule files for `validateRefScriptSize`, `ppMaxRefScriptSizePerTx`, and `RefScriptsSizeTooBig` returns zero hits outside `Ledger.hs`, confirming there is no compensating check anywhere in the sub-transaction path.

---

### Impact Explanation

**Medium** — Attacker-controlled transactions exceed intended validation limits.

The `ppMaxRefScriptSizePerTx` limit (200 KiB in Conway) is a hard resource cap designed to bound per-transaction validation cost. By setting `isValid = False` on the top-level tx, an attacker can include sub-transactions whose aggregate reference-script footprint exceeds this cap — up to the block-level limit of 1 MiB — forcing every validating node to load and process 5× the intended maximum reference-script data for a single transaction batch. This is a deterministic, protocol-rule bypass, not a probabilistic or spam-only attack.

---

### Likelihood Explanation

Any unprivileged user can submit such a transaction. The only prerequisites are:

1. Pre-existing UTxO outputs containing large reference scripts (achievable with ordinary transactions).
2. A top-level `DijkstraTx` with `isValid = IsValid False` and sub-transactions whose `referenceInputsTxBodyL` / `inputsTxBodyL` point to those outputs.

No governance majority, privileged key, or third-party compromise is required. The exploit is fully local-testable.

---

### Recommendation

Move `validateAllRefScriptSize` (or an equivalent per-batch check) **outside** the `IsValid True` guard so it executes unconditionally, mirroring how `validateTreasuryValue` is placed inside the guard but the SUBLEDGERS call is not. Alternatively, add a dedicated ref-script-size check in `dijkstraSubLedgersTransition` / `dijkstraSubLedgersTransition` that fires regardless of `topIsValid`. [2](#0-1) 

---

### Proof of Concept

```
1. Produce N UTxO outputs each carrying a reference script of size S,
   where N × S > ppMaxRefScriptSizePerTx (e.g. 201 KiB total).

2. Build a DijkstraTx:
     isValid      = IsValid False
     subTxs       = [ subTx { referenceInputs = {those N outputs} } ]
     collateral   = { some key-locked input }

3. Submit via LEDGER.

4. Assert: the ledger accepts the transaction without raising
   DijkstraTxRefScriptsSizeTooBig.
   (Under correct behaviour it should reject with that failure.)
```

### Citations

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Ledger.hs (L321-329)
```haskell
validateAllRefScriptSize pp utxo tx =
  let totalRefScriptSize = batchNonDistinctRefScriptsSize utxo tx
      maxRefScriptSizePerTx = fromIntegral @Word32 @Int $ pp ^. ppMaxRefScriptSizePerTxG
   in failureUnless (totalRefScriptSize <= maxRefScriptSizePerTx) $
        DijkstraTxRefScriptsSizeTooBig
          Mismatch
            { mismatchSupplied = totalRefScriptSize
            , mismatchExpected = maxRefScriptSizePerTx
            }
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Ledger.hs (L369-383)
```haskell
  -- Process all subtransactions first
  LedgerState utxoStateAfterSubLedgers certStateAfterSubLedgers <-
    trans @(EraRule "SUBLEDGERS" era) $
      TRC
        ( SubLedgerEnv
            slot
            mbCurEpochNo
            txIx
            pp
            chainAccountState
            originalUtxo
            (tx ^. isValidTxL)
        , ledgerState
        , subStAnnTxs
        )
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Ledger.hs (L387-393)
```haskell
  (utxoStateBeforeUtxow, certStateFinal) <-
    if tx ^. isValidTxL == IsValid True
      then do
        let txBody = tx ^. bodyTxL
        runTest $ Conway.validateTreasuryValue txBody (chainAccountState ^. casTreasuryL)
        runTest $ validateAllRefScriptSize pp originalUtxo tx

```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs (L264-277)
```haskell
batchNonDistinctRefScriptsSize ::
  ( EraTx era
  , DijkstraEraTxBody era
  ) =>
  UTxO era ->
  Tx TopTx era ->
  Int
batchNonDistinctRefScriptsSize utxo tx =
  txNonDistinctRefScriptsSize utxo tx
    + getSum
      ( foldMap'
          (Sum . txNonDistinctRefScriptsSize utxo)
          (tx ^. bodyTxL . subTransactionsTxBodyL)
      )
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubLedger.hs (L105-110)
```haskell
data DijkstraSubLedgerPredFailure era
  = SubUtxowFailure (PredicateFailure (EraRule "SUBUTXOW" era))
  | SubEntitiesFailure (PredicateFailure (EraRule "SUBENTITIES" era))
  | SubGovFailure (PredicateFailure (EraRule "SUBGOV" era))
  | SubTreasuryValueMismatch (Mismatch RelEQ Coin)
  deriving (Generic)
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubLedger.hs (L287-293)
```haskell
  utxoStateAfterSubUtxow <-
    trans @(EraRule "SUBUTXOW" era) $
      TRC
        ( SubUtxoEnv slot pp certState originalUtxo topIsValid
        , utxoStateBeforeSubUtxow
        , stAnnTx
        )
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubLedger.hs (L375-376)
```haskell
  Conway.ConwayTxRefScriptsSizeTooBig _ -> error "Impossible: `ConwayTxRefScriptsSizeTooBig` for SUBLEDGER"
  Conway.ConwayMempoolFailure _ -> error "Impossible: `ConwayMempoolFailure` for SUBLEDGER"
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubUtxow.hs (L67-111)
```haskell
data DijkstraSubUtxowPredFailure era
  = SubUtxoFailure (PredicateFailure (EraRule "SUBUTXO" era))
  | SubInvalidWitnessesUTXOW (NonEmpty (VKey Witness))
  | -- | witnesses which failed in verifiedWits function
    SubMissingVKeyWitnessesUTXOW
      -- | witnesses which were needed and not supplied
      (NonEmptySet (KeyHash Witness))
  | -- | failed scripts
    SubScriptWitnessNotValidatingUTXOW (NonEmptySet ScriptHash)
  | -- | hash of the full metadata
    SubMissingTxBodyMetadataHash TxAuxDataHash
  | -- | hash of the metadata included in the transaction body
    SubMissingTxMetadata TxAuxDataHash
  | SubConflictingMetadataHash (Mismatch RelEQ TxAuxDataHash)
  | -- | Contains out of range values (string`s too long)
    SubInvalidMetadata
  | SubMissingRedeemers (NonEmpty (PlutusPurpose AsItem era, ScriptHash))
  | SubMissingRequiredDatums
      -- | Set of missing data hashes
      (NonEmptySet DataHash)
      -- | Set of received data hashes
      (Set DataHash)
  | SubNotAllowedSupplementalDatums
      -- | Set of unallowed data hashes.
      (NonEmptySet DataHash)
      -- | Set of acceptable supplemental data hashes
      (Set DataHash)
  | SubPPViewHashesDontMatch
      (Mismatch RelEQ (StrictMaybe ScriptIntegrityHash))
  | -- | Set of transaction inputs that are TwoPhase scripts, and should have a DataHash but don't
    SubUnspendableUTxONoDatumHash
      (NonEmptySet TxIn)
  | -- | List of redeemers not needed
    SubExtraRedeemers (NonEmpty (PlutusPurpose AsIx era))
  | -- | Embed UTXO rule failures
    SubMalformedScriptWitnesses (NonEmptySet ScriptHash)
  | -- | the set of malformed script witnesses
    SubMalformedReferenceScripts (NonEmptySet ScriptHash)
  | -- | The computed script integrity hash does not match the provided script integrity hash
    SubScriptIntegrityHashMismatch
      (Mismatch RelEQ (StrictMaybe ScriptIntegrityHash))
      (StrictMaybe ByteString)
  | -- | Guard credentials with incorrect datum presence in requiredTopLevelGuards
    SubMalformedGuardDatums (NonEmptySet (Credential Guard))
  deriving (Generic)
```
