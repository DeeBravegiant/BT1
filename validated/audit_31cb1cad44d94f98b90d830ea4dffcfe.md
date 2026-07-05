### Title
DirectDeposits Not Included in `produced` Balance Check Allows ADA Creation from Nothing — (`File: eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs`)

---

### Summary

The Dijkstra era introduces a `DirectDeposits` field in the transaction body that allows a transaction to directly credit ADA into registered account addresses. However, `DirectDeposits` are not included in either the `consumed` or `produced` side of the ledger's value-preservation check (`consumed == produced`). As a result, any ADA credited via `DirectDeposits` is created from nothing — it is added to account balances without being deducted from any other pot, violating the fundamental ADA preservation invariant.

---

### Finding Description

The Dijkstra era's `DijkstraTxBodyRaw` includes a `dtbrDirectDeposits :: !DirectDeposits` field for top-level transactions and `dstbrDirectDeposits :: !DirectDeposits` for sub-transactions. [1](#0-0) [2](#0-1) 

These direct deposits are applied to account balances in the `ENTITIES` and `SUBENTITIES` rules via `applyDirectDeposits`: [3](#0-2) 

The application happens unconditionally (after only a registration check) in the ENTITIES transition: [4](#0-3) 

**The critical omission**: `dijkstraProducedValue` — the function that computes the "produced" side of the balance check for the Dijkstra era — delegates to `conwayProducedValue` for the top-level transaction and `dijkstraSubTxProducedValue` for sub-transactions. Neither includes `DirectDeposits`: [5](#0-4) [6](#0-5) 

Similarly, `getConsumedDijkstraValue` delegates to `getConsumedMaryValue` → `getConsumedCoin`, which sums only UTxO inputs, withdrawals, and refunds — no `DirectDeposits`: [7](#0-6) 

The `EraUTxO DijkstraEra` instance wires these functions into the ledger rules: [8](#0-7) 

**Concrete exploit trace**:

| Pot | Before | After UTXO rule | After ENTITIES rule |
|---|---|---|---|
| UTxO | 100 ADA | 95 ADA (−5) | 95 ADA |
| Fee pot | 0 ADA | +5 ADA | 5 ADA |
| Account balance | 0 ADA | 0 ADA | +5 ADA |
| **Total** | **100 ADA** | **100 ADA** | **105 ADA** |

Transaction: inputs=100, outputs=95, fee=5, DirectDeposits=5.
- `consumed` = 100, `produced` = 95+5 = 100 → balance check **passes**.
- `applyDirectDeposits` then adds 5 ADA to the account balance.
- **Net: 5 ADA created from nothing.**

The UTXO rule only validates network IDs for `DirectDeposits` — no balance accounting: [9](#0-8) 

The same omission exists in the `SubUtxo` rule: [10](#0-9) 

---

### Impact Explanation

**Critical — Direct creation of ADA through an invalid ledger state transition.**

An unprivileged transaction sender can include a `DirectDeposits` map in any Dijkstra-era transaction. For each lovelace declared in `DirectDeposits`, that amount is added to the target account balance without being deducted from any other pot. The attacker can target their own registered account address, repeatedly submitting transactions to inflate their account balance arbitrarily. This directly violates the ADA preservation invariant that is the foundational security property of the Cardano ledger.

---

### Likelihood Explanation

Any user who can submit a valid Dijkstra-era transaction can exploit this. The only prerequisite is that the target `AccountAddress` is registered (which the attacker can register themselves). No privileged access, governance majority, or key compromise is required. The exploit is deterministic and repeatable.

---

### Recommendation

Include the sum of `DirectDeposits` in the `produced` value calculation. In `dijkstraProducedValue` (and `dijkstraSubTxProducedValue`), add the total coin from `directDepositsTxBodyL` to the injected coin alongside `feeTxBodyL`, `getTotalDepositsTxBody`, and `treasuryDonationTxBodyL`:

```haskell
-- In dijkstraProducedValue / dijkstraSubTxProducedValue:
<> inject (... <> fold (unDirectDeposits (txBody ^. directDepositsTxBodyL)))
```

This mirrors how `treasuryDonationTxBodyL` is handled — it is a coin that leaves the UTxO and must appear on the `produced` side to satisfy `consumed == produced`. [6](#0-5) 

---

### Proof of Concept

1. Register a stake credential and obtain an `AccountAddress` (e.g., `AccountAddress Mainnet (AccountId $ KeyHashObj myKeyHash)`).
2. Construct a Dijkstra-era transaction:
   - `inputs`: one UTxO entry worth N lovelace
   - `outputs`: one UTxO entry worth (N − fee) lovelace
   - `fee`: standard minimum fee
   - `directDeposits`: `DirectDeposits $ Map.singleton myAccountAddress (Coin X)` where X is any positive amount ≤ (N − fee − fee_for_outputs)
3. Adjust outputs so that `sum(outputs) + fee == sum(inputs)` (balance check passes without counting DirectDeposits).
4. Submit the transaction. The UTXO rule accepts it (balance check passes). The ENTITIES rule calls `applyDirectDeposits`, crediting X lovelace to `myAccountAddress`.
5. Observe that `myAccountAddress` now has X lovelace that was not deducted from any other pot — total ADA in the system has increased by X.

The example in the test suite confirms `DirectDeposits` of `Coin 1_000_000` are a valid transaction field, with no corresponding deduction from the balance check: [11](#0-10)

### Citations

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxBody.hs (L184-186)
```haskell
    , dtbrSubTransactions :: !(OMap TxId (Tx SubTx era))
    , dtbrDirectDeposits :: !DirectDeposits
    , dtbrAccountBalanceIntervals :: !(AccountBalanceIntervals era)
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxBody.hs (L205-207)
```haskell
    , dstbrRequiredTopLevelGuards :: !(Map (Credential Guard) (StrictMaybe (Data era)))
    , dstbrDirectDeposits :: !DirectDeposits
    , dstbrAccountBalanceIntervals :: !(AccountBalanceIntervals era)
```

**File:** libs/cardano-ledger-core/src/Cardano/Ledger/State/Account.hs (L290-298)
```haskell
applyDirectDeposits ::
  EraAccounts era =>
  DirectDeposits ->
  Accounts era ->
  Accounts era
applyDirectDeposits (DirectDeposits dd) =
  updateAccountBalances
    (\amount account -> addCompactCoin amount (account ^. balanceAccountStateL))
    dd
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Entities.hs (L211-216)
```haskell
  let directDeposits = tx ^. bodyTxL . directDepositsTxBodyL
      accountsAfterCerts = certStateAfterCerts ^. certDStateL . accountsL
  failOnJust (directDepositsMissingAccounts directDeposits accountsAfterCerts) $
    injectFailure . DirectDepositsToMissingAccounts

  pure $ certStateAfterCerts & certDStateL . accountsL %~ applyDirectDeposits directDeposits
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs (L65-91)
```haskell
getConsumedDijkstraValue ::
  forall era l.
  ( DijkstraEraTxBody era
  , EraUTxO era
  , Value era ~ MaryValue
  , STxLevel l era ~ STxBothLevels l era
  ) =>
  PParams era ->
  (Credential Staking -> Maybe Coin) ->
  (Credential DRepRole -> Maybe Coin) ->
  UTxO era ->
  TxBody l era ->
  Value era
getConsumedDijkstraValue pp lookupStakingDeposit lookupDRepDeposit utxo txBody =
  withBothTxLevels
    txBody
    ( \topTxBody ->
        txBodyConsumedValue topTxBody <> subTransactionsConsumedValue topTxBody
    )
    txBodyConsumedValue
  where
    txBodyConsumedValue :: forall m. TxBody m era -> Value era
    txBodyConsumedValue = getConsumedMaryValue pp lookupStakingDeposit lookupDRepDeposit utxo
    subTransactionsConsumedValue topTxBody =
      foldMap'
        (getConsumedValue pp lookupStakingDeposit lookupDRepDeposit utxo . view bodyTxL)
        (topTxBody ^. subTransactionsTxBodyL)
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs (L93-106)
```haskell
dijkstraProducedValue ::
  ( DijkstraEraTxBody era
  , EraUTxO era
  , Value era ~ MaryValue
  ) =>
  PParams era ->
  (KeyHash StakePool -> Bool) ->
  TxBody TopTx era ->
  MaryValue
dijkstraProducedValue pp isRegPoolId txBody =
  conwayProducedValue pp isRegPoolId txBody
    <> foldMap'
      (getProducedValue pp isRegPoolId . view bodyTxL)
      (txBody ^. subTransactionsTxBodyL)
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs (L124-131)
```haskell
instance EraUTxO DijkstraEra where
  type ScriptsNeeded DijkstraEra = AlonzoScriptsNeeded DijkstraEra

  consumed = conwayConsumed

  getConsumedValue = getConsumedDijkstraValue

  getProducedValue = getProducedDijkstraValue
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs (L252-261)
```haskell
dijkstraSubTxProducedValue ::
  (ConwayEraTxBody era, Value era ~ MaryValue) =>
  PParams era ->
  (KeyHash StakePool -> Bool) ->
  TxBody SubTx era ->
  Value era
dijkstraSubTxProducedValue pp isRegPoolId txBody =
  sumAllValue (txBody ^. outputsTxBodyL)
    <> inject (getTotalDepositsTxBody pp isRegPoolId txBody <> txBody ^. treasuryDonationTxBodyL)
    <> burnedMultiAssets txBody
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Utxo.hs (L304-316)
```haskell
validateWrongNetworkInDirectDeposit ::
  DijkstraEraTxBody era =>
  Network ->
  TxBody t era ->
  Test (DijkstraUtxoPredFailure era)
validateWrongNetworkInDirectDeposit netId txb =
  failureOnNonEmptySet depositsWrongNetwork (WrongNetworkInDirectDeposit netId)
  where
    depositsWrongNetwork =
      Map.keysSet $
        Map.filterWithKey
          (\a _ -> aaNetworkId a /= netId)
          (unDirectDeposits $ txb ^. directDepositsTxBodyL)
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubUtxo.hs (L241-278)
```haskell
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

**File:** eras/dijkstra/impl/testlib/Test/Cardano/Ledger/Dijkstra/Examples.hs (L258-263)
```haskell
exampleDirectDeposits :: DirectDeposits
exampleDirectDeposits =
  DirectDeposits $
    Map.singleton
      (AccountAddress Mainnet (AccountId $ KeyHashObj $ mkKeyHash 300))
      (Coin 1_000_000)
```
