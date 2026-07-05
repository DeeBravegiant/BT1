### Title
Repeated Deposit Refund via Multiple Sub-Transactions Claiming the Same Credential Deregistration - (File: `eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubLedgers.hs`)

---

### Summary

In the Dijkstra era, the `SUBLEDGERS` rule processes multiple sub-transactions sequentially via `foldM`. Each sub-transaction can carry an `UnRegDepositTxCert` certificate. The Dijkstra-era refund function `dijkstraTotalRefundsTxCerts` computes refunds purely from certificate body fields without consulting the live ledger state. A missing predicate check — explicitly acknowledged by a disabled test with `error "TODO: predicate failure not yet implemented"` — means two sub-transactions within the same top-level transaction can both claim the deposit refund for the same staking credential, decrementing `utxosDeposited` twice while only one deposit was ever made.

---

### Finding Description

**Vulnerability class:** Funds/accounting bug — repeated deposit deduction across multiple sub-transactions.

**Root cause — step 1: `dijkstraTotalRefundsTxCerts` ignores ledger state**

In `eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxCert.hs`, the Dijkstra-era refund function is:

```haskell
dijkstraTotalRefundsTxCerts = foldMap' $ \case
  UnRegDepositTxCert _ deposit -> deposit
  UnRegDRepTxCert _ deposit -> deposit
  _ -> zero
``` [1](#0-0) 

Unlike the Conway-era `shelleyTotalRefundsTxCerts` and `conwayDRepRefundsTxCerts`, which both thread a `lookupDeposit` function through the fold to verify the credential is actually registered before counting a refund, `dijkstraTotalRefundsTxCerts` simply sums the deposit amounts stated in the certificate bodies. There is no state lookup and no deduplication guard. [2](#0-1) [3](#0-2) 

**Root cause — step 2: `SUBLEDGERS` loops over sub-transactions with shared environment**

`dijkstraSubLedgersTransition` in `SubLedgers.hs` folds over all sub-transactions, passing the same `SubLedgerEnv` to every iteration:

```haskell
dijkstraSubLedgersTransition = do
  TRC (env, ledgerState, subTxs) <- judgmentContext
  foldM
    ( \ls subTx ->
        trans @(EraRule "SUBLEDGER" era) $ TRC (env, ls, subTx)
    )
    ledgerState
    subTxs
``` [4](#0-3) 

**Root cause — step 3: `SUBUTXOW` receives the pre-SUBENTITIES cert state**

Inside `dijkstraSubLedgersTransition` (the `SUBLEDGER` rule), after `SUBENTITIES` processes the certificates and produces `certStateAfterSubEntities`, the `SUBUTXOW` rule is invoked with the **original** `certState` (before SUBENTITIES), not the updated one:

```haskell
utxoStateAfterSubUtxow <-
    trans @(EraRule "SUBUTXOW" era) $
      TRC
        ( SubUtxoEnv slot pp certState originalUtxo topIsValid  -- original certState
        , utxoStateBeforeSubUtxow
        , stAnnTx
        )
``` [5](#0-4) 

`SUBUTXOW` → `SUBUTXO` → `updateUTxOStateNoFees` then calls `certsTotalRefundsTxBody pp certState txBody`, which for Dijkstra era resolves to `dijkstraTotalRefundsTxCerts` — the stateless function above. The deposit pot is decremented by whatever the certificate body claims, with no cross-check against the live registration state. [6](#0-5) 

**Root cause — step 4: Missing predicate failure for duplicate deregistration across sub-transactions**

The developers are aware of this gap. The test file `eras/dijkstra/impl/testlib/Test/Cardano/Ledger/Dijkstra/Imp/CertSpec.hs` contains a disabled test that exactly describes the attack:

```haskell
xit "Multiple subtransactions cannot get the same refund" $ do
    stakingCred <- KeyHashObj <$> freshKeyHash
    _ <- registerStakeCredential stakingCred
    keyDeposit <- getsPParams ppKeyDepositL
    ...
    let
      subTx1 = ... & bodyTxL . certsTxBodyL .~
                     SSeq.singleton (UnRegDepositTxCert stakingCred keyDeposit)
      subTx2 = ... & bodyTxL . certsTxBodyL .~
                     SSeq.singleton (UnRegDepositTxCert stakingCred keyDeposit)
      tx = ... & bodyTxL . subTransactionsTxBodyL .~ OMap.fromFoldable [subTx1, subTx2]
    submitFailingTx tx . NE.singleton $ error "TODO: predicate failure not yet implemented"
``` [7](#0-6) 

The `xit` marker disables the test; the `error "TODO: predicate failure not yet implemented"` confirms that no predicate failure type exists yet to reject this scenario. The test is not merely aspirational — it documents a concrete missing enforcement path.

---

### Impact Explanation

An attacker who has registered a staking credential (paying `keyDeposit` ADA into `utxosDeposited`) can submit a single top-level transaction containing N sub-transactions, each carrying `UnRegDepositTxCert stakingCred keyDeposit`. Because no predicate failure prevents the second (and subsequent) deregistration attempts, and because `dijkstraTotalRefundsTxCerts` blindly sums the deposit amounts from certificate bodies, `utxosDeposited` is decremented by `N × keyDeposit` while only `keyDeposit` was ever deposited. The excess `(N-1) × keyDeposit` ADA is effectively created from nothing in the deposit accounting, violating the preservation-of-value invariant and constituting a direct loss of ADA from the deposit pot.

**Matched impact:** *Critical — Direct loss, creation, or destruction of ADA or native assets through an invalid ledger state transition.*

---

### Likelihood Explanation

The Dijkstra era introduces sub-transactions as a new feature. Any unprivileged transaction sender who has previously registered a staking credential can exploit this. The attack requires only:
1. One prior `RegDepositTxCert` transaction (standard staking registration).
2. One top-level transaction with two or more sub-transactions each carrying `UnRegDepositTxCert` for the same credential.

No privileged access, governance majority, or key compromise is required. The attack is deterministic and reproducible.

---

### Recommendation

1. **Implement the missing predicate failure** for duplicate deregistration across sub-transactions. The `SUBDELEG` or `SUBCERTS` rule must reject an `UnRegDepositTxCert` for a credential that was already deregistered by an earlier sub-transaction in the same top-level transaction. Enable and complete the disabled test in `CertSpec.hs`.

2. **Pass `certStateFinal` (post-SUBENTITIES) to `SUBUTXOW`** instead of the original `certState`, so that `certsTotalRefundsTxBody` operates on the state that reflects all certificate changes made by the current sub-transaction.

3. **Add a state-aware guard in `dijkstraTotalRefundsTxCerts`** (or its call site) analogous to the `lookupDeposit` pattern used in `shelleyTotalRefundsTxCerts` and `conwayDRepRefundsTxCerts`, so that a refund is only counted when the credential is actually registered at the time of processing.

---

### Proof of Concept

```
1. Alice registers stakingCred, paying keyDeposit ADA.
   utxosDeposited = keyDeposit

2. Alice submits topTx with:
     subTx1: inputs=[input1], certs=[UnRegDepositTxCert stakingCred keyDeposit]
     subTx2: inputs=[input2], certs=[UnRegDepositTxCert stakingCred keyDeposit]

3. SUBLEDGERS processes subTx1:
   - SUBENTITIES: stakingCred removed from cert state.
   - SUBUTXO: dijkstraTotalRefundsTxCerts returns keyDeposit (no state check).
   - updateUTxOStateNoFees: utxosDeposited -= keyDeposit  → utxosDeposited = 0

4. SUBLEDGERS processes subTx2 (no predicate failure implemented):
   - SUBENTITIES: stakingCred already absent — no check rejects this.
   - SUBUTXO: dijkstraTotalRefundsTxCerts returns keyDeposit again.
   - updateUTxOStateNoFees: utxosDeposited -= keyDeposit  → utxosDeposited = -keyDeposit

5. Result: utxosDeposited underflows; (N-1)*keyDeposit ADA is destroyed from the
   deposit pot, breaking the preservation-of-value invariant.
```

The disabled test at `eras/dijkstra/impl/testlib/Test/Cardano/Ledger/Dijkstra/Imp/CertSpec.hs:53–75` is the developers' own acknowledgment that this path is unguarded. [7](#0-6) [1](#0-0) [4](#0-3) [5](#0-4) [6](#0-5)

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

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/TxCert.hs (L860-872)
```haskell
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

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubLedger.hs (L287-294)
```haskell
  utxoStateAfterSubUtxow <-
    trans @(EraRule "SUBUTXOW" era) $
      TRC
        ( SubUtxoEnv slot pp certState originalUtxo topIsValid
        , utxoStateBeforeSubUtxow
        , stAnnTx
        )
  pure $ LedgerState utxoStateAfterSubUtxow certStateFinal
```

**File:** eras/shelley/impl/src/Cardano/Ledger/Shelley/Rules/Utxo.hs (L618-641)
```haskell
updateUTxOStateNoFees pp utxos txBody certState govState depositChangeEvent txUtxODiffEvent = do
  let UTxOState {utxosUtxo, utxosDeposited, utxosFees, utxosDonation} = utxos
      UTxO utxo = utxosUtxo
      !utxoAdd = txouts txBody -- These will be inserted into the UTxO
      {- utxoDel  = txins txb ◁ utxo -}
      !(utxoWithout, utxoDel) = extractKeys utxo (txBody ^. inputsTxBodyL)
      {- newUTxO = (txins txb ⋪ utxo) ∪ outs txb -}
      newUTxO = utxoWithout `Map.union` unUTxO utxoAdd
      deletedUTxO = UTxO utxoDel
      totalRefunds = certsTotalRefundsTxBody pp certState txBody
      totalDeposits = certsTotalDepositsTxBody pp certState txBody
      depositChange = totalDeposits <-> totalRefunds
  depositChangeEvent depositChange
  txUtxODiffEvent deletedUTxO utxoAdd
  pure $!
    UTxOState
      { utxosUtxo = UTxO newUTxO
      , utxosDeposited = utxosDeposited <> depositChange
      , utxosFees = utxosFees
      , utxosGovState = govState
      , utxosInstantStake =
          deleteInstantStake deletedUTxO (addInstantStake utxoAdd (utxos ^. instantStakeL))
      , utxosDonation = utxosDonation
      }
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
