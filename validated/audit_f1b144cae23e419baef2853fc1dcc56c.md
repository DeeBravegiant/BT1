### Title
Dijkstra Era `SUBLEDGERS` Rule Permits Multiple Subtransactions to Claim the Same Stake-Credential Deposit Refund - (File: eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubLedgers.hs)

---

### Summary

The Dijkstra era introduces nested subtransactions. The `SUBLEDGERS` rule folds over subtransactions sequentially and passes the updated `LedgerState` forward, but there is no batch-level guard preventing two subtransactions within the same top-level transaction from each including an `UnRegDepositTxCert` for the **same** stake credential. The developers themselves have recorded this gap as an open `xit` test with the comment `error "TODO: predicate failure not yet implemented"`. The analogous cross-subtransaction guard that exists for reward withdrawals (`validateBatchWithdrawals`) is absent for deposit refunds, confirming the missing check is not an oversight in the test but in the rule itself.

---

### Finding Description

**Vulnerability class:** Invalid state transition / funds-accounting bug (two-step deposit/claim pattern with no cross-claim guard).

**Analog mapping:** The original report describes a DEX `mint()` that reads the contract's current token balance to determine how many LP-NFTs to issue, without tying the claim to the depositor's identity. Two callers can therefore race to claim the same deposited assets. In the Dijkstra ledger, the analogous pattern is:

1. A stake credential is registered and a deposit is locked in the deposit pot.
2. A top-level transaction bundles two subtransactions (`subTx1`, `subTx2`), each containing `UnRegDepositTxCert stakingCred keyDeposit`.
3. The `SUBLEDGERS` rule applies them in sequence. The value-conservation check for each subtransaction independently counts the `UnRegDepositTxCert` refund as consumed value (via `dijkstraTotalRefundsTxCerts`, which sums certificate-declared refunds without verifying registration state).
4. No batch-level predicate prevents both subtransactions from successfully claiming the same deposit.

**Root cause — `SUBLEDGERS` sequential fold with no cross-subtransaction deposit guard:**

```haskell
-- eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubLedgers.hs
dijkstraSubLedgersTransition = do
  TRC (env, ledgerState, subTxs) <- judgmentContext
  foldM
    ( \ls subTx ->
        trans @(EraRule "SUBLEDGER" era) $ TRC (env, ls, subTx)
    )
    ledgerState
    subTxs
``` [1](#0-0) 

Each subtransaction is validated in isolation. The refund computation used in the value-conservation check is:

```haskell
dijkstraTotalRefundsTxCerts = foldMap' $ \case
  UnRegDepositTxCert _ deposit -> deposit
  UnRegDRepTxCert _ deposit -> deposit
  _ -> zero
``` [2](#0-1) 

This sums the deposit amounts declared in the certificates **without checking whether the credential is still registered**. Contrast this with the batch-level withdrawal guard that does exist:

```haskell
validateBatchWithdrawals accounts tx =
  let allWithdrawals =
        Map.unionsWith (<>) $
          unWithdrawals (tx ^. bodyTxL . withdrawalsTxBodyL)
            : [ unWithdrawals $ subTx ^. bodyTxL . withdrawalsTxBodyL
              | subTx <- OMap.elems $ tx ^. bodyTxL . subTransactionsTxBodyL
              ]
``` [3](#0-2) 

No equivalent `validateBatchDepositRefunds` function exists.

**Developer acknowledgement

### Citations

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubLedgers.hs (L128-135)
```haskell
dijkstraSubLedgersTransition = do
  TRC (env, ledgerState, subTxs) <- judgmentContext
  foldM
    ( \ls subTx ->
        trans @(EraRule "SUBLEDGER" era) $ TRC (env, ls, subTx)
    )
    ledgerState
    subTxs
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
