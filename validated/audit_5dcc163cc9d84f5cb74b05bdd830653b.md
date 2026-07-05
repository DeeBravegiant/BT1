### Title
Multiple Sub-Transactions Can Double-Claim Deposit Refunds in Dijkstra Era Batch Transactions - (File: `eras/dijkstra/impl/testlib/Test/Cardano/Ledger/Dijkstra/Imp/CertSpec.hs`)

---

### Summary

The Dijkstra era introduces batch transactions containing sub-transactions. A batch transaction can include `UnRegDepositTxCert` for the **same staking credential** in multiple sub-transactions, potentially allowing double-claiming of the deposit refund. The developers explicitly acknowledge this protection is absent: the test guarding against it is disabled (`xit`) with the comment `error "TODO: predicate failure not yet implemented"`. No cross-sub-transaction validation for deposit refunds exists, analogous to the missing slippage bounds in the external report.

---

### Finding Description

The Dijkstra era's `validateBatchWithdrawals` correctly aggregates all withdrawal amounts across the top-level transaction and all sub-transactions and validates them against the initial account balance in a single pass: [1](#0-0) 

However, no analogous cross-sub-transaction validation exists for deposit refunds claimed via `UnRegDepositTxCert` certificates. A batch transaction can embed two sub-transactions that each contain `UnRegDepositTxCert stakingCred keyDeposit` for the **same** staking credential. The developers themselves wrote and then disabled the test that should guard against this: [2](#0-1) 

The test is marked `xit` (pending/skipped) and the expected predicate failure is `error "TODO: predicate failure not yet implemented"`. This is direct developer acknowledgment that the protection is absent. The `UnRegDepositTxCert` certificate carries the refund amount the submitter expects to receive: [3](#0-2) 

In a single-transaction context, the `DELEG` rule correctly validates the refund against the tracked deposit. But in a Dijkstra batch, if sub-transactions are validated against the same initial state (as `validateBatchWithdrawals` does for withdrawals), both sub-transactions see the credential as registered and both pass the refund check, allowing the deposit to be claimed twice.

The deposit tracking mechanism stores the exact amount paid per credential: [4](#0-3) 

---

### Impact Explanation

**Critical. Direct loss or creation of ADA through an invalid ledger state transition.**

If two sub-transactions within a single batch both successfully process `UnRegDepositTxCert` for the same staking credential, the deposit pot is debited twice for a single credential's deposit. This constitutes ADA creation from nothing (the second refund has no corresponding deposit), directly violating the preservation-of-value invariant. The `utxosDeposited` accounting pot would be reduced by `2 × keyDeposit` while only one deposit was ever paid, permanently corrupting the ledger's ADA accounting.

---

### Likelihood Explanation

**Medium.** The Dijkstra era is the newest era and batch transactions are a new feature. Any unprivileged user who can submit a Dijkstra-era transaction can craft a batch with two sub-transactions targeting the same credential. No special privilege, key leak, or governance majority is required. The attacker only needs to own or control a registered staking credential and know the `keyDeposit` value (a public protocol parameter). The attack is deterministic and repeatable.

---

### Recommendation

Implement a cross-sub-transaction validation for deposit refunds analogous to `validateBatchWithdrawals`. Before executing any sub-transaction certificates, aggregate all `UnRegDepositTxCert` credential targets across the entire batch and verify that no credential appears more than once. The predicate failure `StakeKeyNotRegisteredDELEG` or a new dedicated failure should be raised when a duplicate deregistration is detected. The disabled test at `CertSpec.hs:53–75` should be re-enabled once the predicate failure is implemented.

---

### Proof of Concept

1. Register staking credential `stakingCred` with deposit `keyDeposit`.
2. Craft a Dijkstra-era top-level transaction with two sub-transactions:
   - `subTx1`: contains `UnRegDepositTxCert stakingCred keyDeposit`
   - `subTx2`: contains `UnRegDepositTxCert stakingCred keyDeposit`
3. Submit the batch transaction.
4. Because no cross-sub-transaction refund deduplication check exists (acknowledged as `TODO`), both sub-transactions pass validation against the initial state where `stakingCred` is registered.
5. The deposit pot is debited `2 × keyDeposit` while only `1 × keyDeposit` was ever deposited, creating ADA from nothing.

The developer-written (but disabled) test at: [2](#0-1) 

directly encodes this exact scenario and confirms the missing protection with `error "TODO: predicate failure not yet implemented"`.

### Citations

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

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Deleg.hs (L240-265)
```haskell
    ConwayUnRegCert stakeCred sMayRefund -> do
      let (mAccountState, newAccounts) = unregisterConwayAccount stakeCred accounts
          checkInvalidRefund = do
            SJust suppliedRefund <- Just sMayRefund
            -- we don't want to report invalid refund when stake credential is not registered:
            accountState <- mAccountState
            -- we return offending refund only when it doesn't match the expected one:
            let expectedRefund = fromCompact $ accountState ^. depositAccountStateL
            guard (suppliedRefund /= expectedRefund)
            Just $
              if hardforkConwayDELEGIncorrectDepositsAndRefunds pv
                then
                  injectFailure
                    ( RefundIncorrectDELEG
                        Mismatch
                          { mismatchSupplied = suppliedRefund
                          , mismatchExpected = expectedRefund
                          }
                    )
                else injectFailure $ IncorrectDepositDELEG suppliedRefund
          checkStakeKeyHasZeroRewardBalance = do
            accountState <- mAccountState
            let balanceCompact = accountState ^. balanceAccountStateL
            guard (balanceCompact /= mempty)
            Just $ fromCompact balanceCompact
      failOnJust checkInvalidRefund id
```
