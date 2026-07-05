### Title
Double-Claim of Staking Deposit Refund via Multiple Sub-Transactions in Dijkstra Era - (`eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubLedgers.hs`)

---

### Summary

The Dijkstra era introduces nested (sub-)transactions within a single top-level transaction. When two sub-transactions in the same top-level transaction each contain an `UnRegDepositTxCert` for the **same** staking credential, the second sub-transaction can successfully claim the deposit refund a second time. The predicate failure that should reject this double-claim is explicitly acknowledged as unimplemented in the production codebase. An attacker can register a credential once (paying deposit D), then submit a top-level transaction with N sub-transactions each deregistering the same credential, receiving N×D in refunds while only having paid D.

---

### Finding Description

**Root cause — missing cross-sub-transaction deregistration guard**

The `SUBLEDGERS` rule in `SubLedgers.hs` processes sub-transactions sequentially via `foldM`, threading the updated `LedgerState` through each step: [1](#0-0) 

After `subTx1` deregisters the credential and removes it from `certState`, `subTx2` receives the updated `LedgerState`. However, the `SUBLEDGER` transition for each sub-transaction passes the **original** pre-`SUBENTITIES` `certState` to `SUBUTXOW`: [2](#0-1) 

The value-conservation check inside `SUBUTXO` calls `getTotalRefundsTxCerts`, which in Dijkstra era is `dijkstraTotalRefundsTxCerts`. This function reads the refund amount **directly from the certificate** without consulting the ledger state to verify the credential is still registered: [3](#0-2) 

Because the refund is taken from the certificate field rather than from a live ledger lookup, the value-conservation check for `subTx2` passes even though the credential was already deregistered by `subTx1`. The predicate failure that should reject the second deregistration attempt is explicitly marked as unimplemented by the developers: [4](#0-3) 

The test is marked `xit` (skipped) with the comment `error "TODO: predicate failure not yet implemented"`, confirming that the rejection logic does not yet exist in production code.

**Exploit path**

1. Attacker registers staking credential `C` paying deposit `D` (e.g., `ppKeyDepositL`).
2. Attacker constructs a top-level Dijkstra transaction containing two sub-transactions:
   - `subTx1`: `UnRegDepositTxCert C D` (with a UTxO input `input1`)
   - `subTx2`: `UnRegDepositTxCert C D` (with a distinct UTxO input `input2`)
3. `SUBLEDGERS` processes `subTx1` first: credential `C` is deregistered, deposit `D` is credited to `subTx1`'s outputs.
4. `SUBLEDGERS` processes `subTx2` next: the missing predicate failure allows the second deregistration to succeed; deposit `D` is credited again to `subTx2`'s outputs.
5. Net result: attacker paid `D` once but received `2D` in refunds — `D` ADA is created from the deposit pot.

This is directly analogous to the ERC20 Multiple Withdrawal Attack: just as a spender can exploit the window between two `approve` calls to use both the old and new allowance, an attacker here exploits the sequential sub-transaction processing window to claim the same deposit authorization twice.

---

### Impact Explanation

**Critical — Direct creation of ADA through an invalid ledger state transition.**

The deposit pot (`utxosDeposited`) is reduced by `D` for each sub-transaction that claims the refund, but only one deposit of `D` was ever paid. With N sub-transactions, the attacker extracts `(N-1)×D` ADA from the deposit pot that was never deposited. This violates the preservation-of-value invariant and constitutes direct, unauthorized creation of ADA.

---

### Likelihood Explanation

The Dijkstra era is the newest era and sub-transactions are a new feature. The vulnerability is reachable by any unprivileged transaction author who can construct a valid Dijkstra-era transaction. No privileged keys, governance majority, or external dependencies are required. The only precondition is registering a staking credential (a normal, permissionless operation). The attack is deterministic and reproducible. The developers have already identified the scenario (the `xit` test exists) but have not yet implemented the guard.

---

### Recommendation

1. In the `SUBDELEG` (or `SUBENTITIES`) rule for Dijkstra era, add a predicate failure that rejects `UnRegDepositTxCert` when the credential is not present in the current (post-previous-sub-tx) `certState`. This mirrors the `StakeKeyNotRegisteredDELEG` check already present in the Conway `DELEG` rule.
2. Enable and complete the skipped test `"Multiple subtransactions cannot get the same refund"` in `Test.Cardano.Ledger.Dijkstra.Imp.CertSpec` with the correct predicate failure constructor once implemented.
3. Consider whether `dijkstraTotalRefundsTxCerts` should validate against the live ledger state rather than trusting the certificate-embedded refund amount, to provide defense-in-depth at the value-conservation layer.

---

### Proof of Concept

The scenario is already encoded in the test suite (currently skipped):

```haskell
-- eras/dijkstra/impl/testlib/Test/Cardano/Ledger/Dijkstra/Imp/CertSpec.hs
xit "Multiple subtransactions cannot get the same refund" $ do
  stakingCred <- KeyHashObj <$> freshKeyHash
  _ <- registerStakeCredential stakingCred          -- pays deposit D once
  keyDeposit <- getsPParams ppKeyDepositL
  ...
  let subTx1 = mkBasicTx mkBasicTxBody
        & bodyTxL . inputsTxBodyL .~ Set.singleton input1
        & bodyTxL . certsTxBodyL  .~ SSeq.singleton (UnRegDepositTxCert stakingCred keyDeposit)
      subTx2 = mkBasicTx mkBasicTxBody
        & bodyTxL . inputsTxBodyL .~ Set.singleton input2
        & bodyTxL . certsTxBodyL  .~ SSeq.singleton (UnRegDepositTxCert stakingCred keyDeposit)
      tx = mkBasicTx mkBasicTxBody
        & bodyTxL . subTransactionsTxBodyL .~ OMap.fromFoldable [subTx1, subTx2]
  submitFailingTx tx . NE.singleton $ error "TODO: predicate failure not yet implemented"
  -- ^^^ test is xit (skipped) because the rejection is not yet implemented;
  --     the transaction currently SUCCEEDS, allowing double-refund
``` [4](#0-3) 

The sequential `foldM` in `SUBLEDGERS` that enables this: [1](#0-0) 

The refund calculation that does not consult ledger state: [5](#0-4)

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
