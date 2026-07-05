### Title
Multiple Sub-Transactions in a Dijkstra Batch Can Each Claim the Same Deposit Refund, Draining the Deposit Pot - (`eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs`)

---

### Summary

In the Dijkstra era, the value conservation check for a batch transaction aggregates deposit refunds from all sub-transactions using the **original, pre-batch `certState`**. Because no guard prevents multiple sub-transactions from each claiming the same stake-credential deregistration refund, an attacker can craft a batch where N sub-transactions all carry `UnRegDepositTxCert` for the same credential, inflating the consumed-value total by `(N-1) * keyDeposit`. The value conservation check passes, the deposit pot is decremented N times for a single credential, and the attacker receives `(N-1) * keyDeposit` extra ADA in their outputs. The developers have already identified this gap: the test `"Multiple subtransactions cannot get the same refund"` is disabled (`xit`) with `error "TODO: predicate failure not yet implemented"`.

---

### Finding Description

**Vulnerability class:** funds/accounting bug — deposit refund double-claiming across sub-transactions.

**Root cause — `getConsumedDijkstraValue`:**

```haskell
-- eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs  lines 78-91
getConsumedDijkstraValue pp lookupStakingDeposit lookupDRepDeposit utxo txBody =
  withBothTxLevels
    txBody
    ( \topTxBody ->
        txBodyConsumedValue topTxBody <> subTransactionsConsumedValue topTxBody
    )
    txBodyConsumedValue
  where
    txBodyConsumedValue = getConsumedMaryValue pp lookupStakingDeposit lookupDRepDeposit utxo
    subTransactionsConsumedValue topTxBody =
      foldMap'
        (getConsumedValue pp lookupStakingDeposit lookupDRepDeposit utxo . view bodyTxL)
        (topTxBody ^. subTransactionsTxBodyL)
```

The `lookupStakingDeposit` closure is derived from the **original** `certState` (before any sub-transaction is applied). Every sub-transaction's `getConsumedValue` call independently queries this same snapshot. If two sub-transactions both carry `UnRegDepositTxCert stakingCred keyDeposit`, both lookups succeed (the credential appears registered in the original state), and the total consumed value includes `2 * keyDeposit` in refunds. [1](#0-0) 

**Value conservation check uses this inflated consumed value:**

```haskell
-- eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Utxo.hs  line 381
{- consumed pp utxo₀ txb = produced pp certState txb -}
runTest $ Shelley.validateValueNotConservedUTxO pp originalUtxo certState txBody
``` [2](#0-1) 

Because `consumed` now includes `2 * keyDeposit` in refunds, the check passes as long as the batch outputs are `keyDeposit` larger than they should be — which the attacker controls.

**Deposit pot is decremented per sub-transaction:**

```haskell
-- eras/shelley/impl/src/Cardano/Ledger/Shelley/Rules/Utxo.hs  lines 627-635
totalRefunds = certsTotalRefundsTxBody pp certState txBody
totalDeposits = certsTotalDepositsTxBody pp certState txBody
depositChange = totalDeposits <-> totalRefunds
...
utxosDeposited = utxosDeposited <> depositChange
``` [3](#0-2) 

Each sub-transaction's state update subtracts `keyDeposit` from `utxosDeposited`. With N sub-transactions claiming the same refund, `utxosDeposited` is decremented N times while only one credential's deposit was ever held.

**Contrast with the analogous withdrawal guard:**

The batch-withdrawal check (`validateBatchWithdrawals`) explicitly prevents multiple sub-transactions from withdrawing more than the original account balance:

```haskell
-- eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Utxo.hs  lines 259-275
validateBatchWithdrawals accounts tx = ...
  in failureOnNonEmptyMap badWithdrawals WithdrawalsExceedAccountBalance
``` [4](#0-3) 

No equivalent guard exists for deposit refunds.

**Developer acknowledgement — disabled test:**

```haskell
-- eras/dijkstra/impl/testlib/Test/Cardano/Ledger/Dijkstra/Imp/CertSpec.hs  lines 53-75
xit "Multiple subtransactions cannot get the same refund" $ do
  ...
  submitFailingTx tx . NE.singleton $ error "TODO: predicate failure not yet implemented"
``` [5](#0-4) 

`xit` disables the test entirely; the body is never executed. The `error "TODO: predicate failure not yet implemented"` is a placeholder confirming the rejection logic is absent.

---

### Impact Explanation

An unprivileged transaction author submits a single Dijkstra batch containing N sub-transactions each carrying `UnRegDepositTxCert stakingCred keyDeposit` for the same credential. The value conservation check passes (consumed is inflated by `(N-1) * keyDeposit`), the attacker's outputs are `(N-1) * keyDeposit` larger than the inputs justify, and `utxosDeposited` is decremented N times. The attacker receives `(N-1) * keyDeposit` ADA that was never legitimately theirs, directly draining the deposit pot. Repeated across many batches, the entire deposit pot can be emptied.

This matches: **Critical — Direct loss of ADA through an invalid ledger state transition.**

---

### Likelihood Explanation

The Dijkstra era is the current development tip. Any user who can submit a valid Dijkstra transaction can exploit this. No privileged role, governance majority, or key compromise is required. The attack requires only knowledge of a registered stake credential (publicly observable on-chain) and the ability to construct a batch transaction with duplicate deregistration certificates across sub-transactions.

---

### Recommendation

Add a batch-level deposit-refund guard analogous to `validateBatchWithdrawals`. Before the value conservation check, aggregate all `UnRegDepositTxCert` (and `UnRegDRepTxCert`) credentials across the top-level transaction and all sub-transactions. Reject the batch if any credential appears more than once as a deregistration target. Alternatively, thread the evolving `certState` through sub-transaction processing so that `getConsumedDijkstraValue` uses the post-previous-sub-tx state when computing each sub-transaction's refunds, mirroring how `validateBatchWithdrawals` uses the original account balances as a ceiling.

The disabled test at `CertSpec.hs:53` should be re-enabled once the predicate failure is implemented.

---

### Proof of Concept

1. Register stake credential `C` with deposit `D = ppKeyDepositL`.
2. Construct sub-transaction `subTx1`: inputs = `{utxo1}`, certs = `[UnRegDepositTxCert C D]`, outputs = `[addr ↦ value(utxo1) + D]`.
3. Construct sub-transaction `subTx2`: inputs = `{utxo2}`, certs = `[UnRegDepositTxCert C D]`, outputs = `[addr ↦ value(utxo2) + D]`.
4. Construct top-level transaction: inputs = `{}`, outputs = `[]`, fee = `F`, sub-transactions = `{subTx1, subTx2}`.
5. Value conservation check: consumed = `value(utxo1) + value(utxo2) + D + D`; produced = `(value(utxo1)+D) + (value(utxo2)+D) + F`. Balances when `F = 0` (or adjust outputs accordingly).
6. `getConsumedDijkstraValue` counts `D` twice (both sub-tx lookups hit the original `certState` where `C` is registered).
7. The check passes; the attacker's outputs contain `D` extra ADA sourced from `utxosDeposited`.
8. `utxosDeposited` is decremented by `2D` while only one credential's deposit (`D`) was held, leaving the deposit pot `D` short. [6](#0-5) [7](#0-6) [8](#0-7) [5](#0-4)

### Citations

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs (L65-91)
```haskell
getConsumedDijkstraValue ::
  forall era l.
  ( DijkstraEraTxBody era
  , EraUTxO era
  , Value era ~ MaryValue
  , STxLevel l era ~ STxBothLevels l era
  ) =>
  PParams era ->
  (Credential Staking -> Maybe Coin) ->
  (Credential DRepRole -> Maybe Coin) ->
  UTxO era ->
  TxBody l era ->
  Value era
getConsumedDijkstraValue pp lookupStakingDeposit lookupDRepDeposit utxo txBody =
  withBothTxLevels
    txBody
    ( \topTxBody ->
        txBodyConsumedValue topTxBody <> subTransactionsConsumedValue topTxBody
    )
    txBodyConsumedValue
  where
    txBodyConsumedValue :: forall m. TxBody m era -> Value era
    txBodyConsumedValue = getConsumedMaryValue pp lookupStakingDeposit lookupDRepDeposit utxo
    subTransactionsConsumedValue topTxBody =
      foldMap'
        (getConsumedValue pp lookupStakingDeposit lookupDRepDeposit utxo . view bodyTxL)
        (topTxBody ^. subTransactionsTxBodyL)
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Utxo.hs (L259-275)
```haskell
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

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Utxo.hs (L343-381)
```haskell
dijkstraUtxoTransition = do
  TRC (DijkstraUtxoEnv slot pp certState originalUtxo, utxos, stAnnTx) <-
    judgmentContext
  let tx = stAnnTx ^. txStAnnTxG
  -- this is the original Accounts, before any transactions were applied
  let accounts = certState ^. certDStateL . accountsL

  let txBody = tx ^. bodyTxL

  {- inInterval (SlotOf Γ) (ValidIntervalOf txTop) -}
  runTest $ Allegra.validateOutsideValidityIntervalUTxO slot txBody

  sysSt <- liftSTS $ asks systemStart
  ei <- liftSTS $ asks epochInfo

  runTest $ Alonzo.validateOutsideForecast ei slot sysSt tx

  {- SpendInputs ≠ ∅ -}
  runTestOnSignal $ Shelley.validateInputSetEmptyUTxO txBody

  let allInputs = txBody ^. allInputsTxBodyF
      inputs = txBody ^. inputsTxBodyL

  {- SpendInputsOf txTop ∪ RefInputsOf txTop ∪ CollInputsOf txTop ⊆ dom(utxo₀) -}
  runTest $ Shelley.validateBadInputsUTxO originalUtxo allInputs

  {- SpendInputsOf txTop ⊆ dom(utxo_s) — prevents double-spend with subtxs -}
  runTest $ Shelley.validateBadInputsUTxO (utxosUtxo utxos) inputs

  {- minfee pp txTop utxo₀ ≤ txfee txb -}
  runTest $ Shelley.validateFeeTooSmallUTxO pp tx originalUtxo

  {- (RedeemersOf txTop ≠ ∅ ⊎ Any (λ txSub → RedeemersOf txSub ≠ ∅) subtxs) → collateralCheck -}
  validate $ validateBatchCollateral pp tx originalUtxo

  runTest $ validateBatchWithdrawals accounts tx

  {- consumed pp utxo₀ txb = produced pp certState txb -}
  runTest $ Shelley.validateValueNotConservedUTxO pp originalUtxo certState txBody
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
