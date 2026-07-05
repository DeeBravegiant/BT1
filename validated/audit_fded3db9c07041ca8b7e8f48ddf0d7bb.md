### Title
Direct Deposits Excluded from Value Conservation Check Enables ADA Creation — (File: `eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs`)

---

### Summary

In the Dijkstra era, `directDeposits` declared in a transaction body are applied to stake account balances via `applyDirectDeposits` in the `ENTITIES` rule, but are **not included** in the `produced` side of the value conservation equation (`consumed == produced`) enforced in the `UTXO` rule. Any transaction author can credit arbitrary ADA to registered stake accounts without funding those deposits from UTxO inputs, creating ADA from nothing.

---

### Finding Description

**Root cause — `dijkstraProducedValue` omits direct deposits:**

`dijkstraProducedValue` computes the produced value as `conwayProducedValue` plus sub-transaction produced values. Neither branch includes the `directDeposits` field from the transaction body. [1](#0-0) 

`conwayProducedValue` is a Conway-era function that has no knowledge of the Dijkstra-only `directDepositsTxBodyL` field. `dijkstraProducedValue` calls it directly and only adds sub-transaction produced values, never adding the direct deposit total.

Similarly, `getConsumedDijkstraValue` delegates to `getConsumedMaryValue` per transaction body and does not include direct deposits on the consumed side either. [2](#0-1) 

**Value conservation check does not see direct deposits:**

The `UTXO` transition rule calls `Shelley.validateValueNotConservedUTxO`, which uses the above `consumed`/`produced` functions. Since direct deposits appear in neither side, the check passes regardless of the direct deposit amounts. [3](#0-2) 

**Direct deposits are nonetheless applied to account balances:**

In `dijkstraEntitiesTransition`, after certificate processing, `applyDirectDeposits directDeposits` is called unconditionally, crediting the declared amounts to the target accounts. This state change is entirely outside the value conservation check. [4](#0-3) 

`applyDirectDeposits` calls `updateAccountBalances` which adds each declared coin amount to the matching account's balance: [5](#0-4) 

**Same issue in sub-transactions:**

`dijkstraSubEntitiesTransition` also calls `applyDirectDeposits` for sub-transaction direct deposits, and sub-transactions have no value conservation check at all in `dijkstraSubUtxoTransition`. [6](#0-5) 

**Existing validations do not close the gap:**

`validateWrongNetworkInDirectDeposit` only checks that target `AccountAddress` values carry the correct network ID. [7](#0-6) 

`directDepositsMissingAccounts` only checks that target credentials are registered accounts. [8](#0-7) 

Neither check verifies that the declared deposit amounts are funded by the transaction's UTxO inputs.

---

### Impact Explanation

**Critical — Direct creation of ADA through an invalid ledger state transition.**

The preservation-of-value invariant is the foundational accounting property of the Cardano ledger. Because `directDeposits` are absent from `produced`, the equation:

```
consumed(inputs + withdrawals + refunds) == produced(outputs + fee + cert_deposits)
```

holds without accounting for the ADA credited to accounts. The attacker's account balance increases by the direct deposit amount while the UTxO set is only reduced by `outputs + fee + cert_deposits`. The difference is net ADA creation. The attacker can subsequently drain the inflated balance via a standard withdrawal transaction.

---

### Likelihood Explanation

**High.** The attacker only needs:
1. A registered stake credential (standard operation, costs the key deposit).
2. The ability to submit a Dijkstra-era transaction (any unprivileged user).

No governance majority, trusted role, key compromise, or Sybil attack is required. The exploit is deterministic and repeatable.

---

### Recommendation

Include the sum of all direct deposits in the `produced` side of the value conservation equation. Concretely, `dijkstraProducedValue` should add the total coin declared in `txBody ^. directDepositsTxBodyL` to the value it returns, and `dijkstraSubTxProducedValue` should do the same for sub-transaction bodies.

```haskell
dijkstraProducedValue pp isRegPoolId txBody =
  conwayProducedValue pp isRegPoolId txBody
    <> Val.inject (foldMap id . unDirectDeposits $ txBody ^. directDepositsTxBodyL)
    <> foldMap'
         (getProducedValue pp isRegPoolId . view bodyTxL)
         (txBody ^. subTransactionsTxBodyL)
```

An analogous fix is needed in `dijkstraSubTxProducedValue` for sub-transaction direct deposits.

---

### Proof of Concept

1. Register a stake credential `cred` and obtain its `AccountAddress myAddr`.
2. Construct a Dijkstra top-level transaction:
   - **Inputs**: UTxO entries whose total value equals `outputs + fee` (no extra for direct deposits).
   - **Outputs**: change output returning the remainder.
   - **`directDeposits`**: `Map.singleton myAddr (Coin 1_000_000_000)` — 1,000 ADA.
3. Submit the transaction.
4. `Shelley.validateValueNotConservedUTxO` passes because `consumed == produced` (direct deposits absent from both sides).
5. `dijkstraEntitiesTransition` calls `applyDirectDeposits`, crediting 1,000 ADA to `cred`'s account balance.
6. Submit a withdrawal transaction draining `cred`'s account.
7. Net result: 1,000 ADA created from nothing, with no corresponding reduction in any other pot.

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

**File:** libs/cardano-ledger-core/src/Cardano/Ledger/State/Account.hs (L329-343)
```haskell
directDepositsMissingAccounts ::
  EraAccounts era =>
  DirectDeposits ->
  Accounts era ->
  Maybe DirectDeposits
directDepositsMissingAccounts (DirectDeposits dds) accounts
  | Map.foldrWithKey' checkRegistered True dds = Nothing
  | otherwise = Just $ DirectDeposits $ Map.foldrWithKey' collectMissing Map.empty dds
  where
    isRegistered (AccountAddress _ (AccountId credential)) =
      isAccountRegistered credential accounts
    checkRegistered addr _ acc = acc && isRegistered addr
    collectMissing addr amount acc
      | isRegistered addr = acc
      | otherwise = Map.insert addr amount acc
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubEntities.hs (L182-187)
```haskell
  let directDeposits = tx ^. bodyTxL . directDepositsTxBodyL
      accountsAfterSubCerts = certStateAfterSubCerts ^. certDStateL . accountsL
  failOnJust (directDepositsMissingAccounts directDeposits accountsAfterSubCerts) $
    injectFailure . SubDirectDepositsToMissingAccounts

  pure $ certStateAfterSubCerts & certDStateL . accountsL %~ applyDirectDeposits directDeposits
```
