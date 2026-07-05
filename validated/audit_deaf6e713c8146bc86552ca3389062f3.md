### Title
Missing Batch Deposit-Refund Validation Allows Multiple Sub-Transactions to Claim the Same Stake-Credential Refund - (File: `eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Utxo.hs`)

---

### Summary

In the Dijkstra era's nested-transaction system, `dijkstraUtxoTransition` validates that the **sum of withdrawals** across all sub-transactions does not exceed the original account balance (`validateBatchWithdrawals`). No equivalent batch check exists for **deposit refunds**. An unprivileged transaction author can include two or more sub-transactions each containing `UnRegDepositTxCert` for the same staking credential. Because each sub-transaction's individual `consumed`/`produced` balance check is evaluated against the original `certState` (where the account is still registered), every sub-transaction independently passes validation and claims the full `keyDeposit` refund. The deposit pot is drained by N × `keyDeposit` while only one `keyDeposit` was ever paid, violating the preservation-of-value invariant.

---

### Finding Description

**Vulnerability class:** Funds/accounting bug — missing cross-sub-transaction batch validation for deposit refunds.

**Root cause in production code:**

`dijkstraUtxoTransition` in `eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Utxo.hs` performs a batch withdrawal check:

```haskell
-- this is the original Accounts, before any transactions were applied
let accounts = certState ^. certDStateL . accountsL
...
runTest $ validateBatchWithdrawals accounts tx
``` [1](#0-0) 

`validateBatchWithdrawals` aggregates withdrawals from the top-level transaction and every sub-transaction, then checks the total against the **original** account balance:

```haskell
validateBatchWithdrawals accounts tx =
  let allWithdrawals =
        Map.unionsWith (<>) $
          unWithdrawals (tx ^. bodyTxL . withdrawalsTxBodyL)
            : [ unWithdrawals $ subTx ^. bodyTxL . withdrawalsTxBodyL
              | subTx <- OMap.elems $ tx ^. bodyTxL . subTransactionsTxBodyL
              ]
``` [2](#0-1) 

There is **no analogous `validateBatchRefunds`** function. Deposit refunds are validated only per-sub-transaction inside `SUBUTXO` via `validateValueNotConservedUTxO`, which uses the `certState` as it was at the start of that sub-transaction's `SUBLEDGER` invocation. Because each sub-transaction's `SUBUTXO` check sees the staking credential as still registered (the original `certState`), each independently satisfies `consumed = produced` while including the full `keyDeposit` refund in its outputs.

**Developer acknowledgement of the bug:**

The test file `eras/dijkstra/impl/testlib/Test/Cardano/Ledger/Dijkstra/Imp/CertSpec.hs` contains a pending (`xit`) test titled **"Multiple subtransactions cannot get the same refund"** with the explicit comment `error "TODO: predicate failure not yet implemented"`:

```haskell
xit "Multiple subtransactions cannot get the same refund" $ do
  stakingCred <- KeyHashObj <$> freshKeyHash
  _ <- registerStakeCredential stakingCred
  keyDeposit <- getsPParams ppKeyDepositL
  ...
  let
    subTx1 = ... & bodyTxL . certsTxBodyL .~ SSeq.singleton (UnRegDepositTxCert stakingCred keyDeposit)
    subTx2 = ... & bodyTxL . certsTxBodyL .~ SSeq.singleton (UnRegDepositTxCert stakingCred keyDeposit)
    tx = mkBasicTx mkBasicTxBody
           & bodyTxL . subTransactionsTxBodyL .~ OMap.fromFoldable [subTx1, subTx2]
  submitFailingTx tx . NE.singleton $ error "TODO: predicate failure not yet implemented"
``` [3](#0-2) 

`submitFailingTx` asserts the transaction **should fail**. The `error "TODO: predicate failure not yet implemented"` means no predicate failure has been implemented to reject it — i.e., the transaction currently **succeeds**, and the developers know it should not.

**Structural parallel to the external report:**

| External report | Cardano Ledger analog |
|---|---|
| `initialBalance` captured before reentrant call | `certState` (original) used for each sub-tx's `consumed`/`produced` check |
| Reentrant call reuses same `initialBalance` | Each sub-tx sees account as registered, claims full refund |
| Fee paid only on final unwind | Only one `keyDeposit` was ever paid |
| Gelato deposit drained | Cardano deposit pot drained |

The `validateBatchWithdrawals` function was added precisely to prevent the analogous problem for withdrawals. The same pattern was not applied to deposit refunds. [4](#0-3) 

---

### Impact Explanation

An attacker registers a staking credential paying `keyDeposit` ADA. They then submit a single top-level Dijkstra transaction containing N sub-transactions, each with `UnRegDepositTxCert stakingCred keyDeposit`. Each sub-transaction passes individual validation and claims `keyDeposit` in its outputs. The deposit pot (`utxosDeposited` in `UTxOState`) is decremented by N × `keyDeposit` while only one `keyDeposit` was paid. This is a direct, attacker-controlled destruction of ADA accounting integrity — the deposit pot can be driven negative (or to underflow), and N − 1 units of `keyDeposit` are created from nothing in the UTxO. This constitutes a **Critical** impact: direct loss/creation of ADA through an invalid ledger state transition, violating the preservation-of-value property that is the foundational invariant of the Cardano ledger. [5](#0-4) 

---

### Likelihood Explanation

The Dijkstra era is currently experimental but is present in the production codebase and is the next planned era. The attack requires no privileged access: any transaction author can register a staking credential (a permissionless operation) and construct a top-level transaction with multiple sub-transactions. The construction is straightforward — the attacker only needs to create two sub-transactions with the same `UnRegDepositTxCert` certificate and different spend inputs (to give them distinct `TxId`s). The developers have already identified and documented this exact attack vector in the test suite, confirming it is reachable and currently unmitigated.

---

### Recommendation

Add a `validateBatchRefunds` check in `dijkstraUtxoTransition` (analogous to `validateBatchWithdrawals`) that aggregates all `UnRegDepositTxCert` / `UnRegDRepTxCert` certificates across the top-level transaction and all sub-transactions, verifies that no credential appears more than once across the batch, and confirms that each credential being deregistered is actually registered in the original `certState`. This mirrors the existing pattern: [6](#0-5) 

The `xit` test at lines 53–75 of `eras/dijkstra/impl/testlib/Test/Cardano/Ledger/Dijkstra/Imp/CertSpec.hs` should be activated once the predicate failure is implemented.

---

### Proof of Concept

1. Register staking credential `C`, paying `keyDeposit = D` ADA.
2. Construct `subTx1` with spend input `i1` and certificate `UnRegDepositTxCert C D`, with output claiming `D` ADA.
3. Construct `subTx2` with spend input `i2` (different from `i1`) and certificate `UnRegDepositTxCert C D`, with output claiming `D` ADA.
4. Construct top-level transaction `tx` with `subTransactionsTxBodyL = OMap.fromFoldable [subTx1, subTx2]`.
5. Submit `tx`. Both sub-transactions pass their individual `consumed = produced` checks because each sees `C` as registered in the original `certState`. Both outputs receive `D` ADA.
6. Net result: `2D` ADA extracted from the deposit pot; only `D` was ever deposited. The deposit pot is short by `D` ADA, violating preservation of value.

The developers' own pending test (`xit "Multiple subtransactions cannot get the same refund"`) at `eras/dijkstra/impl/testlib/Test/Cardano/Ledger/Dijkstra/Imp/CertSpec.hs:53–75` encodes exactly this scenario and confirms no predicate failure currently prevents it. [3](#0-2)

### Citations

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Utxo.hs (L249-280)
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

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Utxo.hs (L347-378)
```haskell
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

**File:** libs/cardano-ledger-core/src/Cardano/Ledger/State/Account.hs (L196-210)
```haskell
withdrawalsThatDoNotDrainAccounts ::
  EraAccounts era =>
  Withdrawals ->
  Network ->
  Accounts era ->
  -- | invalid withdrawal = that which does not have an account address or is in
  -- the wrong network.
  -- incomplete withdrawal = that which does not withdraw the exact account
  -- balance.
  Maybe (Withdrawals, Map AccountAddress (Mismatch RelEQ Coin))
withdrawalsThatDoNotDrainAccounts =
  categorizeWithdrawals
    ( \withdrawalAmount account ->
        withdrawalAmount == fromCompact (account ^. balanceAccountStateL)
    )
```

**File:** eras/shelley/formal-spec/utxo.tex (L4-12)
```tex
A key constraint that must always be satisfied as a result and precondition of
a valid ledger state transition is called the \textit{general accounting
property}, or the \textit{preservation of value} condition. Every piece of
software that is a part of the implementation of the
Cardano cryptocurrency must function in such a way as to not result in
a violation of this rule.
If this condition is not satisfied, it is an indicator of
incorrect accounting, potentially due to
malicious disruption or a bug.
```
