Looking at the Dijkstra era's `DirectDeposits` feature and how it interacts with the preservation-of-value check.

Key files examined:
- `eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs` — `getConsumedDijkstraValue`, `dijkstraProducedValue`, `dijkstraSubTxProducedValue`
- `eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Utxo.hs` — `dijkstraUtxoTransition` (line 381: `validateValueNotConservedUTxO`)
- `eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Entities.hs` — `dijkstraEntitiesTransition` (line 216: `applyDirectDeposits`)
- `libs/cardano-ledger-core/src/Cardano/Ledger/State/Account.hs` — `applyDirectDeposits`

---

### Title
DirectDeposits Not Included in Produced Value Calculation Enables Unbounded ADA Creation - (File: eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs)

### Summary

The Dijkstra era introduces `DirectDeposits` — a transaction body field that deposits ADA directly into registered account balances. The `ENTITIES` rule applies these deposits unconditionally via `applyDirectDeposits`. However, neither `dijkstraProducedValue` nor `dijkstraSubTxProducedValue` includes the sum of `DirectDeposits` in the produced-value calculation. Because the preservation-of-value check (`consumed = produced`) is blind to direct deposits, an attacker can include arbitrarily large `DirectDeposits` in a balanced transaction and have that ADA credited to their account without funding it from UTxO inputs — creating ADA from nothing.

### Finding Description

**Root cause — `dijkstraProducedValue` omits `DirectDeposits`:**

`dijkstraProducedValue` aggregates the top-level Conway produced value plus sub-transaction produced values:

```haskell
dijkstraProducedValue pp isRegPoolId txBody =
  conwayProducedValue pp isRegPoolId txBody
    <> foldMap'
      (getProducedValue pp isRegPoolId . view bodyTxL)
      (txBody ^. subTransactionsTxBodyL)
```

`conwayProducedValue` is a pre-Dijkstra function; it has no knowledge of `DirectDeposits`. The Dijkstra extension does not add `inject (fold (unDirectDeposits (txBody ^. directDepositsTxBodyL)))` anywhere in the produced-value chain. [1](#0-0) 

`dijkstraSubTxProducedValue` (used for sub-transactions) similarly omits `DirectDeposits`:

```haskell
dijkstraSubTxProducedValue pp isRegPoolId txBody =
  sumAllValue (txBody ^. outputsTxBodyL)
    <> inject (getTotalDepositsTxBody pp isRegPoolId txBody <> txBody ^. treasuryDonationTxBodyL)
    <> burnedMultiAssets txBody
``` [2](#0-1) 

`getConsumedDijkstraValue` also does not include `DirectDeposits` — it delegates to `getConsumedMaryValue` for every tx body level: [3](#0-2) 

**The preservation-of-value check in `dijkstraUtxoTransition` is therefore blind to `DirectDeposits`:**

```haskell
{- consumed pp utxo₀ txb = produced pp certState txb -}
runTest $ Shelley.validateValueNotConservedUTxO pp originalUtxo certState txBody
``` [4](#0-3) 

**`DirectDeposits` are then unconditionally applied to account balances in the `ENTITIES` rule:**

```haskell
let directDeposits = tx ^. bodyTxL . directDepositsTxBodyL
    accountsAfterCerts = certStateAfterCerts ^. certDStateL . accountsL
failOnJust (directDepositsMissingAccounts directDeposits accountsAfterCerts) $
  injectFailure . DirectDepositsToMissingAccounts

pure $ certStateAfterCerts & certDStateL . accountsL %~ applyDirectDeposits directDeposits
``` [5](#0-4) 

The only guard is that target accounts must already be registered (`directDepositsMissingAccounts`). There is no check that the deposited amounts are funded.

`applyDirectDeposits` unconditionally adds the specified coin to each account balance:

```haskell
applyDirectDeposits (DirectDeposits dd) =
  updateAccountBalances
    (\amount account -> addCompactCoin amount (account ^. balanceAccountStateL))
    dd
``` [6](#0-5) 

The same omission exists in `dijkstraSubEntitiesTransition` for sub-transactions: [7](#0-6) 

**Contrast with `treasuryDonation`**, which is correctly included in both `conwayProducedValue` (top-level) and `dijkstraSubTxProducedValue` (sub-transactions), showing the intended pattern that `DirectDeposits` should follow: [8](#0-7) 

### Impact Explanation

**Critical. Direct creation of ADA through an invalid ledger state transition.**

Because `DirectDeposits` are applied to account balances without appearing in the `produced` side of the preservation-of-value equation, an attacker can credit an arbitrary amount of ADA to any registered account at zero cost. The credited balance is immediately withdrawable via the normal withdrawal mechanism, permanently inflating the ADA supply and destroying the monetary integrity of the ledger.

### Likelihood Explanation

Any unprivileged transaction sender can exploit this. The attack requires only:
1. A registered stake account (trivially obtained by paying the key deposit).
2. A valid Dijkstra-era transaction with a non-

### Citations

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

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Utxo.hs (L380-381)
```haskell
  {- consumed pp utxo₀ txb = produced pp certState txb -}
  runTest $ Shelley.validateValueNotConservedUTxO pp originalUtxo certState txBody
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Entities.hs (L211-216)
```haskell
  let directDeposits = tx ^. bodyTxL . directDepositsTxBodyL
      accountsAfterCerts = certStateAfterCerts ^. certDStateL . accountsL
  failOnJust (directDepositsMissingAccounts directDeposits accountsAfterCerts) $
    injectFailure . DirectDepositsToMissingAccounts

  pure $ certStateAfterCerts & certDStateL . accountsL %~ applyDirectDeposits directDeposits
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

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubEntities.hs (L182-187)
```haskell
  let directDeposits = tx ^. bodyTxL . directDepositsTxBodyL
      accountsAfterSubCerts = certStateAfterSubCerts ^. certDStateL . accountsL
  failOnJust (directDepositsMissingAccounts directDeposits accountsAfterSubCerts) $
    injectFailure . SubDirectDepositsToMissingAccounts

  pure $ certStateAfterSubCerts & certDStateL . accountsL %~ applyDirectDeposits directDeposits
```
