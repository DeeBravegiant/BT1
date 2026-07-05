### Title
Missing Execution-Unit Bound on Dijkstra Sub-Transactions Allows `maxTxExUnits`/`maxBlockExUnits` to Be Exceeded - (File: `eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubUtxo.hs`)

---

### Summary

The Dijkstra era introduces nested ("sub") transactions. The `SUBUTXO` transition rule validates each sub-transaction but never checks its declared execution units against `maxTxExUnits`. Because `totExUnits` only sums the top-level transaction's redeemers, the BBODY-level `validateExUnits` guard also ignores sub-transaction execution units. An unprivileged sender can therefore embed sub-transactions whose declared `ExUnits` are arbitrarily large, causing the actual per-block script-execution budget (`maxBlockExUnits`) to be exceeded without triggering any ledger predicate failure.

---

### Finding Description

**`totExUnits` is top-level only.**

```haskell
-- eras/alonzo/impl/src/Cardano/Ledger/Alonzo/Tx.hs
totExUnits tx = foldMap snd $ tx ^. witsTxL . rdmrsTxWitsL . unRedeemersL
```

It reads only `tx ^. witsTxL`, the top-level transaction's witness set. Sub-transactions carry their own `TxWits` accessed via `subTransactionsTxBodyL`; those redeemers are never folded in. [1](#0-0) 

**`validateExUnitsTooBigUTxO` is absent from `dijkstraSubUtxoTransition`.**

The full `SUBUTXO` transition rule performs validity-interval, forecast, output-size, input-set, bad-inputs, network-id, and output-size checks, but contains no call to `validateExUnitsTooBigUTxO`: [2](#0-1) 

Compare with the top-level Dijkstra UTXO rule, which does enforce the limit:

```haskell
{- totExunits tx ≤ maxTxExUnits pp -}
runTest $ Alonzo.validateExUnitsTooBigUTxO pp tx
``` [3](#0-2) 

**`SubMaxTxSizeUTxO` constructor exists but is never raised.**

The `DijkstraSubUtxoPredFailure` type declares `SubMaxTxSizeUTxO`, indicating a sub-transaction size check was anticipated, yet `dijkstraSubUtxoTransition` never calls `validateMaxTxSizeUTxO` for sub-transactions either: [4](#0-3) 

**BBODY `validateExUnits` is blind to sub-transaction budgets.**

```haskell
validateExUnits txs ppMax =
  let txTotal = foldMap totExUnits txs
   in pointWiseExUnits (<=) txTotal ppMax ...
``` [5](#0-4) 

Because `totExUnits` ignores sub-transaction redeemers, the block-level guard never accounts for sub-transaction script budgets.

**`ExUnits` values are compact on the wire.**

`ExUnits` is a pair of `Natural` (serialised as CBOR unsigned integers). Declaring `ExUnits maxBound maxBound` adds only ~18 bytes to a redeemer entry, so the `maxTxSize` guard (itself `runTestOnSignal`, skippable during replay) does not effectively cap declared execution units: [6](#0-5) 

---

### Impact Explanation

An attacker submits a Dijkstra top-level transaction whose own redeemers declare zero or minimal execution units (passing `validateExUnitsTooBigUTxO`) while embedding sub-transactions each declaring `ExUnits` near `maxBound`. The ledger accepts the batch because:

1. The top-level `validateExUnitsTooBigUTxO` check passes (top-level units ≤ `maxTxExUnits`).
2. The BBODY `validateExUnits` check passes (same reason).
3. No check exists in `SUBUTXO` for sub-transaction execution units.

The node must then execute the sub-transaction Plutus scripts up to their declared budgets. The aggregate script work in a single block can therefore far exceed `maxBlockExUnits`, violating the intended resource bound. This maps to the allowed **Medium** impact: *attacker-controlled transactions exceed intended validation limits*.

---

### Likelihood Explanation

**Medium.** The Dijkstra era is currently marked experimental and is not yet deployed on mainnet. However, the code is present in the production repository and the missing check is unconditional — any transaction sender who can submit a Dijkstra transaction can trigger it. No privileged role, key leak, or consensus majority is required. The attack requires only knowledge of the sub-transaction format and the ability to craft a valid top-level transaction.

---

### Recommendation

Add an execution-unit bound check inside `dijkstraSubUtxoTransition`, analogous to the top-level check:

```haskell
-- after existing checks in dijkstraSubUtxoTransition
{- totExunits subTx ≤ maxTxExUnits pp -}
runTest $ Alonzo.validateExUnitsTooBigUTxO pp tx
```

Additionally, the BBODY `validateExUnits` (or a Dijkstra-specific override) should accumulate sub-transaction execution units when computing the per-block total, so that `maxBlockExUnits` is enforced across the entire nested batch.

---

### Proof of Concept

1. Construct a Dijkstra `TopTx` with no top-level redeemers (`totExUnits = ExUnits 0 0`).
2. Embed N sub-transactions, each containing a Plutus redeemer with `ExUnits { exUnitsMem = maxBound, exUnitsSteps = maxBound }`.
3. Submit the transaction. `validateExUnitsTooBigUTxO` on the top-level passes (0 ≤ `maxTxExUnits`). `dijkstraSubUtxoTransition` runs for each sub-transaction and never checks execution units. The BBODY `validateExUnits` sums only the top-level units (0) and passes.
4. The node attempts to execute the sub-transaction scripts up to their declared budgets, consuming resources far beyond `maxBlockExUnits` with no ledger-level rejection. [7](#0-6) [8](#0-7)

### Citations

**File:** eras/alonzo/impl/src/Cardano/Ledger/Alonzo/Tx.hs (L391-394)
```haskell
  (EraTx era, AlonzoEraTxWits era) =>
  Tx l era ->
  ExUnits
totExUnits tx = foldMap snd $ tx ^. witsTxL . rdmrsTxWitsL . unRedeemersL
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubUtxo.hs (L80-80)
```haskell
  | SubMaxTxSizeUTxO (Mismatch RelLTEQ Word32)
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubUtxo.hs (L215-278)
```haskell
dijkstraSubUtxoTransition ::
  forall era.
  ( EraTx era
  , EraStake era
  , EraCertState era
  , DijkstraEraTxBody era
  , AlonzoEraTxWits era
  , STS (EraRule "SUBUTXO" era)
  , EraRule "SUBUTXO" era ~ SUBUTXO era
  , InjectRuleFailure "SUBUTXO" Shelley.ShelleyUtxoPredFailure era
  , InjectRuleFailure "SUBUTXO" Allegra.AllegraUtxoPredFailure era
  , InjectRuleFailure "SUBUTXO" Alonzo.AlonzoUtxoPredFailure era
  , InjectRuleFailure "SUBUTXO" Babbage.BabbageUtxoPredFailure era
  , InjectRuleFailure "SUBUTXO" DijkstraUtxoPredFailure era
  ) =>
  TransitionRule (EraRule "SUBUTXO" era)
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

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Utxo.hs (L414-415)
```haskell
  {- totExunits tx ≤ maxTxExUnits pp -}
  runTest $ Alonzo.validateExUnitsTooBigUTxO pp tx
```

**File:** eras/alonzo/impl/src/Cardano/Ledger/Alonzo/Rules/Bbody.hs (L147-167)
```haskell
-- | Validate that total execution units (all transactions) do not exceed block limit.
-- ∑(tx ∈ txs)(totExunits tx) ≤ maxBlockExUnits pp
validateExUnits ::
  forall era.
  ( AlonzoEraTx era
  , InjectRuleFailure "BBODY" AlonzoBbodyPredFailure era
  ) =>
  StrictSeq.StrictSeq (Tx TopTx era) ->
  -- | Max block exunits protocol parameter.
  ExUnits ->
  Rule (EraRule "BBODY" era) 'Transition ()
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

**File:** libs/cardano-ledger-core/src/Cardano/Ledger/Plutus/ExUnits.hs (L79-91)
```haskell
data ExUnits' a = ExUnits'
  { exUnitsMem' :: !a
  , exUnitsSteps' :: !a
  }
  deriving (Eq, Generic, Show, Functor)
  -- It is deliberate that there is no Ord instance, use `pointWiseExUnits` instead.
  deriving
    (Measure, BoundedMeasure)
    via (InstantiatedAt Generic (ExUnits' a))
  deriving
    (Monoid, Semigroup)
    via (InstantiatedAt Measure (ExUnits' a))

```
