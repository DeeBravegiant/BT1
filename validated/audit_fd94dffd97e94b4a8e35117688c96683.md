### Title
Sub-Transaction UTXO Rule Missing Value Conservation Check Allows Unconstrained Native Asset Creation/Destruction - (File: `eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubUtxo.hs`)

---

### Summary

The Dijkstra era's `SUBUTXO` transition rule (`dijkstraSubUtxoTransition`) applies UTxO state updates for sub-transactions without performing any value conservation check. An unprivileged transaction sender can craft a sub-transaction whose outputs contain more native assets than its inputs plus mint field, creating native assets from nothing — entirely bypassing minting policy validation. The inverse (destroying native assets without burning) is equally possible.

---

### Finding Description

In every era from Mary onward, the `UTXO` rule enforces value conservation via `validateValueNotConservedUTxO`, which requires:

```
consumed(pp, utxo, txBody) = produced(pp, certState, txBody)
```

where `consumed` includes the positive mint field and `produced` includes the negative mint field (burned assets). This is the mechanism that ties native asset creation/destruction to minting policy scripts.

The Dijkstra era introduces nested ("sub") transactions. Sub-transactions are processed through `SUBLEDGERS → SUBLEDGER → SUBUTXOW → SUBUTXO`. The `SUBUTXO` transition rule (`dijkstraSubUtxoTransition`) performs the following checks:

- Validity interval
- Forecast range
- Output size limits
- Non-empty input set
- Bad inputs (inputs exist in UTxO)
- Output min-ADA
- Network ID checks

It then unconditionally calls `Shelley.updateUTxOStateNoFees`, which simply removes the sub-tx's inputs from the UTxO and inserts its outputs:

```haskell
updateUTxOStateNoFees pp utxos txBody certState govState depositChangeEvent txUtxODiffEvent = do
  let ...
      !utxoAdd = txouts txBody
      !(utxoWithout, utxoDel) = extractKeys utxo (txBody ^. inputsTxBodyL)
      !newUTxO = utxoWithout `Map.union` unUTxO utxoAdd
```

**There is no call to `validateValueNotConservedUTxO` or any equivalent check in `dijkstraSubUtxoTransition`.** The sub-transaction's mint field is never verified against its inputs and outputs.

The top-level `dijkstraUtxoTransition` does call `validateValueNotConservedUTxO`, but only for the top-level transaction body using `originalUtxo` (the UTxO snapshot before any sub-transactions ran):

```haskell
{- consumed pp utxo₀ txb = produced pp certState txb -}
runTest $ Shelley.validateValueNotConservedUTxO pp originalUtxo certState txBody
```

This check covers only the top-level `txBody`. Sub-transactions' mint fields and UTxO deltas are invisible to it.

The top-level UTXOW does aggregate script-hash presence checks across sub-transactions:

```haskell
let allScriptHashesNeeded =
      Set.unions $
        topScriptHashesNeeded
          : (getScriptsHashesNeeded . scriptsNeededStAnnTx <$> subStAnnTxs)
...
runTest $ Babbage.babbageMissingScripts pp allScriptHashesNeeded refScripts witnessScripts
```

However, this only verifies that scripts referenced in the sub-tx's `mint` field are provided. If the sub-tx's `mint` field is **empty** while its outputs contain native assets not present in its inputs, no minting script hash is referenced, so no script is required, and the check passes silently.

---

### Impact Explanation

**Critical. Direct creation of native assets through an invalid ledger state transition.**

An attacker can create arbitrary quantities of any native asset (any `PolicyID` + `AssetName`) without satisfying the corresponding minting policy. The fabricated assets are written into the UTxO by `updateUTxOStateNoFees` and persist after the batch is processed. Any subsequent transaction can spend the output containing the fabricated assets, putting them into circulation. This is equivalent to minting without a minting policy — the fundamental invariant of Cardano's native asset system is broken.

The inverse attack (destroying native assets without burning) is equally possible: a sub-transaction can consume inputs containing native assets and produce outputs containing only ADA, permanently removing the native assets from circulation without satisfying the minting policy's burn conditions.

---

### Likelihood Explanation

**High.** Any unprivileged transaction sender who owns at least one UTxO entry can exploit this. No special role, key, or governance threshold is required. The attacker only needs to:
1. Construct a sub-transaction with unbalanced native asset values.
2. Embed it in a valid top-level Dijkstra transaction.

The attack is deterministic and requires no probabilistic advantage.

---

### Recommendation

Add a value conservation check to `dijkstraSubUtxoTransition` analogous to the one in the top-level `dijkstraUtxoTransition`. For sub-transactions, the check should use the sub-transaction's own inputs (resolved against the current UTxO state at the time the sub-tx is processed) and the sub-tx's mint field:

```haskell
-- In dijkstraSubUtxoTransition, before updateUTxOStateNoFees:
runTest $ Shelley.validateValueNotConservedUTxO pp (utxosUtxo utxoState) certState txBody
```

This mirrors the standard Mary/Alonzo/Babbage/Conway UTXO rule behavior and ensures that native asset creation/destruction in sub-transactions is always tied to a validated minting policy.

---

### Proof of Concept

**Attack: Create 1,000,000 units of `TokenX` from nothing**

1. Attacker owns UTxO entry `U = (txIn, TxOut { addr = attackerAddr, value = 5 ADA })`.

2. Attacker constructs sub-transaction `subTx`:
   - `inputsTxBodyL = { txIn }` (spends `U`)
   - `outputsTxBodyL = [ TxOut { addr = attackerAddr, value = 5 ADA + 1,000,000 TokenX } ]`
   - `mintTxBodyL = mempty` (empty — no minting policy script required)

3. Attacker constructs top-level transaction `topTx`:
   - Spends any other UTxO entry the attacker owns (to satisfy `validateInputSetEmptyUTxO`)
   - `subTransactionsTxBodyL = OMap.singleton subTx`

4. `topTx` is submitted. Processing order:
   - `SUBLEDGERS` processes `subTx` via `SUBUTXOW → SUBUTXO`.
   - `dijkstraSubUtxoTransition` runs all checks — none involve value conservation.
   - `updateUTxOStateNoFees` removes `txIn` from UTxO and inserts `TxOut { 5 ADA + 1,000,000 TokenX }`.
   - Top-level `dijkstraUtxoTransition` runs `validateValueNotConservedUTxO` on `topTx`'s own body against `originalUtxo` — this check passes because `topTx`'s own inputs/outputs are balanced.

5. After the batch, the UTxO contains `TxOut { 5 ADA + 1,000,000 TokenX }`. The 1,000,000 `TokenX` were created without any minting policy being evaluated.

6. A subsequent transaction spends this output, putting the fabricated `TokenX` into circulation.

**Relevant code locations:**

- Missing check: [1](#0-0) 

- `updateUTxOStateNoFees` (no value conservation): [2](#0-1) 

- Top-level check (covers only top-level txBody): [3](#0-2) 

- Top-level aggregated script check (does not substitute for value conservation): [4](#0-3) 

- Sub-tx body has a `mint` field that is never validated for conservation: [5](#0-4) 

- Mary value conservation (the check that is absent in SUBUTXO): [6](#0-5)

### Citations

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

**File:** eras/shelley/impl/src/Cardano/Ledger/Shelley/Rules/Utxo.hs (L604-641)
```haskell
updateUTxOStateNoFees ::
  ( EraTxBody era
  , EraStake era
  , EraCertState era
  , Monad m
  ) =>
  PParams era ->
  UTxOState era ->
  TxBody l era ->
  CertState era ->
  GovState era ->
  (Coin -> m ()) ->
  (UTxO era -> UTxO era -> m ()) ->
  m (UTxOState era)
updateUTxOStateNoFees pp utxos txBody certState govState depositChangeEvent txUtxODiffEvent = do
  let UTxOState {utxosUtxo, utxosDeposited, utxosFees, utxosDonation} = utxos
      UTxO utxo = utxosUtxo
      !utxoAdd = txouts txBody -- These will be inserted into the UTxO
      {- utxoDel  = txins txb ◁ utxo -}
      !(utxoWithout, utxoDel) = extractKeys utxo (txBody ^. inputsTxBodyL)
      {- newUTxO = (txins txb ⋪ utxo) ∪ outs txb -}
      newUTxO = utxoWithout `Map.union` unUTxO utxoAdd
      deletedUTxO = UTxO utxoDel
      totalRefunds = certsTotalRefundsTxBody pp certState txBody
      totalDeposits = certsTotalDepositsTxBody pp certState txBody
      depositChange = totalDeposits <-> totalRefunds
  depositChangeEvent depositChange
  txUtxODiffEvent deletedUTxO utxoAdd
  pure $!
    UTxOState
      { utxosUtxo = UTxO newUTxO
      , utxosDeposited = utxosDeposited <> depositChange
      , utxosFees = utxosFees
      , utxosGovState = govState
      , utxosInstantStake =
          deleteInstantStake deletedUTxO (addInstantStake utxoAdd (utxos ^. instantStakeL))
      , utxosDonation = utxosDonation
      }
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Utxo.hs (L380-381)
```haskell
  {- consumed pp utxo₀ txb = produced pp certState txb -}
  runTest $ Shelley.validateValueNotConservedUTxO pp originalUtxo certState txBody
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Utxow.hs (L237-261)
```haskell
  let allScriptHashesNeeded =
        Set.unions $
          topScriptHashesNeeded
            : (getScriptsHashesNeeded . scriptsNeededStAnnTx <$> subStAnnTxs)

  {- ∀s ∈ (txscripts txw utxo neededHashes ) ∩ Scriptph1 , validateScript s tx -}
  -- Per-level: phase-1 script validation is per-tx (script execution)
  runTest $ Babbage.validateFailedBabbageScripts tx scriptsProvided topScriptHashesNeeded

  {- neededHashes − dom(refScripts tx utxo) = dom(txwitscripts txw) -}
  -- Aggregated: missing/extraneous scripts across all levels.
  let witnessScripts =
        Map.keysSet (tx ^. witsTxL . scriptTxWitsL)
          <> foldMap (Map.keysSet . (^. witsTxL . scriptTxWitsL)) subTxs
      allRefScriptInputs =
        txBody ^. referenceInputsTxBodyL
          <> txBody ^. inputsTxBodyL
          <> foldMap
            ( \subTx ->
                subTx ^. bodyTxL . referenceInputsTxBodyL
                  <> subTx ^. bodyTxL . inputsTxBodyL
            )
            subTxs
      refScripts = Map.keysSet $ getReferenceScripts originalUtxo allRefScriptInputs
  runTest $ Babbage.babbageMissingScripts pp allScriptHashesNeeded refScripts witnessScripts
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxBody.hs (L734-735)
```haskell
  , dstbMint
  , dstbScriptIntegrityHash
```

**File:** eras/mary/impl/src/Cardano/Ledger/Mary/UTxO.hs (L69-102)
```haskell
getConsumedMaryValue ::
  (MaryEraTxBody era, Value era ~ MaryValue) =>
  PParams era ->
  (Credential Staking -> Maybe Coin) ->
  (Credential DRepRole -> Maybe Coin) ->
  UTxO era ->
  TxBody l era ->
  MaryValue
getConsumedMaryValue pp lookupStakingDeposit lookupDRepDeposit utxo txBody =
  consumedValue <> MaryValue mempty mintedMultiAsset
  where
    mintedMultiAsset = filterMultiAsset (\_ _ -> (> 0)) $ txBody ^. mintTxBodyL
    {- balance (txins tx ◁ u) + wbalance (txwdrls tx) + keyRefunds pp tx -}
    consumedValue =
      sumUTxO (txInsFilter utxo (txBody ^. inputsTxBodyL))
        <> inject (refunds <> withdrawals)
    refunds = getTotalRefundsTxBody pp lookupStakingDeposit lookupDRepDeposit txBody
    withdrawals = fold . unWithdrawals $ txBody ^. withdrawalsTxBodyL

getProducedMaryValue ::
  (MaryEraTxBody era, Value era ~ MaryValue) =>
  PParams era ->
  -- | Check whether a pool with a supplied PoolStakeId is already registered.
  (KeyHash StakePool -> Bool) ->
  TxBody TopTx era ->
  MaryValue
getProducedMaryValue pp isPoolRegistered txBody =
  shelleyProducedValue pp isPoolRegistered txBody <> burnedMultiAssets txBody

burnedMultiAssets :: MaryEraTxBody era => TxBody l era -> MaryValue
burnedMultiAssets txBody =
  MaryValue mempty $
    mapMaybeMultiAsset (\_ _ v -> if v < 0 then Just (negate v) else Nothing) $
      txBody ^. mintTxBodyL
```
