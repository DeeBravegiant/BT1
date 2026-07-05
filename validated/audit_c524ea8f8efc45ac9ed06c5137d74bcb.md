### Title
Multiple Subtransactions Can Claim the Same Deposit Refund, Inflating Consumed Value — (`eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs`)

---

### Summary

In the Dijkstra era, `getConsumedDijkstraValue` aggregates consumed values from all subtransactions independently, each using the same pre-transaction `lookupStakingDeposit` function. If two or more subtransactions each contain an `UnRegDepositTxCert` for the same staking credential, the deposit refund is counted once per subtransaction, inflating the total consumed value by `N × keyDeposit` while the deposit pot holds only `1 × keyDeposit`. No batch-level deduplication check for deposit refunds exists, analogous to the `validateBatchWithdrawals` guard that does exist for withdrawals. The developers have explicitly acknowledged this gap with a disabled test marked `TODO: predicate failure not yet implemented`.

---

### Finding Description

**Root cause — `getConsumedDijkstraValue`:**

```haskell
getConsumedDijkstraValue pp lookupStakingDeposit lookupDRepDeposit utxo txBody =
  withBothTxLevels txBody
    ( \topTxBody ->
        txBodyConsumedValue topTxBody <> subTransactionsConsumedValue topTxBody
    )
    txBodyConsumedValue
  where
    txBodyConsumedValue = getConsumedMaryValue pp lookupStakingDeposit lookupDRepDeposit utxo
    subTransactionsConsumedValue topTxBody =
      foldMap'
        (getConsumedValue pp lookupStakingDeposit lookupDRepDeposit utxo . view bodyTxL)
        (topTxBody ^. subTransactionsTxBodyL)
``` [1](#0-0) 

`subTransactionsConsumedValue` calls `getConsumedValue` for every subtransaction using the **same** `lookupStakingDeposit` closure, which reflects the ledger state **before** the batch is applied. For each subtransaction that contains `UnRegDepositTxCert stakingCred deposit`, `dijkstraTotalRefundsTxCerts` adds `deposit` to the consumed value:

```haskell
dijkstraTotalRefundsTxCerts = foldMap' $ \case
  UnRegDepositTxCert _ deposit -> deposit
  UnRegDRepTxCert _ deposit -> deposit
  _ -> zero
``` [2](#0-1) 

Because this function reads the deposit amount directly from the certificate (not from the ledger state), and because `getConsumedDijkstraValue` sums across all subtransactions without deduplication, N subtransactions each containing `UnRegDepositTxCert stakingCred keyDeposit` produce a total consumed-value contribution of `N × keyDeposit`.

**Missing batch-level guard:**

A `validateBatchWithdrawals` function exists that aggregates all withdrawals across the batch and checks them against the account balance:

```haskell
validateBatchWithdrawals accounts tx =
  let allWithdrawals =
        Map.unionsWith (<>) $
          unWithdrawals (tx ^. bodyTxL . withdrawalsTxBodyL)
            : [ unWithdrawals $ subTx ^. bodyTxL . withdrawalsTxBodyL
              | subTx <- OMap.elems $ tx ^. bodyTxL . subTransactionsTxBodyL
              ]
``` [3](#0-2) 

No equivalent `validateBatchRefunds` function exists for deposit refunds.

**Developer acknowledgement — disabled test:**

```haskell
xit "Multiple subtransactions cannot get the same refund" $ do
  ...
  let
    subTx1 = mkBasicTx mkBasicTxBody
      & bodyTxL . certsTxBodyL .~ SSeq.singleton (UnRegDepositTxCert stakingCred keyDeposit)
    subTx2 = mkBasicTx mkBasicTxBody
      & bodyTxL . certsTxBodyL .~ SSeq.singleton (UnRegDepositTxCert stakingCred keyDeposit)
    tx = mkBasicTx mkBasicTxBody
      & bodyTxL . subTransactionsTxBodyL .~ OMap.fromFoldable [subTx1, subTx2]
  submitFailingTx tx . NE.singleton $ error "TODO: predicate failure not yet implemented"
``` [4](#0-3) 

The `xit` prefix disables the test. The expected failure is `error "TODO: predicate failure not yet implemented"`, confirming that the production-code guard against this scenario **does not yet exist**.

---

### Impact Explanation

The preservation-of-value invariant (`consumed == produced`) is the ledger's fundamental accounting property. If `getConsumedDijkstraValue` returns `N × keyDeposit` for a single credential's deposit, the UTXO rule permits the transaction to produce `N × keyDeposit` in outputs. The deposit pot, however, holds only `1 × keyDeposit` for that credential. If the CERT rule does not independently reject the second (and subsequent) `UnRegDepositTxCert` for the already-deregistered credential — which is precisely the missing predicate failure the `xit` test acknowledges — the transaction succeeds and `(N−1) × keyDeposit` of ADA is created from nothing, directly reducing the deposit pot below its true obligation. This is a **direct creation of ADA through an invalid ledger state transition**.

Even if the CERT rule does catch the double-deregistration and rejects the transaction, the consumed-value calculation is still incorrect, meaning the preservation-of-value check is unreliable for batched transactions, constituting a deterministic disagreement risk between nodes that evaluate the UTXO rule before vs. after the CERT rule.

---

### Likelihood Explanation

The Dijkstra era introduces subtransactions as a new primitive. The attack requires only:
1. Registering a stake credential (paying `keyDeposit`, currently 2 ADA on mainnet).
2. Constructing a top-level transaction with two or more subtransactions each containing `UnRegDepositTxCert` for the same credential.
3. Submitting the transaction.

No privileged access, governance majority, or external dependency is required. The attacker controls the transaction structure entirely. The `xit` test confirms the protection is absent from production code.

---

### Recommendation

1. **Add a batch-level refund deduplication check** analogous to `validateBatchWithdrawals`: before accepting a batch, collect all `UnRegDepositTxCert` and `UnRegDRepTxCert` credentials across all subtransactions, verify each credential appears at most once across the entire batch, and fail with a new predicate failure (e.g., `DuplicateRefundInBatch`) if a credential appears more than once.

2. **Fix `getConsumedDijkstraValue`** to track which credentials have already contributed a refund within the batch, so the consumed-value sum is not inflated by duplicate deregistrations.

3. **Enable and complete the `xit` test** in `Test.Cardano.Ledger.Dijkstra.Imp.CertSpec` once the predicate failure is implemented.

---

### Proof of Concept

```haskell
-- Attacker registers one staking credential, paying keyDeposit once.
stakingCred <- KeyHashObj <$> freshKeyHash
_ <- registerStakeCredential stakingCred
keyDeposit <- getsPParams ppKeyDepositL   -- e.g. Coin 2_000_000

-- Two independent UTxO inputs to satisfy each subtransaction's balance.
input1 <- sendCoinTo addr1 someValue
input2 <- sendCoinTo addr2 someValue

let
  -- Both subtransactions claim the same deposit refund.
  subTx1 = mkBasicTx mkBasicTxBody
    & bodyTxL . inputsTxBodyL  .~ Set.singleton input1
    & bodyTxL . certsTxBodyL   .~ SSeq.singleton (UnRegDepositTxCert stakingCred keyDeposit)
    -- output: keyDeposit to attacker
  subTx2 = mkBasicTx mkBasicTxBody
    & bodyTxL . inputsTxBodyL  .~ Set.singleton input2
    & bodyTxL . certsTxBodyL   .~ SSeq.singleton (UnRegDepositTxCert stakingCred keyDeposit)
    -- output: keyDeposit to attacker (second claim of the same deposit)
  tx = mkBasicTx mkBasicTxBody
    & bodyTxL . subTransactionsTxBodyL .~ OMap.fromFoldable [subTx1, subTx2]

-- getConsumedDijkstraValue counts 2 × keyDeposit in refunds.
-- The deposit pot holds only 1 × keyDeposit.
-- If the missing predicate failure is absent, the transaction succeeds
-- and the attacker receives 2 × keyDeposit while only 1 × keyDeposit
-- is legitimately owed, creating keyDeposit ADA from nothing.
submitTx_ tx
```

This directly mirrors the external report's pattern: a function that increments an accumulator (`unclaimedFlux += amount` / consumed-value `+= deposit`) is called once per subtransaction/`poke()` call without an idempotency guard, allowing unbounded inflation of the value being claimed.

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

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxCert.hs (L229-238)
```haskell
dijkstraTotalRefundsTxCerts ::
  ( Foldable f
  , ConwayEraTxCert era
  ) =>
  f (TxCert era) ->
  Coin
dijkstraTotalRefundsTxCerts = foldMap' $ \case
  UnRegDepositTxCert _ deposit -> deposit
  UnRegDRepTxCert _ deposit -> deposit
  _ -> zero
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Utxo.hs (L249-275)
```haskell
-- | For each account, the total withdrawals across the entire batch should not exceed the original account balance.
-- Unregistered accounts are treated as having 0 balance.
validateBatchWithdrawals ::
  ( EraTx era
  , EraAccounts era
  , DijkstraEraTxBody era
  ) =>
  Accounts era ->
  Tx TopTx era ->
  Test (DijkstraUtxoPredFailure era)
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
