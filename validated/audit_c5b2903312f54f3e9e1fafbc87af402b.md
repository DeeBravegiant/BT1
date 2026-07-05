### Title
`directDeposits` field omitted from preservation-of-value check enables ADA creation from nothing — (File: `eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs`)

---

### Summary

The Dijkstra era introduces a `directDeposits` field in the transaction body that adds ADA directly to account balances via the `ENTITIES` rule. However, this field is absent from both the `consumed` and `produced` sides of the preservation-of-value check (`consumed pp certState utxo txBody = produced pp certState txBody`) enforced in the `UTXO` rule. A transaction author can therefore include arbitrary `directDeposits` amounts that inflate account balances without any corresponding deduction from the UTxO, creating ADA from nothing.

---

### Finding Description

**New feature.** `DijkstraEraTxBody` adds a `directDepositsTxBodyL :: Lens' (TxBody l era) DirectDeposits` field (a `Map AccountAddress Coin`). [1](#0-0) 

**Application in ENTITIES.** `dijkstraEntitiesTransition` applies these deposits to account balances after running the `CERTS` sub-rule:

```haskell
let directDeposits = tx ^. bodyTxL . directDepositsTxBodyL
    accountsAfterCerts = certStateAfterCerts ^. certDStateL . accountsL
failOnJust (directDepositsMissingAccounts directDeposits accountsAfterCerts) $
  injectFailure . DirectDepositsToMissingAccounts

pure $ certStateAfterCerts & certDStateL . accountsL %~ applyDirectDeposits directDeposits
``` [2](#0-1) 

The only validation performed is `directDepositsMissingAccounts` — confirming target accounts are registered. No funding check exists. [3](#0-2) 

The same pattern is replicated for subtransactions in `dijkstraSubEntitiesTransition`: [4](#0-3) 

**Missing from preservation-of-value.** The `UTXO` rule enforces:

```haskell
{- consumed pp utxo₀ txb = produced pp certState txb -}
runTest $ Shelley.validateValueNotConservedUTxO pp originalUtxo certState txBody
``` [5](#0-4) 

`consumed` for Dijkstra is `getConsumedDijkstraValue`, which delegates to `getConsumedMaryValue`:

```haskell
consumedValue =
  sumUTxO (txInsFilter utxo (txBody ^. inputsTxBodyL))
    <> inject (refunds <> withdrawals)
``` [6](#0-5) 

`produced` for Dijkstra is `getProducedDijkstraValue` → `dijkstraProducedValue` → `conwayProducedValue` → outputs + fee + deposits + treasury donation: [7](#0-6) 

For subtransactions, `dijkstraSubTxProducedValue` = outputs + deposits + treasury donation + burned assets:

<cite repo

### Citations

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxBody.hs (L1319-1319)
```haskell
  directDepositsTxBodyL :: Lens' (TxBody l era) DirectDeposits
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Entities.hs (L211-216)
```haskell
  let directDeposits = tx ^. bodyTxL . directDepositsTxBodyL
      accountsAfterCerts = certStateAfterCerts ^. certDStateL . accountsL
  failOnJust (directDepositsMissingAccounts directDeposits accountsAfterCerts) $
    injectFailure . DirectDepositsToMissingAccounts

  pure $ certStateAfterCerts & certDStateL . accountsL %~ applyDirectDeposits directDeposits
```

**File:** libs/cardano-ledger-core/src/Cardano/Ledger/State/Account.hs (L287-298)
```haskell
-- | Add each direct-deposit amount to the matching account balance.
--
-- /Note/ - There are no checks that direct deposits mention only registered accounts.
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

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Utxo.hs (L380-381)
```haskell
  {- consumed pp utxo₀ txb = produced pp certState txb -}
  runTest $ Shelley.validateValueNotConservedUTxO pp originalUtxo certState txBody
```

**File:** eras/mary/impl/src/Cardano/Ledger/Mary/UTxO.hs (L82-86)
```haskell
    consumedValue =
      sumUTxO (txInsFilter utxo (txBody ^. inputsTxBodyL))
        <> inject (refunds <> withdrawals)
    refunds = getTotalRefundsTxBody pp lookupStakingDeposit lookupDRepDeposit txBody
    withdrawals = fold . unWithdrawals $ txBody ^. withdrawalsTxBodyL
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
