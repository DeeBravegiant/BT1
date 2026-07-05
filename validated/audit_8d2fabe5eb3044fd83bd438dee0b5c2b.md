### Title
Missing Preservation-of-Value Check in Dijkstra Sub-Transaction UTXO Rule Allows Unauthorized Native Token Creation - (File: eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubUtxo.hs)

---

### Summary

The Dijkstra era's `SUBUTXO` rule (`dijkstraSubUtxoTransition`) processes nested sub-transactions without performing a preservation-of-value (`consumed = produced`) check. Sub-transactions carry a `mint` field (`dstbrMint :: !MultiAsset`) and arbitrary outputs. Because no value-conservation validation is applied at the sub-transaction level, an attacker can craft a sub-transaction whose outputs contain more native tokens than its inputs plus its declared mint field, creating native assets out of thin air through an otherwise-valid ledger state transition.

---

### Finding Description

The Dijkstra era introduces nested sub-transactions. Each sub-transaction is validated by the `SUBUTXO` STS rule, whose transition function is `dijkstraSubUtxoTransition` in `SubUtxo.hs`.

**What `dijkstraSubUtxoTransition` checks:**

- Validity interval (`validateOutsideValidityIntervalUTxO`)
- Forecast window (`validateOutsideForecast`)
- Output size (`validateOutputTooBigUTxO`)
- Non-empty input set (`validateInputSetEmptyUTxO`)
- Input existence in original UTxO and current UTxO state (`validateBadInputsUTxO`)
- Bootstrap address attribute size (`validateOutputBootAddrAttrsTooBig`)
- Minimum ADA per output (`validateOutputTooSmallUTxO`)
- Network IDs in outputs, withdrawals, direct deposits, and tx body [1](#0-0) 

**What is conspicuously absent:** `validateValueNotConservedUTxO` — the check that `consumed pp utxo txb = produced pp certState txb`. This check is the ledger's sole enforcement that native token quantities in outputs are balanced by inputs plus the declared `mint` field.

The `DijkstraSubUtxoPredFailure` type carries no `ValueNotConservedUTxO` constructor: [2](#0-1) 

The injection mapping `dijkstraUtxoToDijkstraSubUtxoPredFailure` explicitly panics on this case, confirming the omission is structural: [3](#0-2) 

Sub-transactions carry a `mint` field (`dstbrMint :: !MultiAsset`) in `DijkstraSubTxBodyRaw`: [4](#0-3) 

The top-level `UTXO` rule does call `validateValueNotConservedUTxO`, but only against the top-level `txBody` and `originalUtxo`: [5](#0-4) 

The `getConsumedMaryValue` function (used by `consumed`) only sums `txBody ^. inputsTxBodyL` and `txBody ^. mintTxBodyL` — the top-level body fields — and does not traverse sub-transactions: [6](#0-5) 

After passing all `SUBUTXO` checks, the sub-transaction is applied via `Shelley.updateUTxOStateNoFees`, which removes the declared inputs from the UTxO and inserts the declared outputs — including any native tokens in those outputs — without any balance validation: [7](#0-6) 

The preservation-of-value property for the Mary/multi-asset era requires that `consumed = produced` where `consumed` includes the `mint` field: [8](#0-7) 

Without this check at the sub-transaction level, the invariant is broken.

---

### Impact Explanation

**Critical — Direct creation of native assets through an invalid ledger state transition.**

A sub-transaction can declare outputs containing an arbitrary quantity of any native asset without those tokens being present in its inputs or in a validated `mint` field. `updateUTxOStateNoFees` unconditionally inserts those outputs into the UTxO. The resulting UTxO contains native tokens that were never minted under any policy, violating the global preservation-of-value invariant and constituting unauthorized creation of native assets.

---

### Likelihood Explanation

Any unprivileged user who can submit a Dijkstra-era transaction can embed sub-transactions. No special key, governance role, or script authorization is required to include a sub-transaction with imbalanced native-token outputs. The attacker only needs to:

1. Hold a UTxO entry with sufficient ADA to satisfy `minUTxO` for the outputs.
2. Craft a sub-transaction whose outputs contain more native tokens than its inputs.
3. Submit the enclosing top-level transaction (which itself passes all top-level checks).

The `isValid` flag gating sub-transaction application (`sueTopTxIsValid`) reflects the top-level transaction's script validity, not the sub-transaction's value balance, so a top-level transaction with no scripts (or passing scripts) will have `isValid = True` and all sub-transactions will be applied.

---

### Recommendation

Add a preservation-of-value check to `dijkstraSubUtxoTransition` analogous to the one in the top-level `dijkstraUtxoTransition`. Specifically, call `Shelley.validateValueNotConservedUTxO` (or a Dijkstra-specific equivalent) against the sub-transaction body and the UTxO state visible to that sub-transaction before calling `updateUTxOStateNoFees`. The `DijkstraSubUtxoPredFailure` type must be extended with a `SubValueNotConservedUTxO` constructor to carry the mismatch.

---

### Proof of Concept

1. Attacker holds UTxO entry `u₀` containing 10 ADA and 0 units of native asset `(P, A)`.
2. Attacker constructs a Dijkstra top-level transaction `txTop` with no scripts and a sub-transaction `txSub`:
   - `txSub` inputs: `{u₀}` (10 ADA, 0 tokens)
   - `txSub` outputs: `[out₁: 5 ADA + 0 tokens, out₂: 5 ADA + 1 000 000 tokens of (P, A)]`
   - `txSub` mint field: `∅` (empty — no minting policy invoked)
3. `txTop` is submitted. The top-level `validateValueNotConservedUTxO` checks only `txTop`'s body (no inputs, no outputs referencing `txSub`'s entries) and passes.
4. `dijkstraSubUtxoTransition` processes `txSub`:
   - `validateBadInputsUTxO` passes (`u₀` exists).
   - `validateOutputTooSmallUTxO` passes (both outputs carry ≥ minUTxO ADA).
   - No `validateValueNotConservedUTxO` is called.
5. `updateUTxOStateNoFees` removes `u₀` and inserts `out₁` and `out₂` into the UTxO.
6. The attacker now controls `out₂` containing 1 000 000 units of `(P, A)` that were never minted under policy `P`, violating the preservation-of-value invariant and constituting unauthorized native asset creation. [1](#0-0) [9](#0-8) [5](#0-4)

### Citations

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubUtxo.hs (L72-110)
```haskell
data DijkstraSubUtxoPredFailure era
  = -- | The bad transaction inputs
    SubBadInputsUTxO (NonEmptySet TxIn)
  | SubOutsideValidityIntervalUTxO
      -- | transaction's validity interval
      ValidityInterval
      -- | current slot
      SlotNo
  | SubMaxTxSizeUTxO (Mismatch RelLTEQ Word32)
  | SubInputSetEmptyUTxO
  | -- | the set of addresses with incorrect network IDs
    SubWrongNetwork
      -- | the expected network id
      Network
      -- | the set of addresses with incorrect network IDs
      (NonEmptySet Addr)
  | SubWrongNetworkWithdrawal
      -- | the expected network id
      Network
      -- | the set of reward addresses with incorrect network IDs
      (NonEmptySet AccountAddress)
  | -- | list of supplied bad transaction outputs
    SubOutputBootAddrAttrsTooBig (NonEmpty (TxOut era))
  | -- | list of supplied bad transaction output triples (actualSize,PParameterMaxValue,TxOut)
    SubOutputTooBigUTxO (NonEmpty (Int, Int, TxOut era))
  | -- | Wrong Network ID in body
    SubWrongNetworkInTxBody
      (Mismatch RelEQ Network)
  | -- | slot number outside consensus forecast range
    SubOutsideForecast SlotNo
  | -- | list of supplied transaction outputs that are too small,
    -- together with the minimum value for the given output.
    SubBabbageOutputTooSmallUTxO (NonEmpty (TxOut era, Coin))
  | SubWrongNetworkInDirectDeposit
      -- | the expected network id
      Network
      -- | the set of account addresses with incorrect network IDs
      (NonEmptySet AccountAddress)
  deriving (Generic)
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

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubUtxo.hs (L333-333)
```haskell
  ValueNotConservedUTxO _ -> error "Impossible: `ValueNotConservedUTxO` for SUBUTXO"
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxBody.hs (L189-208)
```haskell
  DijkstraSubTxBodyRaw ::
    { dstbrSpendInputs :: !(Set TxIn)
    , dstbrReferenceInputs :: !(Set TxIn)
    , dstbrOutputs :: !(StrictSeq (Sized (TxOut era)))
    , dstbrCerts :: !(OSet.OSet (TxCert era))
    , dstbrWithdrawals :: !Withdrawals
    , dstbrVldt :: !ValidityInterval
    , dstbrGuards :: !(OSet (Credential Guard))
    , dstbrMint :: !MultiAsset
    , dstbrScriptIntegrityHash :: !(StrictMaybe ScriptIntegrityHash)
    , dstbrAuxDataHash :: !(StrictMaybe TxAuxDataHash)
    , dstbrNetworkId :: !(StrictMaybe Network)
    , dstbrVotingProcedures :: !(VotingProcedures era)
    , dstbrProposalProcedures :: !(OSet.OSet (ProposalProcedure era))
    , dstbrCurrentTreasuryValue :: !(StrictMaybe Coin)
    , dstbrTreasuryDonation :: !Coin
    , dstbrRequiredTopLevelGuards :: !(Map (Credential Guard) (StrictMaybe (Data era)))
    , dstbrDirectDeposits :: !DirectDeposits
    , dstbrAccountBalanceIntervals :: !(AccountBalanceIntervals era)
    } ->
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Utxo.hs (L380-381)
```haskell
  {- consumed pp utxo₀ txb = produced pp certState txb -}
  runTest $ Shelley.validateValueNotConservedUTxO pp originalUtxo certState txBody
```

**File:** eras/mary/impl/src/Cardano/Ledger/Mary/UTxO.hs (L77-86)
```haskell
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
```

**File:** eras/shelley-ma/formal-spec/utxo.tex (L49-57)
```tex
    & \fun{consumed} \in \PParams \to \UTxO \to \TxBody \to \hldiff{\ValMonoid} \\
    & \consumed{pp}{utxo}{txb} = \\
    & ~~\ubalance{(\txins{txb} \restrictdom \var{utxo})} ~+~ \hldiff{\fun{mint}~\var{txb}} \\
    &~~+~\hldiff{\fun{inject}}(\fun{wbalance}~(\fun{txwdrls}~{txb})~+~ \keyRefunds{pp}{txb})
    \nextdef
    & \fun{produced} \in \PParams \to \StakePools \to \TxBody \to \hldiff{\ValMonoid} \\
    & \fun{produced}~\var{pp}~\var{stpools}~\var{txb} = \\
    &~~\ubalance{(\fun{outs}~{txb})} \\
    &~~+ \hldiff{\fun{inject}}(\txfee{txb} + \totalDeposits{pp}{stpools}{(\txcerts{txb})})
```
