### Title
Direct Deposits Not Included in Produced-Value Accounting Allows ADA Creation Out of Thin Air — (`File: eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs`)

---

### Summary

The Dijkstra era introduces a `directDeposits` field in both top-level and sub-transaction bodies that allows a transaction to add ADA directly to registered account balances via `applyDirectDeposits`. However, the `directDeposits` amount is never included in the `produced` side of the UTxO preservation-of-value check (`consumed == produced`). As a result, a transaction can credit arbitrary ADA to account balances without providing the corresponding ADA in UTxO inputs, violating the fundamental preservation-of-value invariant and creating ADA out of thin air.

---

### Finding Description

The Dijkstra era adds `dtbrDirectDeposits :: !DirectDeposits` to `DijkstraTxBodyRaw` for both `TopTx` and `SubTx` levels. [1](#0-0) [2](#0-1) 

These direct deposits are applied to account balances in the `ENTITIES` and `SUBENTITIES` transition rules: [3](#0-2) [4](#0-3) 

`applyDirectDeposits` unconditionally adds each deposit amount to the matching account balance: [5](#0-4) 

However, the UTxO preservation-of-value check (`consumed == produced`) never accounts for direct deposits. The `getConsumedDijkstraValue` function aggregates UTxO inputs, withdrawals, and deposit refunds — but not direct deposits: [6](#0-5) 

The `dijkstraProducedValue` function aggregates outputs, fees, certificate deposits, and treasury donations — but not direct deposits: [7](#0-6) 

The sub-transaction produced value likewise omits direct deposits: [8](#0-7) 

Compare with `treasuryDonationTxBodyL`, which **is** correctly included in `produced` (line 260 above). Direct deposits are structurally identical in purpose — ADA leaving the UTxO and entering another pot — yet they are absent from the balance equation.

The `validateValueNotConservedUTxO` check enforces `consumed == produced`: [9](#0-8) 

Because direct deposits appear on neither side of this equation, the check passes regardless of how large the `directDeposits` map is. The ADA credited to account balances is never deducted from UTxO inputs.

The Cardano ledger's preservation-of-value property requires that the sum of all pots (UTxO + deposits + fees + rewards + treasury + reserves) remains constant: [10](#0-9) 

Direct deposits increase the rewards/accounts pot without a corresponding decrease in the UTxO pot, breaking this invariant.

---

### Impact Explanation

**Critical — Direct creation of ADA through an invalid ledger state transition.**

An unprivileged transaction sender can include a `directDeposits` map crediting any registered account address with an arbitrary `Coin` amount. Because the UTxO balance check does not require the sender to provide the corresponding ADA in inputs, the transaction is accepted and the target accounts receive ADA that was never deducted from any existing pot. This is unbounded ADA minting: the attacker can repeat the attack across multiple transactions and epochs, inflating the total ADA supply without limit.

---

### Likelihood Explanation

Any transaction sender in the Dijkstra era can craft a transaction with a non-empty `directDeposits` field targeting any registered account. No special privilege, key, or governance threshold is required. The only prerequisite is that the target account address is registered (which is publicly observable on-chain). The attack is deterministic and reproducible.

---

### Recommendation

Include the total direct-deposit amount in the `produced` side of the UTxO balance equation, analogous to how `treasuryDonationTxBodyL` is handled. Concretely, `dijkstraProducedValue` and `dijkstraSubTxProducedValue` should add `inject (fold (unDirectDeposits (txBody ^. directDepositsTxBodyL)))` to the produced value. This ensures that the ADA credited to account balances must be provided by the transaction's UTxO inputs, preserving the full ADA conservation invariant.

---

### Proof of Concept

1. Register a stake credential `cred` and obtain its `AccountAddress`.
2. Construct a Dijkstra `TopTx` with:
   - A single UTxO input worth, say, 2 ADA (enough to cover the fee).
   - A single output returning ~1.8 ADA to the sender (fee ~0.2 ADA).
   - `directDeposits = { AccountAddress(cred) → 1_000_000_000_000 }` (1 million ADA).
3. Submit the transaction. The `consumed == produced` check passes because direct deposits appear on neither side.
4. Observe that `cred`'s account balance has increased by 1,000,000,000,000 lovelace, while the UTxO decreased only by the fee (~0.2 ADA). Total ADA in the system has increased by ~999,999.8 ADA.

The relevant accounting gap is confirmed by comparing `dijkstraProducedValue` (which includes `treasuryDonationTxBodyL` but not `directDepositsTxBodyL`) against `applyDirectDeposits` (which unconditionally credits account balances). [8](#0-7) [5](#0-4)

### Citations

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxBody.hs (L184-186)
```haskell
    , dtbrSubTransactions :: !(OMap TxId (Tx SubTx era))
    , dtbrDirectDeposits :: !DirectDeposits
    , dtbrAccountBalanceIntervals :: !(AccountBalanceIntervals era)
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxBody.hs (L205-207)
```haskell
    , dstbrRequiredTopLevelGuards :: !(Map (Credential Guard) (StrictMaybe (Data era)))
    , dstbrDirectDeposits :: !DirectDeposits
    , dstbrAccountBalanceIntervals :: !(AccountBalanceIntervals era)
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Entities.hs (L211-216)
```haskell
  let directDeposits = tx ^. bodyTxL . directDepositsTxBodyL
      accountsAfterCerts = certStateAfterCerts ^. certDStateL . accountsL
  failOnJust (directDepositsMissingAccounts directDeposits accountsAfterCerts) $
    injectFailure . DirectDepositsToMissingAccounts

  pure $ certStateAfterCerts & certDStateL . accountsL %~ applyDirectDeposits directDeposits
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

**File:** eras/shelley/formal-spec/Properties.md (L10-22)
```markdown
# Preservation of Value

Recall that there are six pots of money in the Shelley ledger:

* Circulation (total value of the UTxO)
* Deposits
* Fees
* Rewards (total value of the reward accounts)
* Reserves
* Treasury

For each transition system, we will list what pots are in scope,
describe how value moves between the pots,
```
