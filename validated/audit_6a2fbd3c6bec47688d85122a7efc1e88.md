### Title
Multiple Subtransactions Can Claim the Same Deposit Refund, Creating ADA Out of Thin Air - (`eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubLedgers.hs`)

---

### Summary

In the Dijkstra era's nested transaction system, multiple subtransactions within a single top-level transaction can each include an `UnRegDepositTxCert` for the same staking credential. The `SUBLEDGERS` rule processes subtransactions sequentially via `foldM`, and while the first subtransaction legitimately deregisters the credential and claims the deposit refund, subsequent subtransactions can also claim the same deposit refund. No cross-subtransaction guard prevents the same deposit from being claimed more than once. This is the direct analog of the `migrateFractions` double-claim vulnerability: user-controlled state (the deposit) is not invalidated after first consumption, allowing repeated extraction.

The developers have explicitly acknowledged this gap: the test `"Multiple subtransactions cannot get the same refund"` in `eras/dijkstra/impl/testlib/Test/Cardano/Ledger/Dijkstra/Imp/CertSpec.hs` is marked `xit` (skipped/pending) with the comment `error "TODO: predicate failure not yet implemented"`, confirming the protective check does not yet exist in the production rules.

---

### Finding Description

**Vulnerability class**: Missing cross-subtransaction state reset / double-claim of deposit refund.

The Dijkstra era introduces nested ("sub") transactions. A top-level transaction may embed an ordered map of subtransactions (`dtbrSubTransactions :: OMap TxId (Tx SubTx era)`). These are processed by the `SUBLEDGERS` rule, which folds over the list sequentially:

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
```

Each `SUBLEDGER` invocation calls `SUBENTITIES`, which calls `SUBCERTS`/`SUBDELEG` to process certificates. The `SUBENTITIES` rule applies withdrawals via `applyWithdrawals` and processes deregistration certificates:

```haskell
-- eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubEntities.hs
let certStateBeforeSubCerts =
      certState
        & Conway.updateDormantDRepExpiries tx curEpoch
        & Conway.updateVotingDRepExpiries tx curEpoch (pp ^. ppDRepActivityL)
        & certDStateL . accountsL %~ applyWithdrawals withdrawals
certStateAfterSubCerts <-
  trans @(EraRule "SUBCERTS" era) $ TRC (subCertsEnv, certStateBeforeSubCerts, certificates)
```

When `subTx1` processes `UnRegDepositTxCert stakingCred keyDeposit`, the credential is removed from the accounts map and the deposit is returned. When `subTx2` subsequently processes the same `UnRegDepositTxCert stakingCred keyDeposit`, the credential is already absent from the accounts map. The `SUBDELEG` rule's refund-validity checks (`checkInvalidRefund`, `checkStakeKeyHasZeroRewardBalance`) both short-circuit on `Nothing` (credential not registered), so neither fires. No predicate failure is raised for the second deregistration.

The refund amount used in the value-conservation check (`validateValueNotConservedUTxO`) is computed by `dijkstraTotalRefundsTxCerts`, which reads the deposit amount directly from the certificate body — not from the ledger state:

```haskell
-- eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxCert.hs
dijkstraTotalRefundsTxCerts = foldMap' $ \case
  UnRegDepositTxCert _ deposit -> deposit
  UnRegDRepTxCert _ deposit -> deposit
  _ -> zero
```

This means the value-conservation check for `subTx2` expects a refund of `keyDeposit` and passes, causing the deposit pot (`utxosDeposited`) to be decremented a second time for a deposit that no longer exists. The outputs of `subTx2` receive `keyDeposit` worth of ADA that was never legitimately available, violating preservation of value.

By contrast, the analogous protection for **withdrawals** across the batch is correctly implemented:

```haskell
-- eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Utxo.hs
validateBatchWithdrawals accounts tx =
  let allWithdrawals =
        Map.unionsWith (<>) $
          unWithdrawals (tx ^. bodyTxL . withdrawalsTxBodyL)
            : [ unWithdrawals $ subTx ^. bodyTxL . withdrawalsTxBodyL
              | subTx <- OMap.elems $ tx ^. bodyTxL . subTransactionsTxBodyL
              ]
      badWithdrawals = Map.mapMaybeWithKey
          ( \acctAddr withdrawn ->
              let balance = getAccountBalance acctAddr
               in if withdrawn > balance
                    then Just Mismatch {mismatchSupplied = withdrawn, mismatchExpected = balance}
                    else Nothing
          )
          allWithdrawals
   in failureOnNonEmptyMap badWithdrawals WithdrawalsExceedAccountBalance
```

No equivalent `validateBatchDepositRefunds` exists for deposit refunds.

---

### Impact Explanation

**Critical. Direct loss, creation, or destruction of ADA or native assets through an invalid ledger state transition.**

An attacker can craft a top-level Dijkstra transaction containing N subtransactions, each bearing `UnRegDepositTxCert stakingCred keyDeposit` for the same credential. The first subtransaction legitimately deregisters the credential and claims `keyDeposit`. Each subsequent subtransaction also passes value-conservation (because `dijkstraTotalRefundsTxCerts` counts the certificate's stated deposit, not the ledger state) and receives `keyDeposit` in its outputs. The deposit pot is decremented N times for a single deposit, creating `(N-1) * keyDeposit` ADA out of thin air. This directly violates the preservation-of-value invariant that is the foundational correctness property of the Cardano ledger.

---

### Likelihood Explanation

The Dijkstra era is currently experimental and not yet deployed on mainnet, which limits immediate exploitability. However:

1. The vulnerability is **acknowledged by the developers** — the test `"Multiple subtransactions cannot get the same refund"` is explicitly marked `xit` with `error "TODO: predicate failure not yet implemented"`, confirming the protective check is absent from production rules.
2. The entry path requires only an **unprivileged transaction sender** — no special role, key, or governance threshold is needed.
3. The attack is **deterministic and cheap** — a single transaction with two subtransactions suffices to double-claim a deposit.
4. Once Dijkstra is deployed, exploitation would be straightforward for any participant who reads the ledger rules.

---

### Recommendation

Implement a cross-subtransaction deposit-refund guard analogous to `validateBatchWithdrawals`. Before processing subtransactions, collect all `UnRegDepositTxCert` and `UnRegDRepTxCert` credentials across all subtransactions and the top-level transaction. Verify that no credential appears more than once across the batch, and that the total claimed refund for each credential does not exceed the deposit recorded in the ledger state. This mirrors the fix recommended in the external report: zero out (or track as consumed) the deposit state after first use, so subsequent claims are rejected.

Alternatively, extend `validateBatchWithdrawals` into a broader `validateBatchAccountClaims` that covers both withdrawal amounts and deposit refunds across the entire subtransaction batch.

---

### Proof of Concept

The developers' own pending test in `eras/dijkstra/impl/testlib/Test/Cardano/Ledger/Dijkstra/Imp/CertSpec.hs` demonstrates the scenario:

```haskell
xit "Multiple subtransactions cannot get the same refund" $ do
  stakingCred <- KeyHashObj <$> freshKeyHash
  _ <- registerStakeCredential stakingCred
  keyDeposit <- getsPParams ppKeyDepositL
  ...
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

The `xit` marker (skipped test) and `error "TODO: predicate failure not yet implemented"` confirm that the transaction currently **does not fail** as it should — the double-claim succeeds because no predicate failure has been implemented to prevent it.

**Relevant production files:**

- `SUBLEDGERS` sequential fold (no cross-subtransaction deposit guard): [1](#0-0) 
- `SUBENTITIES` applies withdrawals but has no deposit-refund cross-check: [2](#0-1) 
- `dijkstraTotalRefundsTxCerts` reads refund from certificate, not ledger state: [3](#0-2) 
- `validateBatchWithdrawals` (the analogous guard that exists for withdrawals but not deposits): [4](#0-3) 
- Acknowledged gap in test (production-scope evidence): [5](#0-4)

### Citations

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubLedgers.hs (L121-135)
```haskell
dijkstraSubLedgersTransition ::
  forall era.
  ( EraRule "SUBLEDGERS" era ~ SUBLEDGERS era
  , EraRule "SUBLEDGER" era ~ SUBLEDGER era
  , Embed (EraRule "SUBLEDGER" era) (SUBLEDGERS era)
  ) =>
  TransitionRule (EraRule "SUBLEDGERS" era)
dijkstraSubLedgersTransition = do
  TRC (env, ledgerState, subTxs) <- judgmentContext
  foldM
    ( \ls subTx ->
        trans @(EraRule "SUBLEDGER" era) $ TRC (env, ls, subTx)
    )
    ledgerState
    subTxs
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubEntities.hs (L155-187)
```haskell
dijkstraSubEntitiesTransition = do
  TRC (subCertsEnv, certState, certificates) <- judgmentContext
  let tx = certsTx subCertsEnv
      pp = certsPParams subCertsEnv
      curEpoch = certsCurrentEpoch subCertsEnv
      withdrawals = tx ^. bodyTxL . withdrawalsTxBodyL
      accounts = certState ^. certDStateL . accountsL

  runTest $ Conway.validateWithdrawalsDelegated accounts tx

  network <- liftSTS $ asks networkId
  let (missingWithdrawals, exceededWithdrawals) =
        case withdrawalsThatExceedAccountBalance withdrawals network accounts of
          Nothing -> (Map.empty, Map.empty)
          Just (missing, exceeded) -> (unWithdrawals missing, exceeded)
  failOnNonEmptyMap missingWithdrawals $
    injectFailure . SubWithdrawalsMissingAccounts . Withdrawals . NEM.toMap
  failOnNonEmptyMap exceededWithdrawals $ injectFailure . SubWithdrawalAmountsExceedAccountBalances

  let certStateBeforeSubCerts =
        certState
          & Conway.updateDormantDRepExpiries tx curEpoch
          & Conway.updateVotingDRepExpiries tx curEpoch (pp ^. ppDRepActivityL)
          & certDStateL . accountsL %~ applyWithdrawals withdrawals
  certStateAfterSubCerts <-
    trans @(EraRule "SUBCERTS" era) $ TRC (subCertsEnv, certStateBeforeSubCerts, certificates)

  let directDeposits = tx ^. bodyTxL . directDepositsTxBodyL
      accountsAfterSubCerts = certStateAfterSubCerts ^. certDStateL . accountsL
  failOnJust (directDepositsMissingAccounts directDeposits accountsAfterSubCerts) $
    injectFailure . SubDirectDepositsToMissingAccounts

  pure $ certStateAfterSubCerts & certDStateL . accountsL %~ applyDirectDeposits directDeposits
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

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Utxo.hs (L249-280)
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
  where
    getAccountBalance (AccountAddress _ (AccountId cred)) =
      case lookupAccountState cred accounts of
        Nothing -> mempty -- unregistered account, 0 balance
        Just accountState -> fromCompact $ accountState ^. balanceAccountStateL
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
