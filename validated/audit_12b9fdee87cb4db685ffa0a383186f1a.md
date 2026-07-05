### Title
Dijkstra `directDeposits` Amounts Excluded from Preservation-of-Value Check, Enabling ADA Creation from Nothing — (`eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs`)

---

### Summary

The Dijkstra era introduces a `directDeposits` transaction body field that credits ADA directly to registered account balances. However, these amounts are absent from both the `consumed` and `produced` calculations used by the preservation-of-value check (`validateValueNotConservedUTxO`). An unprivileged transaction sender can include an arbitrarily large `directDeposits` map in a Dijkstra transaction, pass the balance check without providing the corresponding ADA in UTxO inputs, and have that ADA credited to their account — creating ADA from nothing.

---

### Finding Description

**The mismatch.** The Dijkstra UTXO transition rule enforces preservation of value at line 381 of `eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Utxo.hs`:

```haskell
{- consumed pp utxo₀ txb = produced pp certState txb -}
runTest $ Shelley.validateValueNotConservedUTxO pp originalUtxo certState txBody
``` [1](#0-0) 

`validateValueNotConservedUTxO` delegates to the `EraUTxO` instance's `consumed`/`produced` functions:

```haskell
validateValueNotConservedUTxO pp utxo certState txBody =
  failureUnless (consumedValue == producedValue) $
    ValueNotConservedUTxO Mismatch {mismatchSupplied = consumedValue, mismatchExpected = producedValue}
  where
    consumedValue = consumed pp certState utxo txBody
    producedValue = produced pp certState txBody
``` [2](#0-1) 

For `DijkstraEra`, `getConsumedValue` is `getConsumedDijkstraValue` and `getProducedValue` is `getProducedDijkstraValue`:

```haskell
instance EraUTxO DijkstraEra where
  consumed = conwayConsumed
  getConsumedValue = getConsumedDijkstraValue
  getProducedValue = getProducedDijkstraValue
``` [3](#0-2) 

`getConsumedDijkstraValue` aggregates UTxO inputs, minted multi-assets, refunds, and withdrawals — from both the top-level transaction and all sub-transactions. It does **not** include `directDeposits`:

```haskell
getConsumedDijkstraValue pp lookupStakingDeposit lookupDRepDeposit utxo txBody =
  withBothTxLevels txBody
    (\topTxBody -> txBodyConsumedValue topTxBody <> subTransactionsConsumedValue topTxBody)
    txBodyConsumedValue
  where
    txBodyConsumedValue = getConsumedMaryValue pp lookupStakingDeposit lookupDRepDeposit utxo
    subTransactionsConsumedValue topTxBody =
      foldMap' (getConsumedValue pp lookupStakingDeposit lookupDRepDeposit utxo . view bodyTxL)
               (topTxBody ^. subTransactionsTxBodyL)
``` [4](#0-3) 

`dijkstraProducedValue` aggregates UTxO outputs, fees, certificate deposits, and burned assets — also without `directDeposits`:

```haskell
dijkstraProducedValue pp isRegPoolId txBody =
  conwayProducedValue pp isRegPoolId txBody
    <> foldMap' (getProducedValue pp isRegPoolId . view bodyTxL)
                (txBody ^. subTransactionsTxBodyL)
``` [5](#0-4) 

**The actual state change.** After the UTXO rule passes, the ENTITIES rule applies `directDeposits` unconditionally to account balances:

```haskell
let directDeposits = tx ^. bodyTxL . directDepositsTxBodyL
    accountsAfterCerts = certStateAfterCerts ^. certDStateL . accountsL
failOnJust (directDepositsMissingAccounts directDeposits accountsAfterCerts) $
  injectFailure . DirectDepositsToMissingAccounts

pure $ certStateAfterCerts & certDStateL . accountsL %~ applyDirectDeposits directDeposits
``` [6](#0-5) 

`applyDirectDeposits` adds each amount to the matching account balance with no further checks:

```haskell
applyDirectDeposits (DirectDeposits dd) =
  updateAccountBalances
    (\amount account -> addCompactCoin amount (account ^. balanceAccountStateL))
    dd
``` [7](#0-6) 

The only validation performed on `directDeposits` before application is `directDepositsMissingAccounts`, which only checks that target accounts are registered — it does not verify that the deposited amounts are funded by UTxO inputs. [8](#0-7) 

The same gap exists in the `SUBENTITIES` rule for sub-transactions: [9](#0-8) 

**Net effect.** For a transaction with UTxO inputs worth `X` ADA, UTxO outputs worth `X − fee` ADA, fee `fee`, and `directDeposits` of `Y` ADA:

| Side | Value |
|---|---|
| consumed | X |
| produced | (X − fee) + fee = X |
| Balance check | ✓ passes |
| Account balance change | +Y ADA (unfunded) |

`Y` ADA is created from nothing.

---

### Impact Explanation

This is a **Critical** impact: direct creation of ADA through an invalid ledger state transition. An attacker can inflate their own account balance (or any registered account's balance) by an arbitrary amount without providing the corresponding ADA in UTxO inputs. The ADA credited to accounts has no corresponding debit anywhere in the ledger state, violating the global preservation-of-value invariant. The attacker can subsequently withdraw the fabricated ADA via the normal withdrawal mechanism.

---

### Likelihood Explanation

Any unprivileged transaction sender on the Dijkstra network can exploit this. The only precondition is that the target account address is registered, which the attacker can arrange themselves. No privileged access, governance majority, or key compromise is required. The attack is a single transaction.

---

### Recommendation

Include `directDeposits` amounts in the `produced` side of the preservation-of-value calculation, analogously to how certificate deposits are included. Concretely, `getProducedDijkstraValue` (and `dijkstraProducedValue`) should add `fold (unDirectDeposits (txBody ^. directDepositsTxBodyL))` to the produced value, so that the transaction sender must provide the corresponding ADA in UTxO inputs. The same fix is needed for sub-transactions in `dijkstraSubTxProducedValue`. This mirrors the existing pattern for `totalDeposits` in `conwayProducedValue`.

---

### Proof of Concept

1. Attacker registers a stake credential, obtaining account address `acct`.
2. Attacker constructs a Dijkstra `TopTx` with:
   - UTxO inputs: 10 ADA
   - UTxO outputs: 9 ADA (to self)
   - Fee: 1 ADA
   - `directDeposits`: `{ acct → 1_000_000_000 ADA }`
3. `validateValueNotConservedUTxO` checks `consumed (10) == produced (9 + 1 = 10)` — passes.
4. `directDepositsMissingAccounts` confirms `acct` is registered — passes.
5. `applyDirectDeposits` credits 1,000,000,000 ADA to `acct`.
6. Attacker submits a withdrawal transaction draining `acct`.
7. Net result: attacker gained 1,000,000,000 ADA at a cost of 1 ADA in fees.

### Citations

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Utxo.hs (L380-381)
```haskell
  {- consumed pp utxo₀ txb = produced pp certState txb -}
  runTest $ Shelley.validateValueNotConservedUTxO pp originalUtxo certState txBody
```

**File:** eras/shelley/impl/src/Cardano/Ledger/Shelley/Rules/Utxo.hs (L513-518)
```haskell
validateValueNotConservedUTxO pp utxo certState txBody =
  failureUnless (consumedValue == producedValue) $
    ValueNotConservedUTxO Mismatch {mismatchSupplied = consumedValue, mismatchExpected = producedValue}
  where
    consumedValue = consumed pp certState utxo txBody
    producedValue = produced pp certState txBody
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

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubEntities.hs (L181-187)
```haskell

  let directDeposits = tx ^. bodyTxL . directDepositsTxBodyL
      accountsAfterSubCerts = certStateAfterSubCerts ^. certDStateL . accountsL
  failOnJust (directDepositsMissingAccounts directDeposits accountsAfterSubCerts) $
    injectFailure . SubDirectDepositsToMissingAccounts

  pure $ certStateAfterSubCerts & certDStateL . accountsL %~ applyDirectDeposits directDeposits
```
