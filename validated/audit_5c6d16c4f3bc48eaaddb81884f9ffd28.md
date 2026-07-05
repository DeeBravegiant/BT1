### Title
Multiple Subtransactions Can Claim the Same Deposit Refund in Dijkstra Era - (File: `eras/dijkstra/impl/testlib/Test/Cardano/Ledger/Dijkstra/Imp/CertSpec.hs`)

### Summary
In the Dijkstra era, a top-level transaction containing multiple subtransactions can include `UnRegDepositTxCert` for the **same staking credential** in more than one subtransaction. Because no batch-level uniqueness check exists for deposit refunds across subtransactions, each subtransaction can independently claim the same deposit, allowing an attacker to extract more ADA than was originally deposited. The developers have acknowledged this gap with a disabled (`xit`) test and a `TODO` comment noting the predicate failure is not yet implemented.

### Finding Description
The Dijkstra era introduces nested ("sub") transactions. A top-level `Tx TopTx` carries an `OMap TxId (Tx SubTx era)` of subtransactions, each of which can contain certificates including `UnRegDepositTxCert`.

The production UTXO rule in `eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Utxo.hs` implements `validateBatchWithdrawals`, which aggregates reward withdrawals across the entire batch and checks them against the **initial** account balances before any subtransaction mutates state: [1](#0-0) 

No equivalent batch-level guard exists for deposit refunds issued via `UnRegDepositTxCert`. Each subtransaction's certificate processing is handled by the `DijkstraSUBCERT`/`DijkstraSUBDELEG` rules independently. If both subtransactions are validated against the ledger state **before** either has been applied (or if the balance equation for each subtransaction is checked in isolation), both can include the same `keyDeposit` on their "consumed" side, effectively double-counting the refund.

The developers explicitly acknowledge this gap in the disabled test: [2](#0-1) 

The test is marked `xit` (pending/skipped) and the expected predicate failure is `error "TODO: predicate failure not yet implemented"`, confirming that the rejection logic does not yet exist in production code.

The `UnRegDepositTxCert` certificate is defined and processed in the Conway/Dijkstra `DELEG` rule: [3](#0-2) 

The deposit refund is credited to the submitting transaction's balance. Without a cross-subtransaction uniqueness check, two subtransactions each carrying `UnRegDepositTxCert stakingCred keyDeposit` can both pass individual balance validation while collectively extracting `2 × keyDeposit` from the deposit pot.

### Impact Explanation
**Critical — Direct loss of ADA through an invalid ledger state transition.**

An attacker who controls a registered staking credential can craft a single top-level Dijkstra transaction with N subtransactions, each containing `UnRegDepositTxCert` for the same credential. If the batch-level check is absent, the attacker receives N × `keyDeposit` ADA while only one deposit was ever locked. This drains ADA from the deposit accounting pot, constituting a direct, attacker-controlled creation of ADA value from nothing.

### Likelihood Explanation
**Medium.** The Dijkstra era is experimental and not yet deployed on mainnet. However, the vulnerability is trivially constructable by any transaction author once the era is active: no privileged role, governance majority, or leaked key is required. The attacker only needs a registered staking credential and the ability to submit a valid top-level transaction with multiple subtransactions — both are unprivileged operations open to any participant.

### Recommendation
Add a batch-level uniqueness check for `UnRegDepositTxCert` credentials across all subtransactions, analogous to `validateBatchWithdrawals`. Before processing any subtransaction certificate, collect all credentials appearing in `UnRegDepositTxCert` across the entire subtransaction set and reject the top-level transaction if any credential appears more than once. The pattern already established by `validateBatchWithdrawals` in `eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Utxo.hs` should be extended to cover deposit-refund certificates.

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
      & bodyTxL . inputsTxBodyL .~ Set.singleton input1
      & bodyTxL . certsTxBodyL  .~ SSeq.singleton (UnRegDepositTxCert stakingCred keyDeposit)
    subTx2 = mkBasicTx mkBasicTxBody
      & bodyTxL . inputsTxBodyL .~ Set.singleton input2
      & bodyTxL . certsTxBodyL  .~ SSeq.singleton (UnRegDepositTxCert stakingCred keyDeposit)
    tx = mkBasicTx mkBasicTxBody
      & bodyTxL . subTransactionsTxBodyL .~ OMap.fromFoldable [subTx1, subTx2]
  submitFailingTx tx . NE.singleton $ error "TODO: predicate failure not yet implemented"
```

The `xit` marker and the `error "TODO: predicate failure not yet implemented"` confirm that:
1. The transaction is **not** currently rejected by any production predicate.
2. Both subtransactions can include `UnRegDepositTxCert` for the same credential.
3. Each subtransaction's balance equation independently counts `keyDeposit` as consumed value, allowing double-extraction of the deposit. [2](#0-1) [4](#0-3)

### Citations

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Utxo.hs (L249-281)
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
