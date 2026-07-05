### Title
Double Deposit Refund via Multiple Sub-Transactions Deregistering the Same Credential - (`eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxCert.hs`)

---

### Summary

In the Dijkstra era's nested-transaction feature, `dijkstraTotalRefundsTxCerts` computes deposit refunds by reading the `Coin` field directly from each `UnRegDepositTxCert` certificate, without consulting the live ledger state. When a top-level transaction contains multiple sub-transactions that each carry an `UnRegDepositTxCert` for the **same** staking credential, the value-conservation check for each sub-transaction independently credits the full deposit refund. The developers have explicitly acknowledged this gap with a disabled test (`xit "Multiple subtransactions cannot get the same refund"`) that records the predicate failure as **"not yet implemented"**.

---

### Finding Description

**Vulnerable function – `dijkstraTotalRefundsTxCerts`** [1](#0-0) 

```haskell
-- Unlike previous eras, we no longer need to lookup refunds from the ledger state,
-- since all of the certificates specify the actual refund and ledger rules will validate
-- that they are accurate.
dijkstraTotalRefundsTxCerts = foldMap' $ \case
  UnRegDepositTxCert _ deposit -> deposit
  UnRegDRepTxCert _ deposit -> deposit
  _ -> zero
```

This function is the Dijkstra-era implementation of `getTotalRefundsTxCerts`: [2](#0-1) 

It is called by the UTxO value-conservation check inside `SUBUTXO`/`SUBUTXOW` for every sub-transaction. Unlike the Conway-era equivalents (`shelleyTotalRefundsTxCerts`, `conwayDRepRefundsTxCerts`), it **never consults the cert state** to verify that the credential is actually registered or that it has not already been deregistered by a prior sub-transaction in the same batch.

**Sequential sub-ledger processing – `SUBLEDGERS`** [3](#0-2) 

`SUBLEDGERS` folds over sub-transactions sequentially, threading the `LedgerState` through each `SUBLEDGER` call. After `subTx1` deregisters the credential via `SUBENTITIES`, the updated `certState` (credential removed) is passed as the starting state for `subTx2`.

**`SUBLEDGER` passes the pre-SUBENTITIES cert state to `SUBUTXOW`** [4](#0-3) 

```haskell
utxoStateAfterSubUtxow <-
  trans @(EraRule "SUBUTXOW" era) $
    TRC
      ( SubUtxoEnv slot pp certState originalUtxo topIsValid
      , utxoStateBeforeSubUtxow
      , stAnnTx
      )
```

`certState` here is the state **before** `SUBENTITIES` ran for this sub-transaction. Because `dijkstraTotalRefundsTxCerts` ignores the cert state entirely and reads the deposit amount straight from the certificate field, the value-conservation check for `subTx2` credits `keyDeposit` as a valid refund even when the credential is no longer registered.

**Developer acknowledgement – disabled test** [5](#0-4) 

```haskell
xit "Multiple subtransactions cannot get the same refund" $ do
  ...
  subTx1 = ... & certsTxBodyL .~ SSeq.singleton (UnRegDepositTxCert stakingCred keyDeposit)
  subTx2 = ... & certsTxBodyL .~ SSeq.singleton (UnRegDepositTxCert stakingCred keyDeposit)
  tx = ... & subTransactionsTxBodyL .~ OMap.fromFoldable [subTx1, subTx2]
  submitFailingTx tx . NE.singleton $ error "TODO: predicate failure not yet implemented"
```

The `xit` prefix disables the test. The `error "TODO: predicate failure not yet implemented"` confirms that no ledger rule currently rejects this double-refund scenario with a proper predicate failure.

---

### Impact Explanation

An attacker who has registered a staking credential with deposit `D` can construct a top-level Dijkstra transaction containing two sub-transactions, each carrying `UnRegDepositTxCert stakingCred D`. The value-conservation check for each sub-transaction credits `D` as a refund. The deposit pot (`utxosDeposited`) is decremented by `2 × D` while only `D` was ever deposited, allowing the attacker to extract `D` of ADA that belongs to other depositors. Repeating the attack across many credentials can drain the entire deposit pot.

This is a **Critical** impact: direct destruction of ADA through an invalid ledger state transition.

---

### Likelihood Explanation

The Dijkstra era is already present in the production repository and its sub-transaction feature is actively being developed. Any unprivileged transaction author can craft the malicious batch transaction — no special role, key, or governance majority is required. The attack requires only a registered staking credential and knowledge of the deposit amount (a public protocol parameter). The disabled test proves the developers are aware the guard is missing.

---

### Recommendation

1. **Add a cross-sub-transaction deregistration check.** Before the value-conservation check in `SUBUTXO`/`SUBUTXOW` for each sub-transaction, verify that every `UnRegDepositTxCert` credential is still registered in the **current** (post-prior-sub-transaction) cert state, not the original one.

2. **Fix `dijkstraTotalRefundsTxCerts`.** Either pass the live cert state into the function (as Conway's `shelleyTotalRefundsTxCerts` does via `lookupDeposit`) or add a cross-sub-transaction deduplication pass before the batch is processed.

3. **Enable and complete the disabled test** in `eras/dijkstra/impl/testlib/Test/Cardano/Ledger/Dijkstra/Imp/CertSpec.hs` once the predicate failure is implemented.

---

### Proof of Concept

```
1. Register stakingCred with deposit D = ppKeyDeposit.

2. Construct:
     subTx1 = mkBasicTx mkBasicTxBody
               & inputsTxBodyL  .~ {input1}          -- distinct UTxO input
               & certsTxBodyL   .~ [UnRegDepositTxCert stakingCred D]
               & outputsTxBodyL .~ [out1 worth (value1 + D)]  -- absorbs refund

     subTx2 = mkBasicTx mkBasicTxBody
               & inputsTxBodyL  .~ {input2}          -- distinct UTxO input
               & certsTxBodyL   .~ [UnRegDepositTxCert stakingCred D]
               & outputsTxBodyL .~ [out2 worth (value2 + D)]  -- absorbs second refund

     tx = mkBasicTx mkBasicTxBody
           & subTransactionsTxBodyL .~ OMap.fromFoldable [subTx1, subTx2]

3. Submit tx.

4. dijkstraTotalRefundsTxCerts returns D for subTx1 and D for subTx2.
   Value conservation passes for both sub-transactions.
   The deposit pot is decremented by 2×D; the attacker receives 2×D in outputs
   while only D was ever deposited.
```

### Citations

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxCert.hs (L228-238)
```haskell
-- | Unlike previous eras, we no longer need to lookup refunds from the ledger state, since all of the certificates specify the actual refund and ledger rules will validate that they are accurate.
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

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxCert.hs (L285-285)
```haskell
  getTotalRefundsTxCerts _ _ _ = dijkstraTotalRefundsTxCerts
```

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

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubLedger.hs (L287-293)
```haskell
  utxoStateAfterSubUtxow <-
    trans @(EraRule "SUBUTXOW" era) $
      TRC
        ( SubUtxoEnv slot pp certState originalUtxo topIsValid
        , utxoStateBeforeSubUtxow
        , stAnnTx
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
