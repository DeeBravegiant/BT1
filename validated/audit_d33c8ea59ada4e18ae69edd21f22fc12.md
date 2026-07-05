### Title
`DirectDeposits` Credited to Account Balances Without Inclusion in Preservation-of-Value Check, Enabling Unbounded ADA Creation - (File: `eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs`)

---

### Summary

The Dijkstra era introduces a `directDeposits` field in the transaction body that allows a transaction to directly credit ADA into staking account balances. However, the `directDeposits` amount is **not included** in either the `consumed` or `produced` side of the preservation-of-value (`consumed == produced`) check enforced by `validateValueNotConservedUTxO`. The `ENTITIES` rule then unconditionally applies those deposits to account balances. An unprivileged transaction sender can therefore include an arbitrarily large `directDeposits` map in a Dijkstra transaction, pass the preservation-of-value check with inputs that only cover outputs + fees + cert deposits + treasury donation, and have the ledger credit the specified accounts with ADA that was never deducted from any UTxO input — creating ADA from nothing.

---

### Finding Description

**Vulnerability class:** Funds/accounting bug — invalid ledger state transition (analog of the deflationary-token accounting mismatch).

**Root cause — three cooperating code sites:**

**1. `getConsumedDijkstraValue` / `dijkstraProducedValue` omit `directDeposits`**

`getConsumedDijkstraValue` aggregates consumed value from the top-level and all sub-transaction bodies by calling `getConsumedMaryValue` on each:

```
consumedValue = sumUTxO inputs <> inject (refunds <> withdrawals)
```

`directDeposits` are absent. [1](#0-0) 

`dijkstraProducedValue` delegates to `conwayProducedValue` (outputs + fees + cert deposits + treasury donation) plus sub-transaction produced values:

```haskell
dijkstraProducedValue pp isRegPoolId txBody =
  conwayProducedValue pp isRegPoolId txBody
    <> foldMap' (getProducedValue pp isRegPoolId . view bodyTxL)
                (txBody ^. subTransactionsTxBodyL)
```

`directDeposits` are absent here too. [2](#0-1) 

**2. The UTXO rule enforces `consumed == produced` without `directDeposits`**

```haskell
{- consumed pp utxo₀ txb = produced pp certState txb -}
runTest $ Shelley.validateValueNotConservedUTxO pp originalUtxo certState txBody
``` [3](#0-2) 

Because neither `consumed` nor `produced` include `directDeposits`, the check passes for any transaction regardless of how large the `directDeposits` map is.

**3. The ENTITIES rule unconditionally applies `directDeposits` to account balances**

After the preservation-of-value check has already passed in the UTXO rule, the ENTITIES rule applies the deposits:

```haskell
let directDeposits = tx ^. bodyTxL . directDepositsTxBodyL
    accountsAfterCerts = certStateAfterCerts ^. certDStateL . accountsL
failOnJust (directDepositsMissingAccounts directDeposits accountsAfterCerts) $
  injectFailure . DirectDepositsToMissingAccounts

pure $ certStateAfterCerts & certDStateL . accountsL %~ applyDirectDeposits directDeposits
``` [4](#0-3) 

The only guard is `directDepositsMissingAccounts`, which checks that target accounts are registered — it does **not** verify that the deposited amounts are funded by the transaction's inputs. [5](#0-4) 

`applyDirectDeposits` itself carries an explicit disclaimer: *"There are no checks that direct deposits mention only registered accounts"* — and no check that the amounts are funded. [5](#0-4) 

The same pattern is present in the `SUBENTITIES` rule: [6](#0-5) 

**Contrast with analogous fields that ARE included in `produced`:**

| Field | Included in `produced`? |
|---|---|
| UTxO outputs | Yes (`shelleyProducedValue`) |
| Transaction fee | Yes |
| Cert deposits (stake key / pool) | Yes (`certsTotalDepositsTxBody`) |
| Treasury donation | Yes (`conwayProducedValue`) |
| **`directDeposits`** | **No** | [7](#0-6) 

---

### Impact Explanation

**Critical — Direct creation of ADA through an invalid ledger state transition.**

Let `D` = total lovelace in the `directDeposits` map of a transaction.

- Before the transaction: total ADA = `UTxO_sum + accounts_sum + deposits + fees + treasury + reserves`
- The preservation-of-value check enforces: `inputs + refunds + withdrawals == outputs + fees + cert_deposits + treasury_donation`
- After the transaction: `accounts_sum` increases by `D`, but no other pot decreases by `D`
- After the transaction: total ADA = previous total + `D`

An attacker can set `D` to any value up to `maxBound Word64` (the compact coin limit) per transaction, and repeat across multiple transactions. This directly violates the global ADA supply invariant and constitutes unbounded ADA minting by an unprivileged party.

---

### Likelihood Explanation

**High.** The entry path requires only:
1. A registered staking account (trivially obtained by submitting a stake key registration certificate).
2. A valid Dijkstra-era transaction with a non-empty `directDeposits` map targeting that account.
3. Sufficient UTxO inputs to cover outputs + fees (the `directDeposits` amount itself need not be funded).

No privileged role, governance majority, or leaked key is required. The `directDeposits` field is a standard, serializable part of the Dijkstra transaction body and is accepted by the ledger's CBOR decoder. [8](#0-7) 

---

### Recommendation

Include `directDeposits` in the `produced` side of the preservation-of-value calculation, analogously to how `treasuryDonation` was added to `conwayProducedValue`:

```haskell
-- In dijkstraProducedValue (or getProducedDijkstraValue):
dijkstraProducedValue pp isRegPoolId txBody =
  conwayProducedValue pp isRegPoolId txBody
    <+> inject (sumDirectDeposits $ txBody ^. directDepositsTxBodyL)
    <> foldMap' (getProducedValue pp isRegPoolId . view bodyTxL)
               (txBody ^. subTransactionsTxBodyL)
```

where `sumDirectDeposits (DirectDeposits dd) = fold dd`.

The same fix must be applied to `dijkstraSubTxProducedValue` for sub-transactions. Additionally, a property-based test asserting preservation of ADA across all Dijkstra transactions (including those with non-empty `directDeposits`) should be added to the test suite.

---

### Proof of Concept

```
-- Setup: attacker has a registered staking account `acct` and a UTxO input worth 2 ADA.
-- Attacker constructs a Dijkstra TopTx:
--   inputs:          { utxo_in }          (2 ADA)
--   outputs:         { addr -> 1 ADA }
--   fee:             1 ADA
--   directDeposits:  { acct -> 1_000_000_000_000 ADA }  -- 1 trillion ADA

-- Preservation-of-value check:
--   consumed = 2 ADA  (UTxO input)
--   produced = 1 ADA (output) + 1 ADA (fee) = 2 ADA
--   2 == 2  ✓  (directDeposits not counted)

-- ENTITIES rule applies directDeposits:
--   acct.balance += 1_000_000_000_000 ADA

-- Net effect: 1_000_000_000_000 ADA created from nothing.
-- Attacker can then withdraw the balance via a standard withdrawal transaction.
```

The `directDepositsMissingAccounts` guard is the only obstacle; it is trivially bypassed by pre-registering the target account. [9](#0-8)

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

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/UTxO.hs (L121-131)
```haskell
conwayProducedValue ::
  ( ConwayEraTxBody era
  , Value era ~ MaryValue
  ) =>
  PParams era ->
  (KeyHash StakePool -> Bool) ->
  TxBody TopTx era ->
  Value era
conwayProducedValue pp isStakePool txBody =
  getProducedMaryValue pp isStakePool txBody
    <+> inject (txBody ^. treasuryDonationTxBodyL)
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxBody.hs (L185-186)
```haskell
    , dtbrDirectDeposits :: !DirectDeposits
    , dtbrAccountBalanceIntervals :: !(AccountBalanceIntervals era)
```
