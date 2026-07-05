### Title
Unaccounted `directDeposits` in Dijkstra Value Conservation Allows ADA Creation Out of Thin Air — (`File: eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs`)

---

### Summary

The Dijkstra era introduces a `directDeposits` field in both top-level and sub-transaction bodies. When processed, `applyDirectDeposits` unconditionally adds ADA to account balances. However, neither `dijkstraProducedValue` (for top-level transactions) nor `dijkstraSubTxProducedValue` (for sub-transactions) includes the `directDeposits` sum in the "produced" side of the value conservation equation. An unprivileged transaction author can therefore include arbitrary `directDeposits` amounts in a valid transaction, causing ADA to be credited to account balances without any corresponding deduction from the UTxO — a direct creation of ADA through an invalid ledger state transition.

---

### Finding Description

**Vulnerability class:** Funds/accounting bug — value conservation (preservation of value) check missing a value-flow term.

The Cardano ledger enforces `consumed == produced` for every transaction. For the Dijkstra era, the produced-value functions are:

**Top-level transaction** (`dijkstraProducedValue`, line 102–106):
```haskell
dijkstraProducedValue pp isRegPoolId txBody =
  conwayProducedValue pp isRegPoolId txBody          -- outputs + fee + deposits + donation
    <> foldMap'
      (getProducedValue pp isRegPoolId . view bodyTxL)
      (txBody ^. subTransactionsTxBodyL)
```
`conwayProducedValue` is:
```haskell
conwayProducedValue pp isStakePool txBody =
  getProducedMaryValue pp isStakePool txBody
    <+> inject (txBody ^. treasuryDonationTxBodyL)
```
Neither includes `txBody ^. directDepositsTxBodyL`.

**Sub-transaction** (`dijkstraSubTxProducedValue`, line 258–261):
```haskell
dijkstraSubTxProducedValue pp isRegPoolId txBody =
  sumAllValue (txBody ^. outputsTxBodyL)
    <> inject (getTotalDepositsTxBody pp isRegPoolId txBody <> txBody ^. treasuryDonationTxBodyL)
    <> burnedMultiAssets txBody
```
Again, `directDeposits` is absent.

Meanwhile, `applyDirectDeposits` (called unconditionally in both `dijkstraEntitiesTransition` and `dijkstraSubEntitiesTransition`) adds ADA to account balances:
```haskell
applyDirectDeposits (DirectDeposits dd) =
  updateAccountBalances
    (\amount account -> addCompactCoin amount (account ^. balanceAccountStateL))
    dd
```

The symmetry with withdrawals makes the omission clear:

| Flow | Direction | Included in conservation check? |
|---|---|---|
| Withdrawals | account balances → UTxO | ✅ Yes, in `consumed` |
| Direct deposits | UTxO → account balances | ❌ **No, missing from `produced`** |

Withdrawals are correctly included on the consumed side in `getConsumedMaryValue`:
```haskell
withdrawals = fold . unWithdrawals $ txBody ^. withdrawalsTxBodyL
```
Direct deposits are the exact inverse flow and must appear on the produced side, but they do not.

---

### Impact Explanation

**Critical. Direct creation of ADA through an invalid ledger state transition.**

An attacker submits a Dijkstra-era transaction where:
- Inputs total `N` ADA
- Outputs total `N - fee` ADA (value conservation check passes as-is)
- `directDeposits` field credits `M` ADA to one or more registered accounts

Because `M` is not included in `produced`, the conservation check `consumed == produced` passes. Yet `applyDirectDeposits` adds `M` ADA to account balances. The total ADA in the system increases by `M` — ADA is minted without authorization, violating the fundamental invariant that Ada cannot be created or destroyed.

---

### Likelihood Explanation

**Medium.** The Dijkstra era is the current experimental era in the repository and is a production implementation target. The `directDeposits` field is fully specified in the CDDL (`direct_deposits = {+ reward_account => coin}`), serialization is implemented, and the state-transition rules (`ENTITIES`, `SUBENTITIES`) are wired up. Any unprivileged transaction author who can submit a Dijkstra-era transaction and has at least one registered account as a target can exploit this. No special keys, governance majority, or privileged access is required.

---

### Recommendation

Add the sum of `directDeposits` to the produced-value calculation, mirroring how withdrawals are added to consumed. For top-level transactions, extend `dijkstraProducedValue`:

```haskell
dijkstraProducedValue pp isRegPoolId txBody =
  conwayProducedValue pp isRegPoolId txBody
    <> inject (fold . unDirectDeposits $ txBody ^. directDepositsTxBodyL)  -- ADD THIS
    <> foldMap'
      (getProducedValue pp isRegPoolId . view bodyTxL)
      (txBody ^. subTransactionsTxBodyL)
```

For sub-transactions, extend `dijkstraSubTxProducedValue`:

```haskell
dijkstraSubTxProducedValue pp isRegPoolId txBody =
  sumAllValue (txBody ^. outputsTxBodyL)
    <> inject ( getTotalDepositsTxBody pp isRegPoolId txBody
             <> txBody ^. treasuryDonationTxBodyL
             <> fold (unDirectDeposits (txBody ^. directDepositsTxBodyL)) )  -- ADD THIS
    <> burnedMultiAssets txBody
```

Add a property-based test asserting that the total ADA in the system (UTxO + account balances + fee pot + deposit pot + treasury) is invariant across any valid Dijkstra transaction that uses `directDeposits`.

---

### Proof of Concept

**Root cause files:**

`dijkstraProducedValue` and `dijkstraSubTxProducedValue` — both missing `directDeposits`: [1](#0-0) [2](#0-1) 

`applyDirectDeposits` unconditionally adds ADA to account balances with no corresponding UTxO deduction: [3](#0-2) 

`directDeposits` applied in both top-level and sub-transaction entity rules: [4](#0-3) [5](#0-4) 

`directDeposits` field present in both `DijkstraTxBodyRaw TopTx` and `DijkstraTxBodyRaw SubTx`: [6](#0-5) [7](#0-6) 

Withdrawals (the symmetric inverse flow) are correctly included in `consumed` — confirming the omission of `directDeposits` from `produced` is not intentional: [8](#0-7) 

**Concrete exploit scenario:**

1. Attacker registers a stake credential (account) — pays `keyDeposit`.
2. Attacker submits a Dijkstra top-level transaction:
   - Inputs: UTxO covering fee only (e.g., 0.5 ADA)
   - Outputs: change back to self (e.g., 0.3 ADA)
   - Fee: 0.2 ADA
   - `directDeposits`: `{ attacker_account: 1_000_000_000 }` (1000 ADA)
3. Value conservation check: `consumed = 0.5 ADA`, `produced = 0.3 + 0.2 = 0.5 ADA` — **passes**.
4. `applyDirectDeposits` credits 1000 ADA to attacker's account balance.
5. Attacker withdraws 1000 ADA in a subsequent transaction.
6. Net result: 1000 ADA created from nothing.

### Citations

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs (L102-106)
```haskell
dijkstraProducedValue pp isRegPoolId txBody =
  conwayProducedValue pp isRegPoolId txBody
    <> foldMap'
      (getProducedValue pp isRegPoolId . view bodyTxL)
      (txBody ^. subTransactionsTxBodyL)
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs (L258-261)
```haskell
dijkstraSubTxProducedValue pp isRegPoolId txBody =
  sumAllValue (txBody ^. outputsTxBodyL)
    <> inject (getTotalDepositsTxBody pp isRegPoolId txBody <> txBody ^. treasuryDonationTxBodyL)
    <> burnedMultiAssets txBody
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

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubEntities.hs (L182-187)
```haskell
  let directDeposits = tx ^. bodyTxL . directDepositsTxBodyL
      accountsAfterSubCerts = certStateAfterSubCerts ^. certDStateL . accountsL
  failOnJust (directDepositsMissingAccounts directDeposits accountsAfterSubCerts) $
    injectFailure . SubDirectDepositsToMissingAccounts

  pure $ certStateAfterSubCerts & certDStateL . accountsL %~ applyDirectDeposits directDeposits
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxBody.hs (L185-186)
```haskell
    , dtbrDirectDeposits :: !DirectDeposits
    , dtbrAccountBalanceIntervals :: !(AccountBalanceIntervals era)
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxBody.hs (L206-207)
```haskell
    , dstbrDirectDeposits :: !DirectDeposits
    , dstbrAccountBalanceIntervals :: !(AccountBalanceIntervals era)
```

**File:** eras/mary/impl/src/Cardano/Ledger/Mary/UTxO.hs (L82-86)
```haskell
    consumedValue =
      sumUTxO (txInsFilter utxo (txBody ^. inputsTxBodyL))
        <> inject (refunds <> withdrawals)
    refunds = getTotalRefundsTxBody pp lookupStakingDeposit lookupDRepDeposit txBody
    withdrawals = fold . unWithdrawals $ txBody ^. withdrawalsTxBodyL
```
