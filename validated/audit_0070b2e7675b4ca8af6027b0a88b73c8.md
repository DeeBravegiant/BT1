### Title
`DirectDeposits` Excluded from Preservation-of-Value Check Allows Unbounded ADA Creation — (`File: eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs`)

---

### Summary

The Dijkstra era introduces a `DirectDeposits` field in transaction bodies that allows a transaction to credit ADA directly into registered stake account balances. However, the `directDeposits` amount is never included in either the **consumed** or **produced** side of the preservation-of-value equation enforced by `validateValueNotConservedUTxO`. The `ENTITIES` rule applies the deposits to account balances unconditionally, while the UTxO accounting check passes without accounting for them. This allows any unprivileged transaction sender to create ADA from nothing.

---

### Finding Description

The Dijkstra era adds `DirectDeposits` (a `Map AccountAddress Coin`) to both top-level and sub-transaction bodies: [1](#0-0) [2](#0-1) 

In the `ENTITIES` rule, after validating that all target accounts are registered, `applyDirectDeposits` is called unconditionally to credit those amounts into account balances: [3](#0-2) 

The same pattern exists in `SUBENTITIES`: [4](#0-3) 

`applyDirectDeposits` itself carries an explicit warning that it performs no value checks: [5](#0-4) 

Meanwhile, the preservation-of-value check in the Dijkstra UTXO rule calls the standard `validateValueNotConservedUTxO`: [6](#0-5) 

This check uses `consumed = conwayConsumed` and `getProducedValue = getProducedDijkstraValue`: [7](#0-6) 

`getConsumedDijkstraValue` delegates to `getConsumedMaryValue` (a pre-Dijkstra function) and never reads `directDepositsTxBodyL`: [8](#0-7) 

`dijkstraProducedValue` delegates to `conwayProducedValue` plus sub-transaction outputs/deposits/donations, and also never includes `directDeposits`: [9](#0-8) 

`dijkstraSubTxProducedValue` similarly omits `directDeposits`: [10](#0-9) 

The Cardano ledger's preservation-of-value invariant requires that every ADA credited to any pot (UTxO outputs, fees, deposits, treasury, account balances) must be debited from another pot in the same transition. For withdrawals, the amount appears on the **consumed** side of the UTxO equation and is deducted from account balances in `ENTITIES`. For `directDeposits`, the amount is credited to account balances in `ENTITIES` but appears on **neither** side of the UTxO equation. The net effect is that `directDeposits` creates ADA from nothing.

The formal preservation-of-value property that must hold across all transitions is: [11](#0-10) 

---

### Impact Explanation

**Critical — Direct creation of ADA through an invalid ledger state transition.**

An attacker can craft a Dijkstra-era transaction with:
- UTxO inputs and outputs that balance exactly (satisfying `validateValueNotConservedUTxO`)
- A non-empty `directDeposits` map targeting any registered stake accounts

The UTXO rule accepts the transaction. The ENTITIES rule then credits the `directDeposits` amounts into the targeted account balances. The attacker can subsequently withdraw those balances via normal withdrawal transactions. The total ADA supply increases by the sum of all `directDeposits` in the transaction, with no corresponding deduction from any existing pot.

---

### Likelihood Explanation

Any unprivileged transaction sender can submit a Dijkstra-era transaction. The only prerequisite is that the target accounts in `directDeposits` are registered, which is publicly observable on-chain. No privileged key, governance majority, or special role is required. The attacker controls the `directDeposits` map entirely through the serialized transaction body.

---

### Recommendation

Include the total `directDeposits` amount on the **produced** side of the preservation-of-value equation, analogous to how fees and certificate deposits are treated. Concretely, `dijkstraProducedValue` and `dijkstraSubTxProducedValue` should add `inject (sum (unDirectDeposits (txBody ^. directDepositsTxBodyL)))` to the produced value:

```haskell
dijkstraSubTxProducedValue pp isRegPoolId txBody =
  sumAllValue (txBody ^. outputsTxBodyL)
    <> inject ( getTotalDepositsTxBody pp isRegPoolId txBody
             <> txBody ^. treasuryDonationTxBodyL
             <> fold (unDirectDeposits (txBody ^. directDepositsTxBodyL))  -- ADD THIS
              )
    <> burnedMultiAssets txBody
```

The same addition is needed in the top-level `dijkstraProducedValue` path (via `conwayProducedValue` override or a Dijkstra-specific produced function). A corresponding property-based test should be added to verify that `directDeposits` are included in the ADA preservation invariant, analogous to the existing `checkWithdrawalBound` and `potsSumIncreaseWithdrawalsPerTx` tests: [12](#0-11) 

---

### Proof of Concept

1. Observe that `DijkstraEra` registers `consumed = conwayConsumed` and `getProducedValue = getProducedDijkstraValue`, neither of which reads `directDepositsTxBodyL`. [7](#0-6) 

2. Construct a transaction:
   - `inputs = {someUTxOInput}` (value = V ADA)
   - `outputs = {someAddress → V ADA}` (value = V ADA, so consumed == produced)
   - `directDeposits = {registeredAccount → 1_000_000_000 ADA}`

3. The UTXO rule evaluates `consumed = V`, `produced = V` → passes `validateValueNotConservedUTxO`. [6](#0-5) 

4. The ENTITIES rule validates that `registeredAccount` exists, then calls `applyDirectDeposits`, crediting 1,000,000,000 ADA to the account balance. [3](#0-2) 

5. The attacker submits a withdrawal transaction draining the account balance, receiving 1,000,000,000 ADA that did not exist before.

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

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Entities.hs (L211-216)
```haskell
  let directDeposits = tx ^. bodyTxL . directDepositsTxBodyL
      accountsAfterCerts = certStateAfterCerts ^. certDStateL . accountsL
  failOnJust (directDepositsMissingAccounts directDeposits accountsAfterCerts) $
    injectFailure . DirectDepositsToMissingAccounts

  pure $ certStateAfterCerts & certDStateL . accountsL %~ applyDirectDeposits directDeposits
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubEntities.hs (L182-187)
```haskell
  let directDeposits = tx ^. bodyTxL . directDepositsTxBodyL
      accountsAfterSubCerts = certStateAfterSubCerts ^. certDStateL . accountsL
  failOnJust (directDepositsMissingAccounts directDeposits accountsAfterSubCerts) $
    injectFailure . SubDirectDepositsToMissingAccounts

  pure $ certStateAfterSubCerts & certDStateL . accountsL %~ applyDirectDeposits directDeposits
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

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Utxo.hs (L380-381)
```haskell
  {- consumed pp utxo₀ txb = produced pp certState txb -}
  runTest $ Shelley.validateValueNotConservedUTxO pp originalUtxo certState txBody
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs (L78-91)
```haskell
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

**File:** eras/shelley/formal-spec/utxo.tex (L1-18)
```tex
\section{UTxO}
\label{sec:utxo}

A key constraint that must always be satisfied as a result and precondition of
a valid ledger state transition is called the \textit{general accounting
property}, or the \textit{preservation of value} condition. Every piece of
software that is a part of the implementation of the
Cardano cryptocurrency must function in such a way as to not result in
a violation of this rule.
If this condition is not satisfied, it is an indicator of
incorrect accounting, potentially due to
malicious disruption or a bug.

The preservation of value is expressed as an equality that uses values in
the ledger state and the environment, as well as the values in the body of
the signal transaction.
We have defined the rules of the delegation protocol in a way that should
consistently satisfy the preservation of value.
```

**File:** eras/shelley/test-suite/src/Test/Cardano/Ledger/Shelley/Rules/AdaPreservation.hs (L302-310)
```haskell
checkWithdrawalBound :: EraGen era => SourceSignalTarget (CHAIN era) -> Property
checkWithdrawalBound SourceSignalTarget {source, signal, target} =
  counterexample "checkWithdrawalBound" $
    rewardDelta === withdrawals signal
  where
    rewardDelta :: Coin
    rewardDelta =
      sumAccountsBalances (chainNes source ^. nesEsL . esLStateL . lsCertStateL)
        <-> sumAccountsBalances (chainNes target ^. nesEsL . esLStateL . lsCertStateL)
```
