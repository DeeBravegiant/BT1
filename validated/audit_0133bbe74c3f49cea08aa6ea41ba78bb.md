### Title
Direct Deposits Omitted from Preservation-of-Value Check Enables Unbounded ADA Creation — (File: `eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs`)

---

### Summary

The Dijkstra era introduces `directDeposits` as a new transaction body field that credits ADA directly into account balances. The `ENTITIES` rule applies these deposits to account balances, but neither the consumed nor the produced side of the preservation-of-value (POV) check accounts for them. Any unprivileged transaction sender can craft a transaction that passes the POV check while simultaneously crediting arbitrary ADA into account balances, creating ADA from nothing.

---

### Finding Description

**Vulnerability class:** Funds/accounting bug — invalid state transition via missing value-flow term in the POV equation.

The Dijkstra era UTXO transition rule enforces the POV check at line 381:

```haskell
{- consumed pp utxo₀ txb = produced pp certState txb -}
runTest $ Shelley.validateValueNotConservedUTxO pp originalUtxo certState txBody
``` [1](#0-0) 

`validateValueNotConservedUTxO` delegates to `consumed` (which resolves to `getConsumedDijkstraValue`) and `produced` (which resolves to `getProducedDijkstraValue`).

**Consumed side** — `getConsumedDijkstraValue` sums UTxO inputs, withdrawals, refunds, and minted assets for the top-level body and all sub-transactions. Direct deposits are absent:

```haskell
txBodyConsumedValue = getConsumedMaryValue pp lookupStakingDeposit lookupDRepDeposit utxo
-- getConsumedMaryValue: UTxO inputs + withdrawals + refunds + minted (no directDeposits)
``` [2](#0-1) 

**Produced side** — `dijkstraProducedValue` sums UTxO outputs, fee, certificate deposits, treasury donation, and burned assets for the top-level body and all sub-transactions. Direct deposits are absent:

```haskell
dijkstraProducedValue pp isRegPoolId txBody =
  conwayProducedValue pp isRegPoolId txBody   -- outputs + fee + cert deposits + treasury donation
    <> foldMap' (getProducedValue pp isRegPoolId . view bodyTxL)
                (txBody ^. subTransactionsTxBodyL)
-- directDeposits never referenced
``` [3](#0-2) 

`dijkstraTotalDepositsTxBody`, which feeds the produced side, only covers certificate deposits and proposal deposits — not direct deposits:

```haskell
dijkstraTotalDepositsTxBody pp isPoolRegisted txBody =
  getTotalDepositsTxCerts pp isPoolRegisted (txBody ^. certsTxBodyL)
    <+> conwayProposalsDeposits pp txBody
``` [4](#0-3) 

After the UTXO rule passes, the `ENTITIES` rule unconditionally applies direct deposits to account balances:

```haskell
let directDeposits = tx ^. bodyTxL . directDepositsTxBodyL
    accountsAfterCerts = certStateAfterCerts ^. certDStateL . accountsL
failOnJust (directDepositsMissingAccounts directDeposits accountsAfterCerts) $
  injectFailure . DirectDepositsToMissingAccounts

pure $ certStateAfterCerts & certDStateL . accountsL %~ applyDirectDeposits directDeposits
``` [5](#0-4) 

`applyDirectDeposits` adds the specified coin to each targeted account balance without any deduction from the UTxO or any other pot:

```haskell
applyDirectDeposits (DirectDeposits dd) =
  updateAccountBalances
    (\amount account -> addCompactCoin amount (account ^. balanceAccountStateL))
    dd
``` [6](#0-5) 

The only guard before `applyDirectDeposits` is `directDepositsMissingAccounts`, which only checks that target accounts are registered — it does not check that the deposited amounts are funded by the transaction's UTxO inputs. [7](#0-6) 

The analogy to the external report is exact: just as `transferFrom` silently succeeds without actually moving tokens (so the contract records a transfer that never happened), the Dijkstra ledger silently credits account balances without deducting the corresponding ADA from the UTxO — the "transfer" is recorded in account state but the value was never consumed.

---

### Impact Explanation

**Critical — Direct creation of ADA through an invalid ledger state transition.**

An attacker can include an arbitrarily large `directDeposits` map in a transaction that otherwise satisfies the POV check. The ENTITIES rule credits those amounts to account balances without any corresponding deduction. Total ADA in the system (UTxO + accounts + fees + deposits + treasury + reserves) increases by the sum of all direct deposit amounts in every such transaction. This violates the fundamental Preservation of Ada theorem that the ledger is designed to uphold.

---

### Likelihood Explanation

**High.** The attack requires only the ability to submit a valid Dijkstra-era transaction — no privileged role, no governance majority, no leaked key. Any wallet or script author can construct such a transaction. The only prerequisite is that the target accounts are registered (enforced by `directDepositsMissingAccounts`), which the attacker can satisfy by registering their own stake credentials first.

---

### Recommendation

Include the sum of all direct deposit amounts on the **produced** side of the POV equation. Concretely, `dijkstraProducedValue` (and `dijkstraSubTxProducedValue` for sub-transactions) should add `inject (fold . unDirectDeposits $ txBody ^. directDepositsTxBodyL)` to the produced value, mirroring how withdrawals are added to the consumed side. This ensures that every lovelace credited to an account via `directDeposits` must be funded by the transaction's UTxO inputs.

---

### Proof of Concept

**Setup:** Alice controls a UTxO entry `u` containing 100 ADA. Alice also has a registered stake credential with account balance 0.

**Attack transaction (TopTx):**
- `inputs = {u}` (100 ADA)
- `outputs = [Alice_addr ↦ 95 ADA]`
- `fee = 5 ADA`
- `directDeposits = {Alice_account ↦ 50 ADA}`

**POV check (UTXO rule):**
```
consumed = 100 ADA   (UTxO input)
produced = 95 + 5 = 100 ADA   (outputs + fee)
100 == 100  ✓  — transaction accepted
```

**ENTITIES rule:**
```
applyDirectDeposits {Alice_account ↦ 50 ADA}
→ Alice's account balance: 0 + 50 = 50 ADA
```

**Result after transaction:**
- Alice's UTxO: 95 ADA
- Alice's account: 50 ADA
- Fee pot: +5 ADA
- **Total: 150 ADA** (50 ADA created from nothing; started with 100 ADA)

The attack is repeatable: each submission of such a transaction mints additional ADA bounded only by the attacker's ability to pay fees.

### Citations

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

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxBody.hs (L1003-1007)
```haskell
dijkstraTotalDepositsTxBody ::
  ConwayEraTxBody era => PParams era -> (KeyHash StakePool -> Bool) -> TxBody l era -> Coin
dijkstraTotalDepositsTxBody pp isPoolRegisted txBody =
  getTotalDepositsTxCerts pp isPoolRegisted (txBody ^. certsTxBodyL)
    <+> conwayProposalsDeposits pp txBody
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
