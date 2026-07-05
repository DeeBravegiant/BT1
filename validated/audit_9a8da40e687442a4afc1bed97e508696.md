### Title
`directDeposits` in Dijkstra Sub-Transactions Not Included in Value Conservation Check, Enabling ADA Creation — (`File: eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs`)

---

### Summary

The Dijkstra era introduces a `directDeposits` field in both top-level and sub-transaction bodies. When a sub-transaction is processed, the `SUBENTITIES` rule applies `directDeposits` by adding ADA directly to registered account balances. However, `directDeposits` are never included in the `getProducedDijkstraValue` / `dijkstraSubTxProducedValue` calculation used by the top-level `validateValueNotConservedUTxO` check. As a result, the value conservation equation does not require the submitter to provide UTxO inputs covering the deposited amounts, allowing an unprivileged transaction author to create ADA from nothing.

---

### Finding Description

The Dijkstra era's `EraUTxO` instance sets:

```haskell
getProducedValue = getProducedDijkstraValue
```

`getProducedDijkstraValue` dispatches to `dijkstraSubTxProducedValue` for sub-transaction bodies:

```haskell
dijkstraSubTxProducedValue pp isRegPoolId txBody =
  sumAllValue (txBody ^. outputsTxBodyL)
    <> inject (getTotalDepositsTxBody pp isRegPoolId txBody <> txBody ^. treasuryDonationTxBodyL)
    <> burnedMultiAssets txBody
```

This accounts for outputs, certificate deposits, treasury donations, and burned multi-assets — but **not** `directDeposits`. [1](#0-0) 

The top-level produced value aggregates sub-transaction produced values through `dijkstraProducedValue`:

```haskell
dijkstraProducedValue pp isRegPoolId txBody =
  conwayProducedValue pp isRegPoolId txBody
    <> foldMap'
      (getProducedValue pp isRegPoolId . view bodyTxL)
      (txBody ^. subTransactionsTxBodyL)
``` [2](#0-1) 

Neither `conwayProducedValue` (for the top-level body) nor `dijkstraSubTxProducedValue` (for sub-transaction bodies) includes `directDeposits`. [3](#0-2) 

Meanwhile, the `SUBENTITIES` rule unconditionally applies `directDeposits` to account balances after only checking that the target accounts exist:

```haskell
let directDeposits = tx ^. bodyTxL . directDepositsTxBodyL
    accountsAfterSubCerts = certStateAfterSubCerts ^. certDStateL . accountsL
failOnJust (directDepositsMissingAccounts directDeposits accountsAfterSubCerts) $
  injectFailure . SubDirectDepositsToMissingAccounts
pure $ certStateAfterSubCerts & certDStateL . accountsL %~ applyDirectDeposits directDeposits
``` [4](#0-3) 

`applyDirectDeposits` adds the specified coin amounts to account balances with no further validation: [5](#0-4) 

The top-level `validateValueNotConservedUTxO` check (inherited from Shelley/Babbage) compares `consumed pp certState utxo txBody` against `produced pp certState txBody`. For `DijkstraEra`, `consumed = conwayConsumed` and `getProducedValue = getProducedDijkstraValue`. Since `directDeposits` appear in neither, the check passes even when sub-transaction `directDeposits` are non-zero, and the ADA credited to accounts has no corresponding UTxO debit. [6](#0-5) 

The `SUBUTXO` rule for sub-transactions explicitly has no `ValueNotConservedUTxO` predicate failure and maps that constructor to `error "Impossible"`, confirming sub-transactions carry no independent value conservation check. [7](#0-6) 

---

### Impact Explanation

An attacker can craft a Dijkstra top-level transaction containing a sub-transaction whose `directDeposits` field credits an arbitrary registered account with an arbitrary ADA amount. The top-level value conservation check passes because `directDeposits` are absent from the produced-value sum. The `SUBENTITIES` rule then unconditionally adds that ADA to the account balance. This constitutes **direct creation of ADA through an invalid ledger state transition** — a Critical impact under the allowed scope.

---

### Likelihood Explanation

Any unprivileged transaction author can submit a Dijkstra transaction. No special role, key, or governance threshold is required. The only prerequisite is that the target account address is already registered, which the attacker can arrange by registering their own stake credential. The attack is deterministic and repeatable.

---

### Recommendation

Add `directDeposits` to the produced-value calculation in both `dijkstraSubTxProducedValue` and `conwayProducedValue` (or a Dijkstra-specific override of the top-level produced value). Concretely, `dijkstraSubTxProducedValue` should include:

```haskell
<> inject (fold . unDirectDeposits $ txBody ^. directDepositsTxBodyL)
```

and the top-level Dijkstra produced value should similarly include the top-level `directDeposits` field. This mirrors how `treasuryDonationTxBodyL` is already correctly included on the produced side. [1](#0-0) 

---

### Proof of Concept

1. Register a stake credential so that `attackerAccount` exists in the ledger accounts.
2. Construct a Dijkstra top-level transaction containing one sub-transaction:
   - Sub-transaction inputs: a single UTxO covering only the sub-transaction outputs and any certificate deposits (no extra ADA for the direct deposit).
   - Sub-transaction `directDeposits`: `{ attackerAccount: 1_000_000_000 }` (1000 ADA).
   - Sub-transaction outputs: return change to self.
3. Submit the transaction. `validateValueNotConservedUTxO` at the top level computes:
   - `consumed` = UTxO inputs (small amount)
   - `produced` = outputs + fee + cert deposits + treasury donation (same small amount, no directDeposits term)
   - Check passes.
4. `SUBENTITIES` applies `directDeposits`, adding 1000 ADA to `attackerAccount`.
5. Verify `attackerAccount` balance increased by 1000 ADA while total UTxO decreased by only the fee — net ADA in the system has increased by 1000 ADA.

### Citations

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs (L93-106)
```haskell
dijkstraProducedValue ::
  ( DijkstraEraTxBody era
  , EraUTxO era
  , Value era ~ MaryValue
  ) =>
  PParams era ->
  (KeyHash StakePool -> Bool) ->
  TxBody TopTx era ->
  MaryValue
dijkstraProducedValue pp isRegPoolId txBody =
  conwayProducedValue pp isRegPoolId txBody
    <> foldMap'
      (getProducedValue pp isRegPoolId . view bodyTxL)
      (txBody ^. subTransactionsTxBodyL)
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs (L252-261)
```haskell
dijkstraSubTxProducedValue ::
  (ConwayEraTxBody era, Value era ~ MaryValue) =>
  PParams era ->
  (KeyHash StakePool -> Bool) ->
  TxBody SubTx era ->
  Value era
dijkstraSubTxProducedValue pp isRegPoolId txBody =
  sumAllValue (txBody ^. outputsTxBodyL)
    <> inject (getTotalDepositsTxBody pp isRegPoolId txBody <> txBody ^. treasuryDonationTxBodyL)
    <> burnedMultiAssets txBody
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/UTxO.hs (L121-131)
```haskell
conwayProducedValue ::
  ( ConwayEraTxBody era
  , Value era ~ MaryValue
  ) =>
  PParams era ->
  (KeyHash StakePool -> Bool) ->
  TxBody TopTx era ->
  Value era
conwayProducedValue pp isStakePool txBody =
  getProducedMaryValue pp isStakePool txBody
    <+> inject (txBody ^. treasuryDonationTxBodyL)
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubEntities.hs (L182-187)
```haskell
  let directDeposits = tx ^. bodyTxL . directDepositsTxBodyL
      accountsAfterSubCerts = certStateAfterSubCerts ^. certDStateL . accountsL
  failOnJust (directDepositsMissingAccounts directDeposits accountsAfterSubCerts) $
    injectFailure . SubDirectDepositsToMissingAccounts

  pure $ certStateAfterSubCerts & certDStateL . accountsL %~ applyDirectDeposits directDeposits
```

**File:** libs/cardano-ledger-core/src/Cardano/Ledger/State/Account.hs (L290-298)
```haskell
applyDirectDeposits ::
  EraAccounts era =>
  DirectDeposits ->
  Accounts era ->
  Accounts era
applyDirectDeposits (DirectDeposits dd) =
  updateAccountBalances
    (\amount account -> addCompactCoin amount (account ^. balanceAccountStateL))
    dd
```

**File:** eras/shelley/impl/src/Cardano/Ledger/Shelley/Rules/Utxo.hs (L503-518)
```haskell
-- | Ensure that value consumed and produced matches up exactly
--
-- > consumed pp utxo txb = produced pp poolParams txb
validateValueNotConservedUTxO ::
  (EraUTxO era, EraCertState era) =>
  PParams era ->
  UTxO era ->
  CertState era ->
  TxBody TopTx era ->
  Test (ShelleyUtxoPredFailure era)
validateValueNotConservedUTxO pp utxo certState txBody =
  failureUnless (consumedValue == producedValue) $
    ValueNotConservedUTxO Mismatch {mismatchSupplied = consumedValue, mismatchExpected = producedValue}
  where
    consumedValue = consumed pp certState utxo txBody
    producedValue = produced pp certState txBody
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubUtxo.hs (L333-333)
```haskell
  ValueNotConservedUTxO _ -> error "Impossible: `ValueNotConservedUTxO` for SUBUTXO"
```
