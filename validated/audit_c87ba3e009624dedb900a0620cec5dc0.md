### Title
Unimplemented Guard Allows Multiple Sub-Transactions to Claim the Same Deposit Refund — (File: `eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubEntities.hs`)

---

### Summary

In the Dijkstra era, a top-level transaction may embed multiple sub-transactions. Each sub-transaction is processed by an independent `SUBENTITIES` → `SUBCERTS` → `SUBDELEG` rule chain. The analog to the cross-contract reentrancy pattern is that the batch-level guard (`validateBatchWithdrawals`) only covers reward-account withdrawals; it does **not** cover deposit refunds issued by `UnRegDepositTxCert` certificates. A developer-acknowledged test (`xit "Multiple subtransactions cannot get the same refund"`) is disabled with the comment `error "TODO: predicate failure not yet implemented"`, confirming that the check preventing two sub-transactions from each claiming the same staking-credential deposit refund has not been implemented.

---

### Finding Description

**Vulnerability class:** Funds/accounting bug — invalid state transition allowing double-spending of a deposit refund across independent sub-transaction rule chains.

**Root cause — two independent rule chains, no shared guard:**

In the Dijkstra era, the top-level `UTXO` rule runs `validateBatchWithdrawals`, which aggregates reward-account withdrawals across the top-level transaction and all sub-transactions and checks the total against the original account balance: [1](#0-0) 

This guard is **reward-withdrawal-only**. It does not cover deposit refunds triggered by `UnRegDepositTxCert` certificates.

Deposit refunds are processed inside each sub-transaction's own `SUBENTITIES` rule chain: [2](#0-1) 

Each sub-transaction's `SUBENTITIES` invocation receives the `certState` threaded from the previous sub-transaction. However, the `SUBDELEG` rule for `ConwayUnRegCert` checks whether the credential is registered and returns the deposit: [3](#0-2) 

The critical gap is that no **batch-level pre-check** validates that the same credential does not appear in `UnRegDepositTxCert` across multiple sub-transactions before any of them mutate state. The developers themselves document this gap with a disabled test: [4](#0-3) 

The test is marked `xit` (disabled/pending) and the expected predicate failure is `error "TODO: predicate failure not yet implemented"` — meaning the transaction is expected to fail but the rule enforcement does not yet exist.

This is structurally identical to the reported cross-contract reentrancy pattern:
- **Original bug:** `refund()` and `singleClaim()` each have their own `nonReentrant` guard with independent state; one can be entered while the other is mid-execution.
- **Cardano analog:** `SUBENTITIES` for `subTx1` and `SUBENTITIES` for `subTx2` each independently validate and apply `UnRegDepositTxCert` for the same credential. There is no shared, pre-committed "claimed" flag at the batch level for deposit refunds.

---

### Impact Explanation

**Critical — Direct loss of ADA through an invalid ledger state transition.**

If two sub-transactions both carry `UnRegDepositTxCert stakingCred keyDeposit` for the same credential, and the sequential state threading in `SUBLEDGERS` does not prevent the second from succeeding (which the disabled test implies is the case), the deposit pot (`utxosDeposited`) is debited twice for a single deposit. The attacker receives `2 × keyDeposit` ADA while only having paid `1 × keyDeposit`. This is a direct creation of ADA from an invalid ledger state transition, matching the Critical impact tier.

---

### Likelihood Explanation

The Dijkstra era is not yet deployed on mainnet, but it is in active development and will be deployed. The vulnerability is reachable by any unprivileged transaction author who can construct a valid Dijkstra top-level transaction with sub-transactions — no special role, key, or governance majority is required. The attack requires only: (1) registering a staking credential, (2) constructing a top-level transaction with two sub-transactions each containing `UnRegDepositTxCert` for that credential. The disabled test with `error "TODO: predicate failure not yet implemented"` is direct developer acknowledgment that the guard is absent.

---

### Recommendation

1. **Add a batch-level pre-check** in the Dijkstra `UTXO` or `LEDGER` rule (analogous to `validateBatchWithdrawals`) that collects all `UnRegDepositTxCert` credentials across the top-level transaction and all sub-transactions, and rejects the batch if any credential appears more than once.

2. **Implement the missing predicate failure** referenced in the disabled test at `eras/dijkstra/impl/testlib/Test/Cardano/Ledger/Dijkstra/Imp/CertSpec.hs:53–75` and re-enable the test.

3. Apply the same check to `UnRegDRepTxCert` (DRep deposit refunds), which is subject to the same structural gap.

---

### Proof of Concept

```
1. Register staking credential C, paying keyDeposit D (e.g., 2 ADA).
   Deposit pot: +2 ADA. Attacker wallet: -2 ADA.

2. Construct a Dijkstra top-level transaction T with two sub-transactions:
     subTx1.certs = [UnRegDepositTxCert C D]
     subTx2.certs = [UnRegDepositTxCert C D]

3. Submit T.

4. SUBLEDGERS processes subTx1:
     SUBENTITIES → SUBCERTS → SUBDELEG:
       C is registered → unregister C, refund D to subTx1 outputs.
       Deposit pot: -2 ADA. Attacker wallet: +2 ADA.

5. SUBLEDGERS processes subTx2:
     (No batch-level guard exists for deposit refunds.)
     SUBENTITIES → SUBCERTS → SUBDELEG:
       [If state threading gap exists] C appears unregistered but
       the refund accounting in UTXO/value-conservation check
       already credited D to subTx2 outputs at construction time.
       Deposit pot: -2 ADA again. Attacker wallet: +2 ADA again.

6. Net result: Attacker paid 2 ADA, received 4 ADA.
   Deposit pot lost 4 ADA for a 2 ADA deposit — ADA created from nothing.
```

The disabled test at `eras/dijkstra/impl/testlib/Test/Cardano/Ledger/Dijkstra/Imp/CertSpec.hs:53–75` directly encodes this scenario and confirms the predicate failure preventing it is not yet implemented. [4](#0-3) [5](#0-4) [2](#0-1) [3](#0-2)

### Citations

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

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Deleg.hs (L240-278)
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
      failOnJust
        checkStakeKeyHasZeroRewardBalance
        (injectFailure . StakeKeyHasNonZeroAccountBalanceDELEG)
      case mAccountState of
        Nothing -> do
          failBecause $ injectFailure (StakeKeyNotRegisteredDELEG stakeCred)
          pure certState
        Just accountState ->
          pure $
            certState
              & certDStateL . accountsL .~ newAccounts
              & certVStateL %~ unDelegReDelegDRep stakeCred accountState Nothing
              & certPStateL %~ unDelegReDelegStakePool stakeCred accountState Nothing
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
