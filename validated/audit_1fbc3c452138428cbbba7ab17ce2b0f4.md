### Title
`directDeposits` Omitted from Preservation-of-Value Check Enables Unbounded ADA Creation — (`File: eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs`)

---

### Summary

The Dijkstra era introduces a new `directDeposits` field in both top-level and sub-transaction bodies that deposits ADA directly into registered account balances. The ENTITIES and SUBENTITIES rules faithfully apply these deposits to account state. However, `directDeposits` are absent from **both** the consumed and produced sides of the preservation-of-value (PoV) check in the UTXO rule. Because the PoV check does not require the transaction author to fund the direct-deposit amounts from UTxO inputs, any transaction can inflate account balances by an arbitrary amount without providing the corresponding ADA. This is a direct, attacker-controlled ADA creation path.

---

### Finding Description

**Background — how PoV works for analogous fields**

Every ADA flow that leaves the UTxO must appear on the *produced* side of the PoV equation so that the transaction author is forced to fund it from inputs:

| Flow | Produced side |
|---|---|
| UTxO outputs | `sumAllValue outputs` |
| Transaction fee | `txBody ^. feeTxBodyL` |
| Certificate deposits | `getTotalDepositsTxBody pp isRegPoolId txBody` |
| Treasury donation | `txBody ^. treasuryDonationTxBodyL` |

`directDeposits` move ADA from the UTxO into account balances — the same direction as all of the above — yet they are absent from the produced side.

**Root cause — `dijkstraProducedValue` / `dijkstraSubTxProducedValue`**

`dijkstraProducedValue` (top-level) delegates to `conwayProducedValue` plus sub-transaction produced values:

```haskell
dijkstraProducedValue pp isRegPoolId txBody =
  conwayProducedValue pp isRegPoolId txBody
    <> foldMap'
      (getProducedValue pp isRegPoolId . view bodyTxL)
      (txBody ^. subTransactionsTxBodyL)
```

`conwayProducedValue` predates `directDeposits` and does not include them. [1](#0-0) 

`dijkstraSubTxProducedValue` (sub-transaction) is:

```haskell
dijkstraSubTxProducedValue pp isRegPoolId txBody =
  sumAllValue (txBody ^. outputsTxBodyL)
    <> inject (getTotalDepositsTxBody pp isRegPoolId txBody <> txBody ^. treasuryDonationTxBodyL)
    <> burnedMultiAssets txBody
```

`directDeposits` are not included here either. [2](#0-1) 

**Root cause — `getConsumedDijkstraValue`**

The consumed side also omits `directDeposits`. It delegates to `getConsumedMaryValue` for every body (top-level and sub-transaction), which only sums UTxO inputs, withdrawals, and deposit refunds:

```haskell
txBodyConsumedValue = getConsumedMaryValue pp lookupStakingDeposit lookupDRepDeposit utxo
``` [3](#0-2) 

**Root cause — ENTITIES / SUBENTITIES unconditionally apply `directDeposits`**

After the UTXO check passes, the ENTITIES rule applies the direct deposits to account balances with no further funding check:

```haskell
pure $ certStateAfterCerts & certDStateL . accountsL %~ applyDirectDeposits directDeposits
``` [4](#0-3) 

The SUBENTITIES rule (for sub-transactions) does the same:

```haskell
pure $ certStateAfterSubCerts & certDStateL . accountsL %~ applyDirectDeposits directDeposits
``` [5](#0-4) 

`applyDirectDeposits` unconditionally adds the specified coin to each account balance: [6](#0-5) 

**The PoV check that is actually executed**

```haskell
{- consumed pp utxo₀ txb = produced pp certState txb -}
runTest $ Shelley.validateValueNotConservedUTxO pp originalUtxo certState txBody
``` [7](#0-6) 

This check uses `getConsumedDijkstraValue` and `getProducedDijkstraValue`, neither of which includes `directDeposits`. The check therefore passes regardless of the `directDeposits` amount.

**Both top-level and sub-transaction bodies carry `directDeposits`**

`DijkstraTxBodyRaw` (top-level) has `dtbrDirectDeposits :: !DirectDeposits`. [8](#0-7) 

`DijkstraSubTxBodyRaw` (sub-transaction) has `dstbrDirectDeposits :: !DirectDeposits`. [9](#0-8) 

Both are applied to account balances without PoV coverage.

---

### Impact Explanation

**Critical — Direct creation of ADA through an invalid ledger state transition.**

An attacker submits a transaction whose `directDeposits` map credits their own account with an arbitrary amount D. The PoV check passes because D is absent from both sides of the equation. The ENTITIES rule then adds D to the attacker's account balance. The attacker has created D lovelace from nothing, violating the total-supply invariant. The same attack works inside a sub-transaction, compounding the effect within a single top-level transaction.

The six ADA pots (UTxO, deposits, fees, treasury, reserves, reward accounts) must always sum to the fixed total supply. After this attack, reward accounts hold more ADA than was ever deducted from any other pot, permanently breaking that invariant. Recovery would require a hard fork to correct the inflated balances.

---

### Likelihood Explanation

**High.** The attack requires only the ability to submit a valid Dijkstra-era transaction — no privileged role, no governance majority, no leaked key. The `directDeposits` field is a standard, serializable part of the transaction body. Any transaction author who discovers this omission can exploit it immediately. The Dijkstra era is new and this feature has not been through the same scrutiny as older mechanisms.

---

### Recommendation

Include `directDeposits` in the **produced** side of the preservation-of-value calculation, analogous to how certificate deposits and treasury donations are included. Concretely:

1. In `dijkstraProducedValue` (top-level), add the sum of all `directDeposits` coin values from the top-level body to the produced value.
2. In `dijkstraSubTxProducedValue` (sub-transaction), add the sum of all `directDeposits` coin values from the sub-transaction body.
3. Add a corresponding helper (e.g., `totalDirectDepositsTxBody`) that folds over `unDirectDeposits` and sums the `Coin` values, then inject it into `MaryValue` before adding to the produced side.

This forces the transaction author to fund direct deposits from UTxO inputs, restoring the preservation-of-value invariant.

---

### Proof of Concept

Consider a Dijkstra-era transaction with:

| Field | Value |
|---|---|
| Inputs | 1 UTxO entry worth 5 ADA |
| Outputs | 1 UTxO entry worth 4 ADA |
| Fee | 1 ADA |
| `directDeposits` | `{attackerAccount: 1_000_000 ADA}` |

**PoV check (as currently implemented):**
- consumed = 5 ADA (inputs)
- produced = 4 ADA (outputs) + 1 ADA (fee) = 5 ADA
- 5 == 5 → **check passes**

**ENTITIES rule:**
- `applyDirectDeposits {attackerAccount: 1_000_000 ADA}` → attacker's account balance += 1,000,000 ADA

**Net effect:** 5 ADA was consumed from the UTxO; 4 ADA was produced in the UTxO; 1 ADA went to fees; and 1,000,000 ADA was created in the attacker's account balance from nothing. The total ADA supply has increased by 1,000,000 ADA.

The same attack applies to sub-transactions: a sub-transaction body with `directDeposits` passes through `dijkstraSubTxProducedValue` (which also omits `directDeposits`) and is applied by `dijkstraSubEntitiesTransition`.

### Citations

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

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs (L102-106)
```haskell
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

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Utxo.hs (L380-381)
```haskell
  {- consumed pp utxo₀ txb = produced pp certState txb -}
  runTest $ Shelley.validateValueNotConservedUTxO pp originalUtxo certState txBody
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxBody.hs (L185-188)
```haskell
    , dtbrDirectDeposits :: !DirectDeposits
    , dtbrAccountBalanceIntervals :: !(AccountBalanceIntervals era)
    } ->
    DijkstraTxBodyRaw TopTx era
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxBody.hs (L206-209)
```haskell
    , dstbrDirectDeposits :: !DirectDeposits
    , dstbrAccountBalanceIntervals :: !(AccountBalanceIntervals era)
    } ->
    DijkstraTxBodyRaw SubTx era
```
