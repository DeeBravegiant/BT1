### Title
Dijkstra Era `getMinFeeTxUtxo` Omits Sub-Transaction Reference Script Sizes from Minimum Fee Calculation - (`File: eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs`)

---

### Summary

The `DijkstraEra` instance of `getMinFeeTxUtxo` reuses `getConwayMinFeeTxUtxo`, which only measures reference script sizes from the **top-level transaction**. Sub-transactions embedded in a Dijkstra batch transaction can carry their own reference inputs pointing to UTxO entries with large Plutus scripts, but those scripts are never counted in the minimum fee. A transaction sender can therefore include arbitrarily large reference scripts in sub-transactions and pay only the fee for the top-level transaction's scripts, bypassing the tiered reference-script fee mechanism that was introduced specifically to prevent DDoS attacks.

---

### Finding Description

The `EraUTxO DijkstraEra` instance in `eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs` delegates minimum-fee computation to `getConwayMinFeeTxUtxo`:

```haskell
instance EraUTxO DijkstraEra where
  ...
  getMinFeeTxUtxo = getConwayMinFeeTxUtxo   -- line 141
```

`getConwayMinFeeTxUtxo` is defined in `eras/conway/impl/src/Cardano/Ledger/Conway/UTxO.hs`:

```haskell
getConwayMinFeeTxUtxo pparams tx utxo =
  getMinFeeTx pparams tx $ txNonDistinctRefScriptsSize utxo tx   -- line 174-175
```

`txNonDistinctRefScriptsSize` only unions the **top-level** transaction's regular inputs and reference inputs:

```haskell
txNonDistinctRefScriptsSize utxo tx = getSum $ foldMap (Sum . originalBytesSize . snd) refScripts
  where
    inputs = (tx ^. bodyTxL . referenceInputsTxBodyL) `Set.union` (tx ^. bodyTxL . inputsTxBodyL)
    refScripts = getReferenceScriptsNonDistinct utxo inputs   -- line 183-187
```

The Dijkstra era introduces sub-transactions (`subTransactionsTxBodyL`), each of which can independently carry `referenceInputsTxBodyL` pointing to UTxO entries with large scripts. The codebase already provides a correct aggregation function:

```haskell
batchNonDistinctRefScriptsSize utxo tx =
  txNonDistinctRefScriptsSize utxo tx
    + getSum
      ( foldMap'
          (Sum . txNonDistinctRefScriptsSize utxo)
          (tx ^. bodyTxL . subTransactionsTxBodyL)   -- lines 271-277
      )
```

However, `getMinFeeTxUtxo` for `DijkstraEra` never calls `batchNonDistinctRefScriptsSize`; it calls `getConwayMinFeeTxUtxo` which calls `txNonDistinctRefScriptsSize` (top-level only). The reference script sizes from all sub-transactions are therefore excluded from the minimum fee check.

The `dijkstraProducedValue` and `getConsumedDijkstraValue` functions correctly aggregate sub-transaction value, confirming that sub-transactions are fully processed by the ledger — only the fee measurement is incomplete.

---

### Impact Explanation

The tiered reference-script fee (`tierRefScriptFee`) was introduced after a real DDoS attack on June 25, 2024 (documented in `docs/adr/2024-08-14_009-refscripts-fee-change.md`) to ensure that large scripts are expensive to include. By placing large reference scripts exclusively in sub-transactions, an attacker pays only the top-level fee while forcing every validating node to deserialize and process all sub-transaction scripts. This modifies fees outside design parameters and can be used to submit transactions that are disproportionately expensive to validate relative to their fee, matching the **Medium** allowed impact: *"Attacker-controlled transactions... exceed intended validation limits or modify fees... outside design parameters."*

---

### Likelihood Explanation

Any unprivileged transaction sender on the Dijkstra era can craft a top-level transaction with sub-transactions that reference large Plutus scripts. No special privilege, key, or governance action is required. The attacker only needs to create UTxO entries containing large scripts (which is a normal, permissionless operation) and then reference them from sub-transactions. The fee shortfall scales linearly with the number and size of sub-transaction reference scripts.

---

### Recommendation

Replace the `getMinFeeTxUtxo` implementation for `DijkstraEra` with one that uses `batchNonDistinctRefScriptsSize` instead of `txNonDistinctRefScriptsSize`:

```haskell
getDijkstraMinFeeTxUtxo :: (EraTx era, DijkstraEraTxBody era) => PParams era -> Tx l era -> UTxO era -> Coin
getDijkstraMinFeeTxUtxo pparams tx utxo =
  getMinFeeTx pparams tx $ batchNonDistinctRefScriptsSize utxo tx

instance EraUTxO DijkstraEra where
  ...
  getMinFeeTxUtxo = getDijkstraMinFeeTxUtxo
```

This ensures that reference scripts from all sub-transactions are included in the minimum fee calculation, consistent with how `dijkstraProducedValue` and `getConsumedDijkstraValue` already aggregate sub-transaction data.

---

### Proof of Concept

**Root cause chain:**

1. `EraUTxO DijkstraEra` sets `getMinFeeTxUtxo = getConwayMinFeeTxUtxo` [1](#0-0) 

2. `getConwayMinFeeTxUtxo` passes only `txNonDistinctRefScriptsSize utxo tx` (top-level inputs only) to `getMinFeeTx`: [2](#0-1) 

3. `txNonDistinctRefScriptsSize` unions only the top-level transaction's inputs and reference inputs — sub-transactions are not visited: [3](#0-2) 

4. `batchNonDistinctRefScriptsSize` exists and correctly sums sub-transaction reference script sizes, but is never called from `getMinFeeTxUtxo`: [4](#0-3) 

5. Sub-transactions are fully processed by the ledger (their consumed/produced values are aggregated), confirming the scripts are validated but not priced: [5](#0-4) [6](#0-5) 

6. The ADR confirms the reference-script fee is a security-critical anti-DDoS measure: [7](#0-6)

### Citations

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs (L88-91)
```haskell
    subTransactionsConsumedValue topTxBody =
      foldMap'
        (getConsumedValue pp lookupStakingDeposit lookupDRepDeposit utxo . view bodyTxL)
        (topTxBody ^. subTransactionsTxBodyL)
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs (L102-106)
```haskell
dijkstraProducedValue pp isRegPoolId txBody =
  conwayProducedValue pp isRegPoolId txBody
    <> foldMap'
      (getProducedValue pp isRegPoolId . view bodyTxL)
      (txBody ^. subTransactionsTxBodyL)
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs (L141-141)
```haskell
  getMinFeeTxUtxo = getConwayMinFeeTxUtxo
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs (L263-277)
```haskell
-- | Total size of reference scripts across a top-level transaction and all its subtransactions.
batchNonDistinctRefScriptsSize ::
  ( EraTx era
  , DijkstraEraTxBody era
  ) =>
  UTxO era ->
  Tx TopTx era ->
  Int
batchNonDistinctRefScriptsSize utxo tx =
  txNonDistinctRefScriptsSize utxo tx
    + getSum
      ( foldMap'
          (Sum . txNonDistinctRefScriptsSize utxo)
          (tx ^. bodyTxL . subTransactionsTxBodyL)
      )
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/UTxO.hs (L174-175)
```haskell
getConwayMinFeeTxUtxo pparams tx utxo =
  getMinFeeTx pparams tx $ txNonDistinctRefScriptsSize utxo tx
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/UTxO.hs (L183-187)
```haskell
txNonDistinctRefScriptsSize :: (EraTx era, BabbageEraTxBody era) => UTxO era -> Tx l era -> Int
txNonDistinctRefScriptsSize utxo tx = getSum $ foldMap (Sum . originalBytesSize . snd) refScripts
  where
    inputs = (tx ^. bodyTxL . referenceInputsTxBodyL) `Set.union` (tx ^. bodyTxL . inputsTxBodyL)
    refScripts = getReferenceScriptsNonDistinct utxo inputs
```

**File:** docs/adr/2024-08-14_009-refscripts-fee-change.md (L15-19)
```markdown
It was identified a while ago that there is an overhead associated with deserializing scripts, so much so that it would be possible to create a very large script that was fairly expensive to deserialize, but very cheap to execute. This opened up an attack vector when such a Plutus script would be used as a reference script. This problem was exacerbated by the fact that there was no real limit on the total size of reference scripts that could be used in a transaction, thus being limited only by the size of the transaction itself. Therefore this opened up Cardano to a DDoS attack where an attacker could submit many such transactions that would cost very little, but would be expensive for a `cardano-node` to validate.

In order to prevent such an attack a `"minFeeRefScriptCostPerByte"` protocol parameter was introduced in the Conway era. The idea was fairly simple: we would calculate the total size of reference scripts used by a transaction and multiply it by the value specified by this protocol parameter. Result would be added to the transaction fee. However, in order for this approach to be a definite deterrent of such attacks this parameter would have to be set to a fairly high value. Community was not very keen on having this value set to a high enough value, since that could make reference scripts almost as expensive to use as regular scripts, thus significantly reducing their usability. Taking this fact into consideration, a decision was made to set this parameter to a fairly moderate value to at l ... (truncated)

Unfortunately things did not go exactly as planned, because on [June 25th 2024 an attack like this actually took place](https://cardanospot.io/news/ddos-attack-on-the-cardano-blockchain-mRIKAzZTNnzq5NGd). This attack forced us to make a quick decision on implementing a definite prevention of such attacks, while trying to not have a drastic impact on the common use case that DApp developers rely on so much.
```
