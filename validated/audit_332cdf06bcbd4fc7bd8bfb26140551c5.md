I need to trace the full call chain and verify whether `dijkstraSubTxProducedValue` (and the batch-level conservation check) accounts for `dstbrDirectDeposits`.

## Tracing the Call Chain

**Step 1 — How direct deposits are applied in sub-txs**

`SUBLEDGERS` → `SUBLEDGER` → `SUBENTITIES` (`dijkstraSubEntitiesTransition`): [1](#0-0) 

The only guard before `applyDirectDeposits` is `directDepositsMissingAccounts` — it checks that target accounts exist, nothing more. [2](#0-1) 

**Step 2 — What `dijkstraSubTxProducedValue` computes** [3](#0-2) 

Produced = outputs + deposits + treasury donation + burned multi-assets. **`directDeposits` is absent.**

**Step 3 — What the sub-tx consumed value computes**

For sub-txs, `getConsumedDijkstraValue` dispatches to `txBodyConsumedValue` = `getConsumedMaryValue`: [4](#0-3) 

Consumed = UTxO inputs + refunds + withdrawals. **`directDeposits` is absent.**

**Step 4 — The batch-level conservation check**

The only `validateValueNotConservedUTxO` call is in `dijkstraUtxoTransition` (top-level UTXO rule): [5](#0-4) 

This calls `consumed` → `getConsumedDijkstraValue` and `produced` → `getProducedDijkstraValue`. For sub-txs, the batch aggregation is: [6](#0-5) 

Both sides aggregate sub-tx consumed/produced values — neither path includes `directDeposits`.

**Step 5 — `SUBUTXO` has no conservation check** [7](#0-6) 

`SUBUTXO` calls `updateUTxOStateNoFees` with no `validateValueNotConservedUTxO`. The mapping `ValueNotConservedUTxO → error "Impossible"` in `dijkstraUtxoToDijkstraSubUtxoPredFailure` confirms this is intentional. [8](#0-7) 

---

## Verdict

### Title
Sub-Tx `directDeposits` Absent from Value Conservation Check, Enabling ADA Creation — (`eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs`)

### Summary
`dijkstraSubTxProducedValue` omits `dstbrDirectDeposits` from the produced-value computation. The batch-level `validateValueNotConservedUTxO` therefore does not require sub-tx inputs to cover the direct-deposit amount. `applyDirectDeposits` unconditionally credits coin to account balances, creating ADA with no corresponding UTxO debit.

### Finding Description
The Dijkstra era processes a batch as follows:

1. `SUBLEDGERS` iterates sub-txs, each going through `SUBLEDGER`.
2. `SUBLEDGER` calls `SUBENTITIES` first, which applies `applyDirectDeposits` to the `CertState`.
3. `SUBLEDGER` then calls `SUBUTXOW` → `SUBUTXO`, which updates the UTxO via `updateUTxOStateNoFees` — **no conservation check**.
4. After all sub-txs, the top-level `UTXO` rule runs `validateValueNotConservedUTxO` on the top-level `TxBody`.

The batch conservation check aggregates:

- **Consumed** (all levels): UTxO inputs + refunds + withdrawals — no direct deposits.
- **Produced** (all levels): UTxO outputs + fees + cert deposits + treasury donations — no direct deposits.

`dijkstraSubTxProducedValue` is the sub-tx produced-value function and it does not include `txBody ^. directDepositsTxBodyL`. `getConsumedMaryValue` (used for sub-tx consumed value) also does not include direct deposits. The only guard in `SUBENTITIES` is `directDepositsMissingAccounts`, which only verifies that target accounts are registered.

**Concrete imbalance:** A sub-tx with `inputs = outputs` (UTxO-balanced) and `directDeposits = {addr: N}` satisfies `consumed == produced` at the batch level, while `applyDirectDeposits` credits N lovelace to `addr`. Total ADA in the system (UTxO + accounts) increases by N.

### Impact Explanation
This is a **Critical** impact: direct creation of ADA through an invalid ledger state transition. Any amount of lovelace can be minted into any registered account per batch submission, with no upper bound other than the `Coin` type limit.

### Likelihood Explanation
The precondition is only a registered account, which any user can create via a standard stake registration certificate. The exploit is deterministic, requires no timing, no privileged access, and no external coordination. Any unprivileged transaction author can trigger it.

### Recommendation
Include the total direct-deposit amount in the **produced** side of the value conservation check. For sub-txs, `dijkstraSubTxProducedValue` should be:

```haskell
dijkstraSubTxProducedValue pp isRegPoolId txBody =
  sumAllValue (txBody ^. outputsTxBodyL)
    <> inject ( getTotalDepositsTxBody pp isRegPoolId txBody
             <> txBody ^. treasuryDonationTxBodyL
             <> fold (unDirectDeposits (txBody ^. directDepositsTxBodyL))  -- ADD
              )
    <> burnedMultiAssets txBody
```

The same fix is needed for

### Citations

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

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs (L88-106)
```haskell
    subTransactionsConsumedValue topTxBody =
      foldMap'
        (getConsumedValue pp lookupStakingDeposit lookupDRepDeposit utxo . view bodyTxL)
        (topTxBody ^. subTransactionsTxBodyL)

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

**File:** eras/mary/impl/src/Cardano/Ledger/Mary/UTxO.hs (L77-87)
```haskell
getConsumedMaryValue pp lookupStakingDeposit lookupDRepDeposit utxo txBody =
  consumedValue <> MaryValue mempty mintedMultiAsset
  where
    mintedMultiAsset = filterMultiAsset (\_ _ -> (> 0)) $ txBody ^. mintTxBodyL
    {- balance (txins tx ◁ u) + wbalance (txwdrls tx) + keyRefunds pp tx -}
    consumedValue =
      sumUTxO (txInsFilter utxo (txBody ^. inputsTxBodyL))
        <> inject (refunds <> withdrawals)
    refunds = getTotalRefundsTxBody pp lookupStakingDeposit lookupDRepDeposit txBody
    withdrawals = fold . unWithdrawals $ txBody ^. withdrawalsTxBodyL

```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Utxo.hs (L380-381)
```haskell
  {- consumed pp utxo₀ txb = produced pp certState txb -}
  runTest $ Shelley.validateValueNotConservedUTxO pp originalUtxo certState txBody
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubUtxo.hs (L265-278)
```haskell
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

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubUtxo.hs (L333-333)
```haskell
  ValueNotConservedUTxO _ -> error "Impossible: `ValueNotConservedUTxO` for SUBUTXO"
```
