### Title
Multiple Sub-Transactions Can Claim the Same Deposit Refund, Creating ADA from Nothing — (`eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs`)

---

### Summary

In the Dijkstra era's nested-transaction system, `getConsumedDijkstraValue` aggregates deposit refunds from the top-level transaction body and every sub-transaction body by simple summation, using the same pre-state `lookupStakingDeposit` function for all of them. Two sub-transactions that each carry an `UnRegDepositTxCert` for the **same** staking credential will each contribute the full `keyDeposit` to the consumed-value total, inflating it by one extra `keyDeposit`. The value-conservation check then requires the transaction's outputs to contain that extra `keyDeposit`, which is sourced from the deposit pot — but the deposit pot only holds one copy of that deposit. The net result is that `keyDeposit` ADA is created from nothing. The codebase itself acknowledges this gap: the test that should catch it is disabled with `xit` and a `TODO: predicate failure not yet implemented` comment.

---

### Finding Description

**Root cause — `getConsumedDijkstraValue`**

`getConsumedDijkstraValue` in `eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs` computes the total consumed value for a top-level transaction as the sum of the top-level body's consumed value and the consumed values of every sub-transaction:

```haskell
subTransactionsConsumedValue topTxBody =
  foldMap'
    (getConsumedValue pp lookupStakingDeposit lookupDRepDeposit utxo . view bodyTxL)
    (topTxBody ^. subTransactionsTxBodyL)
``` [1](#0-0) 

The `lookupStakingDeposit` closure is a pure lookup against the **original** ledger state (the `certState` captured before any sub-transaction is applied). If two sub-transactions both carry `UnRegDepositTxCert stakingCred keyDeposit`, then `lookupStakingDeposit stakingCred` returns `Just keyDeposit` for both, and the total consumed value includes `2 × keyDeposit` as refunds.

**Value-conservation check uses this inflated total**

`dijkstraUtxoTransition` calls `Shelley.validateValueNotConservedUTxO`, which dispatches to `getConsumedValue = getConsumedDijkstraValue` (via the `EraUTxO DijkstraEra` instance):

```haskell
{- consumed pp utxo₀ txb = produced pp certState txb -}
runTest $ Shelley.validateValueNotConservedUTxO pp originalUtxo certState txBody
``` [2](#0-1) [3](#0-2) 

For the check to pass, the produced side must also equal `2 × keyDeposit` in refunds. The transaction author satisfies this by routing the extra `keyDeposit` into a UTxO output. The ledger accepts the transaction, but the deposit pot only ever held one copy of the deposit.

**No batch-level deduplication for deposit refunds**

`validateBatchWithdrawals` correctly prevents double-spending of account balances across sub-transactions by summing all withdrawal amounts and comparing against the original balance:

```haskell
allWithdrawals =
  Map.unionsWith (<>) $
    unWithdrawals (tx ^. bodyTxL . withdrawalsTxBodyL)
      : [ unWithdrawals $ subTx ^. bodyTxL . withdrawalsTxBodyL
        | subTx <- OMap.elems $ tx ^. bodyTxL . subTransactionsTxBodyL
        ]
``` [4](#0-3) 

No equivalent guard exists for deposit refunds across sub-transactions.

**Codebase acknowledgement — disabled test with TODO**

The test suite explicitly marks this scenario as unprotected:

```haskell
xit "Multiple subtransactions cannot get the same refund" $ do
  ...
  submitFailingTx tx . NE.singleton $ error "TODO: predicate failure not yet implemented"
``` [5](#0-4) 

The `xit` disables the test entirely; the `error "TODO: predicate failure not yet implemented"` confirms the blocking check does not yet exist.

---

### Impact Explanation

**Critical — Direct creation of ADA through an invalid ledger state transition.**

The deposit pot (`utxosDeposited`) is decremented by the full refund amount computed by `getConsumedDijkstraValue`. When two sub-transactions each claim the same `keyDeposit`, the pot is decremented by `2 × keyDeposit` while only `1 × keyDeposit` was ever deposited for that credential. The extra `keyDeposit` appears in UTxO outputs without a corresponding source, violating the preservation-of-value invariant and inflating the total ADA supply.

---

### Likelihood Explanation

Any unprivileged transaction sender can trigger this:

1. Register a staking credential (paying `keyDeposit`).
2. Construct a top-level transaction containing two sub-transactions, each with `UnRegDepositTxCert stakingCred keyDeposit`.
3. Balance the top-level transaction body so that `produced = consumed` using the inflated consumed value.
4. Submit the transaction.

No special role, governance majority, or leaked key is required. The Dijkstra era is experimental/pre-mainnet, but the vulnerability is present in the production code path as written.

---

### Recommendation

Add a batch-level deduplication check for deposit refunds analogous to `validateBatchWithdrawals`. Before the value-conservation check, collect all credentials appearing in `UnRegDepositTxCert` (and `UnRegDRepTxCert`) across the top-level body and all sub-transaction bodies. Reject the transaction if any credential appears more than once. Alternatively, modify `getConsumedDijkstraValue` to deduplicate refund lookups across sub-transactions so that a credential's deposit is counted at most once regardless of how many sub-transactions claim it.

---

### Proof of Concept

```
1. Register stakingCred, paying keyDeposit into the deposit pot.
   Deposit pot: keyDeposit

2. Build:
     subTx1 body: { inputs = {utxo1}, outputs = {addr1 ← value1 + keyDeposit},
                    certs  = [UnRegDepositTxCert stakingCred keyDeposit] }
     subTx2 body: { inputs = {utxo2}, outputs = {addr2 ← value2 + keyDeposit},
                    certs  = [UnRegDepositTxCert stakingCred keyDeposit] }
     topTx  body: { inputs = {}, outputs = {},
                    subTransactions = [subTx1, subTx2] }

3. getConsumedDijkstraValue counts:
     consumed = value(utxo1) + value(utxo2) + 2 × keyDeposit   ← double-counted

4. validateValueNotConservedUTxO passes because:
     produced = value1 + keyDeposit + value2 + keyDeposit = consumed  ✓

5. Transaction accepted. UTxO outputs contain 2 × keyDeposit.
   Deposit pot decremented by 2 × keyDeposit, but only 1 × keyDeposit existed.

6. Net ADA created: keyDeposit (e.g., 2 ADA at current mainnet parameters).
```

### Citations

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs (L86-91)
```haskell
    txBodyConsumedValue :: forall m. TxBody m era -> Value era
    txBodyConsumedValue = getConsumedMaryValue pp lookupStakingDeposit lookupDRepDeposit utxo
    subTransactionsConsumedValue topTxBody =
      foldMap'
        (getConsumedValue pp lookupStakingDeposit lookupDRepDeposit utxo . view bodyTxL)
        (topTxBody ^. subTransactionsTxBodyL)
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs (L124-131)
```haskell
instance EraUTxO DijkstraEra where
  type ScriptsNeeded DijkstraEra = AlonzoScriptsNeeded DijkstraEra

  consumed = conwayConsumed

  getConsumedValue = getConsumedDijkstraValue

  getProducedValue = getProducedDijkstraValue
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Utxo.hs (L259-275)
```haskell
validateBatchWithdrawals accounts tx =
  let allWithdrawals =
        Map.unionsWith (<>) $
          unWithdrawals (tx ^. bodyTxL . withdrawalsTxBodyL)
            : [ unWithdrawals $ subTx ^. bodyTxL . withdrawalsTxBodyL
              | subTx <- OMap.elems $ tx ^. bodyTxL . subTransactionsTxBodyL
              ]
      badWithdrawals =
        Map.mapMaybeWithKey
          ( \acctAddr withdrawn ->
              let balance = getAccountBalance acctAddr
               in if withdrawn > balance
                    then Just Mismatch {mismatchSupplied = withdrawn, mismatchExpected = balance}
                    else Nothing
          )
          allWithdrawals
   in failureOnNonEmptyMap badWithdrawals WithdrawalsExceedAccountBalance
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Utxo.hs (L380-381)
```haskell
  {- consumed pp utxo₀ txb = produced pp certState txb -}
  runTest $ Shelley.validateValueNotConservedUTxO pp originalUtxo certState txBody
```

**File:** eras/dijkstra/impl/testlib/Test/Cardano/Ledger/Dijkstra/Imp/CertSpec.hs (L53-75)
```haskell
  xit "Multiple subtransactions cannot get the same refund" $ do
    stakingCred <- KeyHashObj <$> freshKeyHash
    _ <- registerStakeCredential stakingCred
    keyDeposit <- getsPParams ppKeyDepositL
    value1 <- arbitrary
    (_, addr1) <- freshKeyAddr
    input1 <- sendCoinTo addr1 value1
    value2 <- arbitrary
    (_, addr2) <- freshKeyAddr
    input2 <- sendCoinTo addr2 value2
    let
      subTx1 =
        mkBasicTx mkBasicTxBody
          & bodyTxL . inputsTxBodyL .~ Set.singleton input1
          & bodyTxL . certsTxBodyL .~ SSeq.singleton (UnRegDepositTxCert stakingCred keyDeposit)
      subTx2 =
        mkBasicTx mkBasicTxBody
          & bodyTxL . inputsTxBodyL .~ Set.singleton input2
          & bodyTxL . certsTxBodyL .~ SSeq.singleton (UnRegDepositTxCert stakingCred keyDeposit)
      tx =
        mkBasicTx mkBasicTxBody
          & bodyTxL . subTransactionsTxBodyL .~ OMap.fromFoldable [subTx1, subTx2]
    submitFailingTx tx . NE.singleton $ error "TODO: predicate failure not yet implemented"
```
