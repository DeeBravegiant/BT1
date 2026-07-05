### Title
Dijkstra Era Sub-Transactions Allow Double-Claiming of Deposit Refunds - (`eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxCert.hs`)

---

### Summary

In the Dijkstra era, a transaction author can include multiple sub-transactions each containing an `UnRegDepositTxCert` (or `UnRegDRepTxCert`) for the same credential. Because `dijkstraTotalRefundsTxCerts` computes refunds purely from the cert fields without consulting the ledger state, and because the `SUBDELEG` rule lacks a predicate failure for attempting to unregister an already-unregistered credential, each sub-transaction independently credits the full deposit refund. The deposit pot (`utxosDeposited`) is decremented once per sub-transaction, allowing an attacker to extract more ADA than was ever deposited.

---

### Finding Description

**Root cause 1 — `dijkstraTotalRefundsTxCerts` ignores ledger state**

`dijkstraTotalRefundsTxCerts` is the Dijkstra-era implementation of `getTotalRefundsTxCerts`. It simply folds over the certificates and sums the deposit field embedded in each `UnRegDepositTxCert` / `UnRegDRepTxCert`, with no lookup into the current `CertState`:

```haskell
-- | Unlike previous eras, we no longer need to lookup refunds from the ledger
-- state, since all of the certificates specify the actual refund and ledger
-- rules will validate that they are accurate.
dijkstraTotalRefundsTxCerts = foldMap' $ \case
  UnRegDepositTxCert _ deposit -> deposit
  UnRegDRepTxCert  _ deposit -> deposit
  _ -> zero
```

The comment asserts that "ledger rules will validate that they are accurate," but as shown below, that validation is absent for sub-transactions. [1](#0-0) 

The type-class binding makes this the sole implementation used for all Dijkstra-era refund accounting:

```haskell
getTotalRefundsTxCerts _ _ _ = dijkstraTotalRefundsTxCerts
``` [2](#0-1) 

**Root cause 2 — `SUBLEDGERS` threads `certState` sequentially but `SUBUTXO` uses the pre-cert-update state**

`dijkstraSubLedgersTransition` processes sub-transactions with `foldM`, so the `LedgerState` (including `certState`) is updated after each sub-transaction: [3](#0-2) 

Inside each `SUBLEDGER` transition, `SUBENTITIES` is called first (updating `certState`), but `SUBUTXOW` is then called with the **original** `certState` from the start of that sub-transaction — not the post-`SUBENTITIES` state: [4](#0-3) 

`SUBUTXOW` → `SUBUTXO` calls `updateUTxOStateNoFees`, which calls `certsTotalRefundsTxBody`. For Dijkstra era this resolves to `dijkstraTotalRefundsTxCerts`, which ignores `certState` entirely and reads the deposit amount directly from the cert field: [5](#0-4) 

**Root cause 3 — No predicate failure for unregistering an already-unregistered credential in sub-transactions**

`SUBDELEG` reuses `Conway.conwayDelegTransition`. For `ConwayUnRegCert`, the refund-mismatch check is explicitly skipped when `mAccountState = Nothing` (credential not registered): [6](#0-5) 

The developers themselves acknowledge that the predicate failure for the double-unregister case in sub-transactions is not yet implemented, as evidenced by the disabled test: [7](#0-6) 

**Combined effect**

For a credential registered with deposit `D`:

| Step | `certState` | `utxosDeposited` change |
|---|---|---|
| Sub-tx 1 `SUBENTITIES` | credential removed | — |
| Sub-tx 1 `SUBUTXO` | reads `D` from cert (ignores state) | `−D` |
| Sub-tx 2 `SUBENTITIES` | credential already absent; no failure | — |
| Sub-tx 2 `SUBUTXO` | reads `D` from cert (ignores state) | `−D` |

`utxosDeposited` is decremented by `2D` while only `D` was ever deposited. The sub-transaction outputs can absorb the extra `D` ADA, draining it from the deposit pot.

---

### Impact Explanation

The deposit pot (`utxosDeposited`) is decremented by a multiple of the actual deposit paid. ADA is effectively created from nothing in the sub-transaction outputs, violating the preservation-of-value invariant. This constitutes a direct, attacker-controlled modification of deposit refunds outside design parameters, and at sufficient scale can drain the deposit pot entirely, causing `utxosDeposited` to underflow.

**Matched impact**: *Medium — Attacker-controlled transactions modify refunds outside design parameters.* At scale this escalates to *Critical — Direct loss/creation of ADA through an invalid ledger state transition.*

---

### Likelihood Explanation

The Dijkstra era is not yet deployed on mainnet, but the vulnerability exists in the production source code and will be reachable by any unprivileged transaction author once the era activates. No special privileges, keys, or governance majority are required — only the ability to submit a transaction with sub-transactions.

---

### Recommendation

1. **Validate credential registration before crediting refund in `SUBDELEG`**: Add a `StakeKeyNotRegisteredDELEG` (or equivalent) predicate failure when `mAccountState = Nothing` for `ConwayUnRegCert` in the sub-transaction context, so that the second sub-transaction fails at the `SUBENTITIES` stage.

2. **Make `dijkstraTotalRefundsTxCerts` consult the cert state**: Rather than reading the deposit amount blindly from the cert field, look up the actual stored deposit from `certState` (as Conway era does via `lookupStakingDeposit` / `lookupDRepDeposit`). The cert-embedded amount should only be used as a user-supplied hint that is validated against the stored value.

3. **Enable and fix the disabled test**: The test `"Multiple subtransactions cannot get the same refund"` in `eras/dijkstra/impl/testlib/Test/Cardano/Ledger/Dijkstra/Imp/CertSpec.hs` (line 53) explicitly marks this scenario as broken with `error "TODO: predicate failure not yet implemented"`. This test must be completed and enabled before the era is deployed.

---

### Proof of Concept

1. Register staking credential `C` with deposit `D` (e.g., `D = 2_000_000` lovelace).
2. Construct sub-transaction `subTx1` with cert `UnRegDepositTxCert C D` and a valid UTxO input.
3. Construct sub-transaction `subTx2` with cert `UnRegDepositTxCert C D` and a different valid UTxO input.
4. Submit a top-level Dijkstra transaction with `subTransactionsTxBodyL = OMap.fromFoldable [subTx1, subTx2]`.
5. Observe that both sub-transactions succeed: `subTx1` unregisters `C` and credits `D` to its outputs; `subTx2` finds `C` already absent (no predicate failure) and `dijkstraTotalRefundsTxCerts` again returns `D`, crediting a second `D` to its outputs.
6. Net result: `2D` ADA extracted from the deposit pot while only `D` was deposited.

This is directly analogous to the reported EVM vulnerability: just as the attacker there caused the system to misclassify an ETH swap as an ERC-20 swap to avoid fees, here the attacker causes the system to miscount deposit refunds across sub-transactions, extracting ADA that was never deposited.

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

**File:** eras/shelley/impl/src/Cardano/Ledger/Shelley/Rules/Utxo.hs (L618-630)
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
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Deleg.hs (L240-259)
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
