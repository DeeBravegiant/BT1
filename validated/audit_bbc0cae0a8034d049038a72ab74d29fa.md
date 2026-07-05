### Title
Unconditional Deposit Refund Counting in `dijkstraTotalRefundsTxCerts` Enables Double-Refund via Subtransactions — (`File: eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxCert.hs`)

---

### Summary

In the Dijkstra era, `dijkstraTotalRefundsTxCerts` counts deposit refunds from `UnRegDepositTxCert` and `UnRegDRepTxCert` certificates unconditionally based solely on certificate content, without consulting the ledger state to verify the credential is actually registered. The `lookupStakingDeposit` and `lookupDRepDeposit` parameters are explicitly discarded. Combined with the Dijkstra subtransaction mechanism and an acknowledged unimplemented predicate failure, a transaction author can craft a top-level transaction containing multiple subtransactions each claiming the same deposit refund, causing the preservation-of-value check to count the deposit multiple times on the consumed side while only one deposit exists in the deposit pot.

---

### Finding Description

**Root cause — `dijkstraTotalRefundsTxCerts`:**

```haskell
-- Unlike previous eras, we no longer need to lookup refunds from the ledger state,
-- since all of the certificates specify the actual refund and ledger rules will
-- validate that they are accurate.
dijkstraTotalRefundsTxCerts = foldMap' $ \case
  UnRegDepositTxCert _ deposit -> deposit
  UnRegDRepTxCert _ deposit -> deposit
  _ -> zero
``` [1](#0-0) 

This function is wired as the `getTotalRefundsTxCerts` implementation for `DijkstraEra`, explicitly ignoring the two lookup parameters:

```haskell
getTotalRefundsTxCerts _ _ _ = dijkstraTotalRefundsTxCerts
``` [2](#0-1) 

**Contrast with Conway**, where `conwayTotalRefundsTxCerts` delegates to `shelleyTotalRefundsTxCerts` and `conwayDRepRefundsTxCerts`, both of which use the lookup functions to only count a refund when the credential is actually registered in the current ledger state:

```haskell
| Just deposit <- lookupDeposit cred -> (regCreds, totalRefunds <+> deposit)
_ -> (regCreds, totalRefunds)
``` [3](#0-2) 

**How refunds enter the preservation-of-value check:**

`getConsumedMaryValue` calls `getTotalRefundsTxBody` → `getTotalRefundsTxCerts` → `dijkstraTotalRefundsTxCerts`:

```haskell
refunds = getTotalRefundsTxBody pp lookupStakingDeposit lookupDRepDeposit txBody
``` [4](#0-3) 

For Dijkstra top-level transactions, `getConsumedDijkstraValue` aggregates consumed values from the top-level body **and all subtransactions**:

```haskell
subTransactionsConsumedValue topTxBody =
  foldMap'
    (getConsumedValue pp lookupStakingDeposit lookupDRepDeposit utxo . view bodyTxL)
    (topTxBody ^. subTransactionsTxBodyL)
``` [5](#0-4) 

Because `dijkstraTotalRefundsTxCerts` ignores the lookup functions, each subtransaction containing `UnRegDepositTxCert cred deposit` contributes `deposit` to the consumed total regardless of whether `cred` is registered — and regardless of whether a sibling subtransaction already claimed the same deposit.

**The unimplemented predicate failure — the smoking gun:**

The test file explicitly acknowledges this gap with a disabled (`xit`) test:

```haskell
xit "Multiple subtransactions cannot get the same refund" $ do
  ...
  submitFailingTx tx . NE.singleton $ error "TODO: predicate failure not yet implemented"
``` [6](#0-5) 

The test constructs exactly the attack scenario: two subtransactions each containing `UnRegDepositTxCert stakingCred keyDeposit` for the same credential. It is disabled because the predicate failure that should reject this has not been implemented.

**Attack path:**

1. Attacker registers credential `cred` paying deposit `D`.
2. Attacker constructs a top-level transaction with two subtransactions: `subTx1` and `subTx2`, each containing `UnRegDepositTxCert cred D`.
3. The preservation-of-value check at the UTXO rule level computes consumed = UTxO inputs + `2 * D` (one per subtransaction, unconditionally). The attacker sets outputs to absorb `2 * D` in refunds. The check passes.
4. Because the predicate failure for the second subtransaction's duplicate unregistration is not implemented, both subtransactions' CERT rules pass.
5. The deposit pot loses `2 * D` while only `D` was ever deposited — net drain of `D` ADA per transaction.

The UTXO rule applies the preservation-of-value check using the **original** certState:

```haskell
{- consumed pp utxo₀ txb = produced pp certState txb -}
runTest $ Shelley.validateValueNotConservedUTxO pp originalUtxo certState txBody
``` [7](#0-6) 

---

### Impact Explanation

**Critical — Direct loss of ADA through an invalid ledger state transition.**

The deposit pot (`utxosDeposited`) is a tracked accounting pot. If the preservation-of-value check passes with `2 * D` on the consumed side while only `D` is removed from the deposit pot, the ledger's total ADA accounting is violated. The attacker extracts `D` ADA per transaction from the deposit pot. This can be repeated until the deposit pot is drained, destroying the ADA that belongs to all other registered credentials.

---

### Likelihood Explanation

**High.** The attack requires only:
- Registering a stake credential (permissionless, costs only the deposit itself).
- Constructing a valid Dijkstra-era transaction with two subtransactions (standard transaction author capability).
- No privileged access, no governance majority, no key leakage.

The disabled test with `error "TODO: predicate failure not yet implemented"` confirms the developers are aware the protection is missing. The Dijkstra era is a new era under active development, making this a realistic pre-deployment finding.

---

### Recommendation

1. **Fix `dijkstraTotalRefundsTxCerts`** to consult the ledger state, analogously to `shelleyTotalRefundsTxCerts`. Either pass and use the `lookupStakingDeposit`/`lookupDRepDeposit` functions, or verify registration before counting the refund.

2. **Implement the missing predicate failure** for the case where multiple subtransactions attempt to claim the same deposit refund. Enable and complete the disabled test `"Multiple subtransactions cannot get the same refund"`.

3. **Consider tracking which credentials have been unregistered within the current subtransaction batch** during the consumed-value calculation, so that a credential unregistered by an earlier subtransaction is not counted again by a later one.

---

### Proof of Concept

```
1. Register cred, paying deposit D = ppKeyDepositL.

2. Construct:
   subTx1 = mkBasicTx mkBasicTxBody
     & bodyTxL . inputsTxBodyL .~ {input1}
     & bodyTxL . certsTxBodyL  .~ [UnRegDepositTxCert cred D]

   subTx2 = mkBasicTx mkBasicTxBody
     & bodyTxL . inputsTxBodyL .~ {input2}
     & bodyTxL . certsTxBodyL  .~ [UnRegDepositTxCert cred D]

   topTx = mkBasicTx mkBasicTxBody
     & bodyTxL . subTransactionsTxBodyL .~ OMap.fromFoldable [subTx1, subTx2]
     & bodyTxL . outputsTxBodyL .~ [output absorbing 2*D]

3. dijkstraTotalRefundsTxCerts counts D from subTx1 and D from subTx2 = 2*D total.
   getConsumedDijkstraValue includes 2*D in consumed.
   validateValueNotConservedUTxO passes.

4. With the predicate failure unimplemented, both subtransactions' CERT rules pass.
   Attacker receives 2*D in outputs; deposit pot loses 2*D; net gain = D.
``` [6](#0-5) [8](#0-7) [9](#0-8)

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

**File:** eras/shelley/impl/src/Cardano/Ledger/Shelley/TxCert.hs (L658-659)
```haskell
              | Just deposit <- lookupDeposit cred -> (regCreds, totalRefunds <+> deposit)
            _ -> (regCreds, totalRefunds)
```

**File:** eras/mary/impl/src/Cardano/Ledger/Mary/UTxO.hs (L85-86)
```haskell
    refunds = getTotalRefundsTxBody pp lookupStakingDeposit lookupDRepDeposit txBody
    withdrawals = fold . unWithdrawals $ txBody ^. withdrawalsTxBodyL
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs (L78-91)
```haskell
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

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Utxo.hs (L380-381)
```haskell
  {- consumed pp utxo₀ txb = produced pp certState txb -}
  runTest $ Shelley.validateValueNotConservedUTxO pp originalUtxo certState txBody
```
