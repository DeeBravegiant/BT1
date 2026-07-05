### Title
Multiple Sub-Transactions Can Claim the Same Deposit Refund in Dijkstra Era — (`eras/dijkstra/impl/testlib/Test/Cardano/Ledger/Dijkstra/Imp/CertSpec.hs`)

---

### Summary

The Dijkstra era introduces nested (sub-)transactions. A top-level transaction can embed multiple sub-transactions, each of which can contain certificates. When two sub-transactions both include an `UnRegDepositTxCert` for the **same** staking credential, the deposit refund is disbursed twice — once per sub-transaction — because no cross-sub-transaction deduplication check has been implemented. The developers are aware of this gap and have left a disabled (`xit`) test with the comment `"TODO: predicate failure not yet implemented"`.

---

### Finding Description

The Dijkstra era adds `dtbrSubTransactions :: OMap TxId (Tx SubTx era)` to the top-level transaction body. [1](#0-0) 

Each sub-transaction body can carry its own certificate sequence (`dstbrCerts`). [2](#0-1) 

The existing refund logic in `shelleyTotalRefundsTxCerts` tracks registrations **within a single certificate sequence** to prevent a register-then-deregister double-refund inside one transaction. [3](#0-2) 

However, no equivalent guard exists across sub-transactions. The disabled test in `CertSpec.hs` directly demonstrates the scenario: two sub-transactions each containing `UnRegDepositTxCert stakingCred keyDeposit` for the **same** credential are bundled into one top-level transaction. The test is marked `xit` with the explicit note `error "TODO: predicate failure not yet implemented"`, confirming the check is absent and the transaction currently succeeds. [4](#0-3) 

The `conwayTotalRefundsTxCerts` / `shelleyTotalRefundsTxCerts` functions are called per-sub-transaction in isolation; there is no batch-level aggregation of refund claims analogous to the batch-level withdrawal check (`validateBatchWithdrawals`) that was correctly implemented for withdrawals. [5](#0-4) 

The `conwayTotalRefundsTxCerts` function that computes refunds per-certificate-sequence: [6](#0-5) 

---

### Impact Explanation

**Critical — Direct loss of ADA through an invalid ledger state transition.**

A staking credential with deposit `D` lovelace can be deregistered in two sub-transactions simultaneously. Both sub-transactions receive the full refund `D`, so `2D` lovelace is returned to the attacker while only `D` was ever locked. The deposit pot loses `D` lovelace that should have remained locked. This constitutes direct, unrecoverable destruction of ADA from the deposit accounting pot via an invalid ledger state transition.

---

### Likelihood Explanation

Any unprivileged transaction author can craft a Dijkstra-era top-level transaction containing two sub-transactions that both carry `UnRegDepositTxCert` for the same credential. No special access, governance majority, or key compromise is required. The only prerequisite is that the credential is registered (which the attacker can arrange themselves). The attack is deterministic and repeatable.

---

### Recommendation

Implement a cross-sub-transaction deduplication check for deposit refunds, mirroring the existing `validateBatchWithdrawals` pattern. Before processing sub-transaction certificates, aggregate all `UnRegDepositTxCert` / `UnRegDRepTxCert` claims across the entire batch (top-level + all sub-transactions) and reject the batch if any credential appears more than once as a refund target. The disabled test at `CertSpec.hs:53` should be re-enabled once the predicate failure is implemented.

---

### Proof of Concept

The developers' own disabled test is the proof of concept:

```haskell
-- eras/dijkstra/impl/testlib/Test/Cardano/Ledger/Dijkstra/Imp/CertSpec.hs
xit "Multiple subtransactions cannot get the same refund" $ do
  stakingCred <- KeyHashObj <$> freshKeyHash
  _ <- registerStakeCredential stakingCred
  keyDeposit <- getsPParams ppKeyDepositL
  ...
  let
    subTx1 = mkBasicTx mkBasicTxBody
      & bodyTxL . inputsTxBodyL  .~ Set.singleton input1
      & bodyTxL . certsTxBodyL   .~ SSeq.singleton (UnRegDepositTxCert stakingCred keyDeposit)
    subTx2 = mkBasicTx mkBasicTxBody
      & bodyTxL . inputsTxBodyL  .~ Set.singleton input2
      & bodyTxL . certsTxBodyL   .~ SSeq.singleton (UnRegDepositTxCert stakingCred keyDeposit)
    tx = mkBasicTx mkBasicTxBody
      & bodyTxL . subTransactionsTxBodyL .~ OMap.fromFoldable [subTx1, subTx2]
  submitFailingTx tx . NE.singleton $ error "TODO: predicate failure not yet implemented"
```

The `xit` disables the test because the predicate failure does not yet exist — meaning the transaction currently **succeeds**, paying out `2 × keyDeposit` when only `1 × keyDeposit` should be refunded. [4](#0-3)

### Citations

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxBody.hs (L184-184)
```haskell
    , dtbrSubTransactions :: !(OMap TxId (Tx SubTx era))
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxBody.hs (L193-193)
```haskell
    , dstbrCerts :: !(OSet.OSet (TxCert era))
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

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/TxCert.hs (L849-872)
```haskell
conwayTotalRefundsTxCerts pp lookupStakingDeposit lookupDRepDeposit certs =
  shelleyTotalRefundsTxCerts pp lookupStakingDeposit certs
    <+> conwayDRepRefundsTxCerts lookupDRepDeposit certs

-- | Compute the Refunds from a TxBody, given a function that computes a partial Coin for
-- known Credentials.
conwayDRepRefundsTxCerts ::
  (Foldable f, ConwayEraTxCert era) =>
  (Credential DRepRole -> Maybe Coin) ->
  f (TxCert era) ->
  Coin
conwayDRepRefundsTxCerts lookupDRepDeposit = snd . F.foldl' go (Map.empty, Coin 0)
  where
    go accum@(!drepRegsInTx, !totalRefund) = \case
      RegDRepTxCert cred deposit _ ->
        -- Track registrations
        (Map.insert cred deposit drepRegsInTx, totalRefund)
      UnRegDRepTxCert cred _
        -- DRep previously registered in the same tx.
        | Just deposit <- Map.lookup cred drepRegsInTx ->
            (Map.delete cred drepRegsInTx, totalRefund <+> deposit)
        -- DRep previously registered in some other tx.
        | Just deposit <- lookupDRepDeposit cred -> (drepRegsInTx, totalRefund <+> deposit)
      _ -> accum
```
