### Title
Sub-Transaction Plutus ExUnits Bypass Per-Transaction and Block-Level Resource Limits in Dijkstra Era — (`eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubUtxo.hs`, `eras/alonzo/impl/src/Cardano/Ledger/Alonzo/Tx.hs`, `eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Bbody.hs`)

---

### Summary

In the Dijkstra era, nested sub-transactions (`SubTx`) can carry Plutus script redeemers with arbitrary `ExUnits` declarations. These ExUnits are never validated against `maxTxExUnits`, never aggregated into the block-level `maxBlockExUnits` check, and never included in the minimum-fee calculation. An unprivileged transaction sender can therefore embed arbitrarily expensive Plutus script executions inside sub-transactions, causing a block to perform far more total Plutus computation than the protocol parameters were designed to allow, while paying only the byte-size component of the fee.

---

### Finding Description

**`totExUnits` is blind to sub-transaction redeemers.**

`totExUnits` is defined as:

```haskell
totExUnits tx = foldMap snd $ tx ^. witsTxL . rdmrsTxWitsL . unRedeemersL
```

It reads only from the transaction's own witness set. For a `DijkstraTx TopTx`, the `witsTxL` lens reaches `dtWits`, which contains only the top-level redeemers. The sub-transactions stored in `dtbrSubTransactions :: !(OMap TxId (Tx SubTx era))` each carry their own `dstWits :: !(TxWits era)` with independent redeemer maps, but `totExUnits` never traverses them. [1](#0-0) [2](#0-1) 

**Per-transaction ExUnits check is top-level only.**

The Dijkstra UTXO rule calls:

```haskell
{- totExunits tx ≤ maxTxExUnits pp -}
runTest $ Alonzo.validateExUnitsTooBigUTxO pp tx
```

where `tx` is the `TopTx`. Sub-transaction ExUnits are invisible to this check. [3](#0-2) 

**Block-level ExUnits check is top-level only.**

`dijkstraBbodyTransition` calls:

```haskell
Alonzo.validateExUnits @era txs $ pp ^. ppMaxBlockExUnitsL
```

`validateExUnits` folds `totExUnits` over `txs :: StrictSeq (Tx TopTx era)` — the sequence of top-level transactions only. Sub-transaction ExUnits are never summed. [4](#0-3) [5](#0-4) 

**SUBUTXO rule has no ExUnits validation at all.**

`dijkstraSubUtxoTransition` performs validity-interval, input-set, output-size, network-id, and output-min-value checks, but contains no call to `validateExUnitsTooBigUTxO` or any equivalent. The `DijkstraSubUtxoPredFailure` type has no `ExUnitsTooBig` constructor. The conversion function `dijkstraUtxoToDijkstraSubUtxoPredFailure` explicitly marks `ExUnitsTooBigUTxO` as `error "Impossible"`, confirming the omission is structural. [6](#0-5) [7](#0-6) 

**Fee calculation excludes sub-transaction ExUnits.**

`alonzoMinFeeTx` (inherited by Dijkstra) computes:

```haskell
txscriptfee (pp ^. ppPricesL) allExunits
  where allExunits = totExUnits tx
```

Since `totExUnits` is top-level only, the `txscriptfee` component of the minimum fee does not cover sub-transaction Plutus execution. The attacker pays only the byte-size fee for the sub-transaction bytes, not for the ExUnits they declare. [8](#0-7) 

---

### Impact Explanation

`maxBlockExUnits` is the protocol mechanism that bounds total Plutus execution time per block, ensuring block validation time stays within the network's slot budget. By embedding Plutus scripts exclusively in sub-transactions, an attacker can cause a block to perform an unbounded multiple of `maxBlockExUnits` worth of actual Plutus computation while the BBODY check reports zero sub-transaction ExUnits. This exceeds the intended validation limits set by the protocol parameters. Additionally, sub-transaction Plutus execution is obtained without paying the `txscriptfee` component, modifying effective fees outside design parameters.

This maps to: **Medium — Attacker-controlled transactions exceed intended validation limits or modify fees outside design parameters.**

---

### Likelihood Explanation

Any unprivileged transaction sender can construct a `DijkstraTx TopTx` containing sub-transactions with Plutus redeemers declaring large ExUnits. The transaction is valid under all current ledger checks. A block producer will include it because it passes mempool and LEDGER validation. No special privilege, key compromise, or governance majority is required. The Dijkstra era is the only era where this is possible (prior eras have no sub-transactions), so the attack surface is new and specific to this era.

---

### Recommendation

1. **Aggregate sub-transaction ExUnits in `totExUnits`**: For `DijkstraTx TopTx`, extend `totExUnits` to recursively sum ExUnits from all sub-transactions in `dtbrSubTransactions`.

2. **Add per-sub-transaction ExUnits check in SUBUTXO**: Call `validateExUnitsTooBigUTxO pp tx` inside `dijkstraSubUtxoTransition`, and add a corresponding `SubExUnitsTooBigUTxO` constructor to `DijkstraSubUtxoPredFailure`.

3. **Include sub-transaction ExUnits in the minimum fee**: Ensure `alonzoMinFeeTx` (or the Dijkstra override) sums ExUnits across all sub-transactions when computing `txscriptfee`.

---

### Proof of Concept

1. Construct a `DijkstraTx TopTx` whose own redeemers declare `ExUnits 0 0` (within `maxTxExUnits`).
2. Embed N `DijkstraSubTx` sub-transactions, each with a Plutus redeemer declaring `ExUnits maxMem maxSteps` (where `maxMem` and `maxSteps` are the per-transaction limits).
3. Submit the transaction. The Dijkstra UTXO rule checks `totExUnits topTx ≤ maxTxExUnits` — passes (top-level ExUnits are 0).
4. A block producer includes the transaction. `dijkstraBbodyTransition` calls `Alonzo.validateExUnits txs ppMax` — passes (sub-tx ExUnits not counted).
5. All nodes accept the block. The actual Plutus execution performed is `N × maxTxExUnits`, which can be made arbitrarily larger than `maxBlockExUnits` by increasing N.
6. The minimum fee paid covers only the byte size of the sub-transactions, not their ExUnits, because `totExUnits` returns 0 for the top-level transaction.

### Citations

**File:** eras/alonzo/impl/src/Cardano/Ledger/Alonzo/Tx.hs (L375-388)
```haskell
alonzoMinFeeTx ::
  ( EraTx era
  , AlonzoEraTxWits era
  , AlonzoEraPParams era
  ) =>
  PParams era ->
  Tx l era ->
  Coin
alonzoMinFeeTx pp tx =
  (tx ^. sizeTxF <×> (fromCompact . unCoinPerByte) (pp ^. ppTxFeePerByteL))
    <+> (pp ^. ppTxFeeFixedL)
    <+> txscriptfee (pp ^. ppPricesL) allExunits
  where
    allExunits = totExUnits tx
```

**File:** eras/alonzo/impl/src/Cardano/Ledger/Alonzo/Tx.hs (L390-394)
```haskell
totExUnits ::
  (EraTx era, AlonzoEraTxWits era) =>
  Tx l era ->
  ExUnits
totExUnits tx = foldMap snd $ tx ^. witsTxL . rdmrsTxWitsL . unRedeemersL
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxBody.hs (L163-188)
```haskell
data DijkstraTxBodyRaw l era where
  DijkstraTxBodyRaw ::
    { dtbrSpendInputs :: !(Set TxIn)
    , dtbrCollateralInputs :: !(Set TxIn)
    , dtbrReferenceInputs :: !(Set TxIn)
    , dtbrOutputs :: !(StrictSeq (Sized (TxOut era)))
    , dtbrCollateralReturn :: !(StrictMaybe (Sized (TxOut era)))
    , dtbrTotalCollateral :: !(StrictMaybe Coin)
    , dtbrCerts :: !(OSet.OSet (TxCert era))
    , dtbrWithdrawals :: !Withdrawals
    , dtbrFee :: !Coin
    , dtbrVldt :: !ValidityInterval
    , dtbrGuards :: !(OSet (Credential Guard))
    , dtbrMint :: !MultiAsset
    , dtbrScriptIntegrityHash :: !(StrictMaybe ScriptIntegrityHash)
    , dtbrAuxDataHash :: !(StrictMaybe TxAuxDataHash)
    , dtbrNetworkId :: !(StrictMaybe Network)
    , dtbrVotingProcedures :: !(VotingProcedures era)
    , dtbrProposalProcedures :: !(OSet.OSet (ProposalProcedure era))
    , dtbrCurrentTreasuryValue :: !(StrictMaybe Coin)
    , dtbrTreasuryDonation :: !Coin
    , dtbrSubTransactions :: !(OMap TxId (Tx SubTx era))
    , dtbrDirectDeposits :: !DirectDeposits
    , dtbrAccountBalanceIntervals :: !(AccountBalanceIntervals era)
    } ->
    DijkstraTxBodyRaw TopTx era
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Utxo.hs (L414-415)
```haskell
  {- totExunits tx ≤ maxTxExUnits pp -}
  runTest $ Alonzo.validateExUnitsTooBigUTxO pp tx
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Bbody.hs (L361-361)
```haskell
  Alonzo.validateExUnits @era txs $ pp ^. ppMaxBlockExUnitsL
```

**File:** eras/alonzo/impl/src/Cardano/Ledger/Alonzo/Rules/Bbody.hs (L158-167)
```haskell
validateExUnits txs ppMax =
  let txTotal = foldMap totExUnits txs
   in pointWiseExUnits (<=) txTotal ppMax
        ?! injectFailure
          ( TooManyExUnits $
              Mismatch
                { mismatchSupplied = txTotal
                , mismatchExpected = ppMax
                }
          )
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubUtxo.hs (L231-278)
```haskell
dijkstraSubUtxoTransition = do
  TRC (SubUtxoEnv slot pp certState originalUtxo (IsValid isValid), utxoState, stAnnTx) <-
    judgmentContext
  let tx = stAnnTx ^. txStAnnTxG

  let txBody = tx ^. bodyTxL

  runTest $ Allegra.validateOutsideValidityIntervalUTxO slot txBody

  sysSt <- liftSTS $ asks systemStart
  ei <- liftSTS $ asks epochInfo
  runTest $ Alonzo.validateOutsideForecast ei slot sysSt tx

  let allSizedOutputs = txBody ^. allSizedOutputsTxBodyF
  let allOutputs = fmap sizedValue allSizedOutputs
  runTest $ Alonzo.validateOutputTooBigUTxO pp allOutputs

  runTest $ Shelley.validateInputSetEmptyUTxO txBody

  let inputs = txBody ^. inputsTxBodyL
  let refInputs = txBody ^. referenceInputsTxBodyL
  runTest $ Shelley.validateBadInputsUTxO originalUtxo (inputs `Set.union` refInputs)
  runTest $ Shelley.validateBadInputsUTxO (utxosUtxo utxoState) inputs

  runTestOnSignal $ Shelley.validateOutputBootAddrAttrsTooBig allOutputs

  runTestOnSignal $ Babbage.validateOutputTooSmallUTxO pp allSizedOutputs

  netId <- liftSTS $ asks networkId
  runTestOnSignal $ Shelley.validateWrongNetwork netId allOutputs
  runTestOnSignal $ Shelley.validateWrongNetworkWithdrawal netId txBody
  runTestOnSignal $ validateWrongNetworkInDirectDeposit netId txBody
  runTestOnSignal $ Alonzo.validateWrongNetworkInTxBody netId txBody

  if isValid
    then do
      newState <-
        Shelley.updateUTxOStateNoFees
          pp
          utxoState
          txBody
          certState
          (utxosGovState utxoState)
          (tellEvent . TotalDeposits (hashAnnotated txBody))
          (\a b -> tellEvent $ TxUTxODiff a b)
      pure $ newState & utxosDonationL <>~ txBody ^. treasuryDonationTxBodyL
    else
      pure utxoState
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubUtxo.hs (L340-340)
```haskell
  ExUnitsTooBigUTxO _ -> error "Impossible: `ExUnitsTooBigUTxO` for SUBUTXO"
```
