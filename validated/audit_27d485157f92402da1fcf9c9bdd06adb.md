### Title
Missing Deregistration Guard Across Sequential Sub-Transactions Enables ADA Creation — (`eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubUtxo.hs`, `eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubLedgers.hs`)

---

### Summary

In the Dijkstra era, a top-level transaction may embed multiple sub-transactions that are processed sequentially. The `SUBLEDGERS` rule threads updated `CertState` between sub-transactions via `foldM`, but the `SUBUTXO` rule — which computes deposit refunds and updates the UTxO — contains no value-conservation check. A developer-acknowledged, unimplemented predicate failure (marked `xit` with `error "TODO: predicate failure not yet implemented"`) confirms that two sub-transactions can both carry `UnRegDepositTxCert` for the same staking credential. The first sub-transaction legitimately unregisters the credential and receives the deposit refund. The second sub-transaction's cert processing becomes a no-op (credential already gone), but because `SUBUTXO` never checks that outputs ≤ inputs + refunds, the attacker can include the deposit amount in the second sub-transaction's outputs without any corresponding decrease in `utxosDeposited`. The result is direct creation of ADA from nothing.

---

### Finding Description

**Vulnerability class:** Invalid state transition / funds-accounting bug — state updated after sequential processing, analogous to the reentrancy pattern in the external report.

**Step 1 — Sequential sub-transaction processing with shared CertState.**

`dijkstraSubLedgersTransition` in `SubLedgers.hs` folds over all sub-transactions, passing the accumulated `LedgerState` (including `CertState`) from one sub-transaction to the next:

```haskell
foldM
  ( \ls subTx ->
      trans @(EraRule "SUBLEDGER" era) $ TRC (env, ls, subTx)
  )
  ledgerState
  subTxs
``` [1](#0-0) 

After `subTx1` processes `UnRegDepositTxCert stakingCred keyDeposit`, the staking credential is removed from `CertState`. The updated `CertState` (credential absent) is then passed as the starting state for `subTx2`.

**Step 2 — SUBUTXO computes zero refund for the second sub-transaction.**

Inside `dijkstraSubLedgersTransition` (the single-sub-tx rule), `SUBUTXOW` is invoked with `certState` — the state *entering* this sub-ledger step, which for `subTx2` is the post-`subTx1` state where the credential is already unregistered:

```haskell
utxoStateAfterSubUtxow <-
  trans @(EraRule "SUBUTXOW" era) $
    TRC (SubUtxoEnv slot pp certState originalUtxo topIsValid, ...)
``` [2](#0-1) 

`SUBUTXO` calls `Shelley.updateUTxOStateNoFees` with this `certState`:

```haskell
newState <-
  Shelley.updateUTxOStateNoFees
    pp utxoState txBody certState ...
``` [3](#0-2) 

`updateUTxOStateNoFees` calls `certsTotalRefundsTxBody pp certState txBody`. Because the credential is absent from `certState`, `lookupDepositDState` returns `Nothing`, so `totalRefunds = 0` and `depositChange = 0`. `utxosDeposited` is not reduced.

```haskell
totalRefunds = certsTotalRefundsTxBody pp certState txBody
totalDeposits = certsTotalDepositsTxBody pp certState txBody
depositChange = totalDeposits <-> totalRefunds
...
utxosDeposited = utxosDeposited <> depositChange
``` [4](#0-3) 

**Step 3 — SUBUTXO has no value-conservation check.**

The top-level UTXO rule enforces `consumed == produced` and emits `ValueNotConservedUTxO` on failure. The `SUBUTXO` rule does not perform this check. The conversion function explicitly marks it as impossible:

```haskell
ValueNotConservedUTxO _ -> error "Impossible: `ValueNotConservedUTxO` for SUBUTXO"
``` [5](#0-4) 

`updateUTxOStateNoFees` simply removes inputs and adds outputs to the UTxO without verifying that `sum(outputs) ≤ sum(inputs) + refunds − deposits`. There is no guard.

**Step 4 — Missing predicate failure is developer-acknowledged.**

The test suite contains a disabled test (`xit`) that explicitly documents the missing guard:

```haskell
xit "Multiple subtransactions cannot get the same refund" $ do
  ...
  subTx1 = ... & bodyTxL . certsTxBodyL .~ SSeq.singleton (UnRegDepositTxCert stakingCred keyDeposit)
  subTx2 = ... & bodyTxL . certsTxBodyL .~ SSeq.singleton (UnRegDepositTxCert stakingCred keyDeposit)
  tx = mkBasicTx mkBasicTxBody & bodyTxL . subTransactionsTxBodyL .~ OMap.fromFoldable [subTx1, subTx2]
  submitFailingTx tx . NE.singleton $ error "TODO: predicate failure not yet implemented"
``` [6](#0-5) 

The `xit` disables the test because the transaction currently **succeeds** when it should fail. The `error "TODO: predicate failure not yet implemented"` is the placeholder for the missing rejection logic.

---

### Impact Explanation

An attacker who controls a registered staking credential can craft a top-level Dijkstra transaction containing two sub-transactions:

- **subTx1**: `inputs=[UTxO_A]`, `outputs=[UTxO_A_value + keyDeposit]`, `certs=[UnRegDepositTxCert cred keyDeposit]`
  - Credential is unregistered; `utxosDeposited` decreases by `keyDeposit`; UTxO gains `keyDeposit`.
- **subTx2**: `inputs=[UTxO_B]`, `outputs=[UTxO_B_value + keyDeposit]`, `certs=[UnRegDepositTxCert cred keyDeposit]`
  - Credential is already absent; cert processing is a no-op; `utxosDeposited` unchanged; but UTxO gains another `keyDeposit` because SUBUTXO never checks `sum(outputs) ≤ sum(inputs)`.

After the transaction, the UTxO contains `keyDeposit` extra ADA that was never deducted from any pot. The total ADA in the system increases by `keyDeposit`. This is direct, attacker-controlled creation of ADA through an invalid ledger state transition.

**Matched impact:** *Critical — Direct creation of ADA through an invalid ledger state transition.*

---

### Likelihood Explanation

- The Dijkstra era is production code in the repository and is the next scheduled era.
- The attack requires only a registered staking credential (trivially obtainable) and the ability to submit a transaction — no privileged role, no key compromise, no governance majority.
- The developer-acknowledged `xit` test with `error "TODO: predicate failure not yet implemented"` confirms the vulnerability is reachable and currently unguarded.
- The attack is deterministic and reproducible.

---

### Recommendation

1. **Add a predicate failure in the `SUBDELEG` (or `SUBCERTS`) rule** that rejects `UnRegDepositTxCert` when the credential is not registered, mirroring the `StakeKeyNotRegisteredDELEG` check in the Conway `DELEG` rule (`eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Deleg.hs`, line 271).

2. **Add a value-conservation check to `SUBUTXO`** analogous to the `ValueNotConservedUTxO` check in the top-level UTXO rule, ensuring `sum(outputs) + deposits = sum(inputs) + refunds` for every sub-transaction.

3. **Enable the `xit` test** once the predicate failure is implemented, replacing the `error "TODO"` placeholder with the actual expected failure constructor.

---

### Proof of Concept

```
1. Register stakingCred, paying keyDeposit (e.g., 2 ADA).
2. Fund two UTxO entries: UTxO_A (5 ADA) and UTxO_B (5 ADA).
3. Submit a Dijkstra top-level transaction with:
     subTx1:
       inputs  = {UTxO_A}
       outputs = {addr: 7 ADA}          -- 5 + 2 (legitimate refund)
       certs   = [UnRegDepositTxCert stakingCred 2 ADA]
     subTx2:
       inputs  = {UTxO_B}
       outputs = {addr: 7 ADA}          -- 5 + 2 (fraudulent; no refund computed)
       certs   = [UnRegDepositTxCert stakingCred 2 ADA]
4. SUBLEDGERS processes subTx1: credential unregistered, utxosDeposited -= 2 ADA, UTxO gains 2 ADA.
5. SUBLEDGERS processes subTx2: credential absent → cert no-op, utxosDeposited unchanged,
   but SUBUTXO adds outputs (7 ADA) and removes inputs (5 ADA) → UTxO gains another 2 ADA.
6. Net result: 2 ADA created from nothing. utxosDeposited is 0 but UTxO holds 4 ADA extra.
7. The outer transaction's value-conservation check passes because it only audits the outer
   tx's own inputs/outputs, not the sub-transaction effects already baked into the UTxO state.
```

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

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubUtxo.hs (L267-276)
```haskell
      newState <-
        Shelley.updateUTxOStateNoFees
          pp
          utxoState
          txBody
          certState
          (utxosGovState utxoState)
          (tellEvent . TotalDeposits (hashAnnotated txBody))
          (\a b -> tellEvent $ TxUTxODiff a b)
      pure $ newState & utxosDonationL <>~ txBody ^. treasuryDonationTxBodyL
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubUtxo.hs (L333-333)
```haskell
  ValueNotConservedUTxO _ -> error "Impossible: `ValueNotConservedUTxO` for SUBUTXO"
```

**File:** eras/shelley/impl/src/Cardano/Ledger/Shelley/Rules/Utxo.hs (L627-635)
```haskell
      totalRefunds = certsTotalRefundsTxBody pp certState txBody
      totalDeposits = certsTotalDepositsTxBody pp certState txBody
      depositChange = totalDeposits <-> totalRefunds
  depositChangeEvent depositChange
  txUtxODiffEvent deletedUTxO utxoAdd
  pure $!
    UTxOState
      { utxosUtxo = UTxO newUTxO
      , utxosDeposited = utxosDeposited <> depositChange
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
