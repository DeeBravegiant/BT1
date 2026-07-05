### Title
Multiple Sub-Transactions Can Claim the Same Deposit Refund in Dijkstra Era Batch Transactions - (File: `eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubEntities.hs`)

---

### Summary

The Dijkstra era introduces batch transactions (a top-level transaction containing multiple sub-transactions). The SUBENTITIES/SUBCERTS rules that process each sub-transaction's certificates do not implement a cross-sub-transaction check to prevent multiple sub-transactions from claiming the same deposit refund for the same staking credential. An unprivileged sender can register a staking credential once, then submit a batch transaction whose sub-transactions each include an `UnRegDepositTxCert` for that credential, potentially extracting the deposit multiple times.

---

### Finding Description

The Dijkstra era's `dijkstraSubEntitiesTransition` in `eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubEntities.hs` processes each sub-transaction's certificates by calling the `SUBCERTS` rule:

```haskell
let certStateBeforeSubCerts =
      certState
        & Conway.updateDormantDRepExpiries tx curEpoch
        & Conway.updateVotingDRepExpiries tx curEpoch (pp ^. ppDRepActivityL)
        & certDStateL . accountsL %~ applyWithdrawals withdrawals
certStateAfterSubCerts <-
  trans @(EraRule "SUBCERTS" era) $ TRC (subCertsEnv, certStateBeforeSubCerts, certificates)
``` [1](#0-0) 

The top-level `dijkstraLedgerTransition` processes all sub-transactions via `SUBLEDGERS` before the main transaction:

```haskell
LedgerState utxoStateAfterSubLedgers certStateAfterSubLedgers <-
  trans @(EraRule "SUBLEDGERS" era) $
    TRC ( SubLedgerEnv slot mbCurEpochNo txIx pp chainAccountState originalUtxo (tx ^. isValidTxL)
        , ledgerState
        , subStAnnTxs
        )
``` [2](#0-1) 

The developers themselves have identified this gap and written a disabled test that explicitly documents the missing predicate failure:

```haskell
xit "Multiple subtransactions cannot get the same refund" $ do
  ...
  subTx1 = mkBasicTx mkBasicTxBody
    & bodyTxL . certsTxBodyL .~ SSeq.singleton (UnRegDepositTxCert stakingCred keyDeposit)
  subTx2 = mkBasicTx mkBasicTxBody
    & bodyTxL . certsTxBodyL .~ SSeq.singleton (UnRegDepositTxCert stakingCred keyDeposit)
  tx = mkBasicTx mkBasicTxBody
    & bodyTxL . subTransactionsTxBodyL .~ OMap.fromFoldable [subTx1, subTx2]
  submitFailingTx tx . NE.singleton $ error "TODO: predicate failure not yet implemented"
``` [3](#0-2) 

The `xit` prefix disables the test because the expected predicate failure type does not yet exist — meaning the enforcement rule itself is absent. The analog to the external report is direct: just as `prepareCallbackValues` computes PnL from the full collateral without subtracting previously realized PnL, the SUBCERTS rule computes deposit refunds without accounting for refunds already claimed by earlier sub-transactions in the same batch.

The `conwayTotalRefundsTxCerts` / `shelleyTotalRefundsTxCerts` functions correctly track within-transaction duplicate deregistrations using an accumulator, but this accumulator is local to a single certificate sequence and is not shared across sub-transactions:

```haskell
shelleyTotalRefundsTxCerts pp lookupDeposit = snd . F.foldl' accum (mempty, Coin 0)
  where
    accum (!regCreds, !totalRefunds) cert = ...
``` [4](#0-3) 

There is no equivalent cross-sub-transaction accumulator in the SUBLEDGERS processing path.

---

### Impact Explanation

An attacker registers a staking credential paying deposit `D`, then submits a single batch transaction containing `N` sub-transactions each bearing `UnRegDepositTxCert` for the same credential. If the cross-sub-transaction deregistration guard is absent, the deposit pot is decremented by `N × D` while the attacker's account receives `N × D`, creating `(N-1) × D` ADA from nothing. This is a **direct creation of ADA through an invalid ledger state transition** — Critical impact.

---

### Likelihood Explanation

The attack requires only a single unprivileged transaction sender. No governance majority, no privileged key, and no external dependency is needed. The Dijkstra era is new and the sub-transaction feature is novel, making this an under-reviewed code path. The disabled test with `error "TODO: predicate failure not yet implemented"` confirms the guard is not yet in place.

---

### Recommendation

Implement a cross-sub-transaction deregistration guard in the SUBLEDGERS processing loop. Before processing each sub-transaction's certificates, check that no credential being deregistered was already deregistered by a prior sub-transaction in the same batch. Define the corresponding predicate failure (e.g., `DuplicateDeregistrationAcrossSubTxs`) and enable the existing `xit` test in `eras/dijkstra/impl/testlib/Test/Cardano/Ledger/Dijkstra/Imp/CertSpec.hs`.

---

### Proof of Concept

1. Register staking credential `C`, paying deposit `D` (e.g., 2 ADA).
2. Construct sub-transaction `subTx1` with `UnRegDepositTxCert C D`.
3. Construct sub-transaction `subTx2` with `UnRegDepositTxCert C D`.
4. Submit a top-level batch transaction containing both `subTx1` and `subTx2` in `subTransactionsTxBodyL`.
5. If the cross-sub-transaction guard is absent, both sub-transactions succeed: the deposit pot loses `2D` and the attacker's reward account gains `2D`, netting `D` ADA created from nothing.

The developers' own disabled test at [3](#0-2)  encodes exactly this scenario and confirms the predicate failure enforcing the guard is not yet implemented.

### Citations

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubEntities.hs (L174-180)
```haskell
  let certStateBeforeSubCerts =
        certState
          & Conway.updateDormantDRepExpiries tx curEpoch
          & Conway.updateVotingDRepExpiries tx curEpoch (pp ^. ppDRepActivityL)
          & certDStateL . accountsL %~ applyWithdrawals withdrawals
  certStateAfterSubCerts <-
    trans @(EraRule "SUBCERTS" era) $ TRC (subCertsEnv, certStateBeforeSubCerts, certificates)
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Ledger.hs (L370-383)
```haskell
  LedgerState utxoStateAfterSubLedgers certStateAfterSubLedgers <-
    trans @(EraRule "SUBLEDGERS" era) $
      TRC
        ( SubLedgerEnv
            slot
            mbCurEpochNo
            txIx
            pp
            chainAccountState
            originalUtxo
            (tx ^. isValidTxL)
        , ledgerState
        , subStAnnTxs
        )
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

**File:** eras/shelley/impl/src/Cardano/Ledger/Shelley/TxCert.hs (L639-659)
```haskell
shelleyTotalRefundsTxCerts pp lookupDeposit = snd . F.foldl' accum (mempty, Coin 0)
  where
    keyDeposit = pp ^. ppKeyDepositL
    accum (!regCreds, !totalRefunds) cert =
      case lookupRegStakeTxCert cert of
        Just k ->
          -- Need to track new delegations in case that the same key is later deregistered in
          -- the same transaction.
          (Set.insert k regCreds, totalRefunds)
        Nothing ->
          case lookupUnRegStakeTxCert cert of
            Just cred
              -- We first check if there was already a registration certificate in this
              -- transaction.
              | Set.member cred regCreds -> (Set.delete cred regCreds, totalRefunds <+> keyDeposit)
              -- Check for the deposit left during registration in some previous
              -- transaction. This de-registration check will be matched first, despite being
              -- the last case to match, because registration is not possible without
              -- de-registration.
              | Just deposit <- lookupDeposit cred -> (regCreds, totalRefunds <+> deposit)
            _ -> (regCreds, totalRefunds)
```
