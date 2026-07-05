### Title
Reference Script Fee Not Charged for Sub-Transaction Inputs in Dijkstra Era Batch Transactions - (`File: eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs`)

---

### Summary

In the Dijkstra era, the minimum fee calculation for a batch transaction (top-level transaction + sub-transactions) only accounts for reference scripts referenced by the **top-level** transaction's inputs. Reference scripts referenced by **sub-transaction** inputs are deserialized and validated by nodes but are not included in the tiered reference script fee (`tierRefScriptFee`). An unprivileged transaction author can exploit this to pay significantly less than the intended minimum fee while still imposing the full deserialization cost on the network.

---

### Finding Description

The Dijkstra era introduces nested/batch transactions: a top-level `Tx TopTx era` may embed multiple sub-transactions via `subTransactionsTxBodyL`. Each sub-transaction has its own `inputsTxBodyL` and `referenceInputsTxBodyL`, which may point to UTxO entries containing reference scripts.

The codebase defines two distinct functions for measuring total reference script size in a batch:

**1. `batchNonDistinctRefScriptsSize`** — correctly sums reference scripts across the top-level transaction and all sub-transactions:

```haskell
batchNonDistinctRefScriptsSize utxo tx =
  txNonDistinctRefScriptsSize utxo tx
    + getSum
      ( foldMap'
          (Sum . txNonDistinctRefScriptsSize utxo)
          (tx ^. bodyTxL . subTransactionsTxBodyL)
      )
``` [1](#0-0) 

**2. `getConwayMinFeeTxUtxo`** — only counts reference scripts from the top-level transaction's inputs:

```haskell
getConwayMinFeeTxUtxo pparams tx utxo =
  getMinFeeTx pparams tx $ txNonDistinctRefScriptsSize utxo tx
``` [2](#0-1) 

The `EraUTxO DijkstraEra` instance wires `getMinFeeTxUtxo` to `getConwayMinFeeTxUtxo`:

```haskell
instance EraUTxO DijkstraEra where
  ...
  getMinFeeTxUtxo = getConwayMinFeeTxUtxo
``` [3](#0-2) 

The Dijkstra LEDGER rule's **size limit** check correctly uses `batchNonDistinctRefScriptsSize`:

```haskell
validateAllRefScriptSize pp utxo tx =
  let totalRefScriptSize = batchNonDistinctRefScriptsSize utxo tx
      maxRefScriptSizePerTx = fromIntegral @Word32 @Int $ pp ^. ppMaxRefScriptSizePerTxG
   in failureUnless (totalRefScriptSize <= maxRefScriptSizePerTx) $ ...
``` [4](#0-3) 

But the **fee enforcement** path (`feesOK` → `getMinFeeTxUtxo`) uses only `txNonDistinctRefScriptsSize` on the top-level transaction, completely ignoring sub-transaction reference scripts.

The split is directly analogous to the NibblVault bug: the boundary between the top-level transaction domain and the sub-transaction domain is crossed, but the fee is only computed for one side of that boundary.

---

### Impact Explanation

The tiered reference script fee (`tierRefScriptFee`) was introduced specifically to deter DDoS attacks where large scripts impose high deserialization costs on nodes. The fee grows exponentially with total reference script size. [5](#0-4) 

By placing large reference scripts exclusively in sub-transaction inputs, an attacker can:

- Submit a top-level transaction with zero reference script bytes → pays only the base Alonzo fee.
- Embed sub-transactions whose inputs reference UTxO entries containing large Plutus scripts (up to the `maxRefScriptSizePerTx` batch limit of 204,800 bytes).
- Nodes must deserialize all reference scripts (top-level + sub-transactions) during validation, but the fee only covers the top-level portion.

The maximum exploitable gap per transaction is bounded by `maxRefScriptSizePerTx`, but within that bound the attacker pays zero reference script fee for sub-transaction scripts. At the default `minFeeRefScriptCostPerByte = 15` lovelace/byte with the tiered multiplier, 200 KiB of sub-transaction reference scripts would incur a fee shortfall on the order of several ADA per transaction.

This matches the **Medium** allowed impact: *"Attacker-controlled transactions... modify fees... outside design parameters."*

---

### Likelihood Explanation

- Any unprivileged user can construct a Dijkstra batch transaction with sub-transactions referencing UTxO entries that contain large reference scripts.
- No special privileges, governance majority, or key compromise is required.
- The Dijkstra era is the production next era after Conway; the vulnerability is present in the current codebase.
- The attack is cheap to execute repeatedly (low fee per transaction) and imposes disproportionate deserialization cost on all validating nodes.

---

### Recommendation

The `EraUTxO DijkstraEra` instance should override `getMinFeeTxUtxo` to use `batchNonDistinctRefScriptsSize` instead of `txNonDistinctRefScriptsSize`:

```haskell
instance EraUTxO DijkstraEra where
  ...
  getMinFeeTxUtxo pp tx utxo =
    getMinFeeTx pp tx $ batchNonDistinctRefScriptsSize utxo tx
```

This makes the fee calculation consistent with the size limit check already implemented in `validateAllRefScriptSize`, ensuring that reference scripts in sub-transactions are charged for at the same tiered rate as top-level reference scripts.

---

### Proof of Concept

**Setup**: Dijkstra era is active. Protocol parameters: `minFeeRefScriptCostPerByte = 15`, `maxRefScriptSizePerTx = 204800`.

1. Alice creates a UTxO entry `utxo_A` containing a 100 KiB Plutus script as a reference script.
2. Alice creates a UTxO entry `utxo_B` containing another 100 KiB Plutus script as a reference script.
3. Alice constructs a Dijkstra top-level transaction with:
   - Top-level inputs: one ordinary UTxO (no reference script) — contributes 0 bytes to `txNonDistinctRefScriptsSize`.
   - Sub-transaction 1: `referenceInputsTxBodyL = {utxo_A}` — contributes 100 KiB.
   - Sub-transaction 2: `referenceInputsTxBodyL = {utxo_B}` — contributes 100 KiB.
4. `batchNonDistinctRefScriptsSize` = 200 KiB → passes `validateAllRefScriptSize` (≤ 204800).
5. `getMinFeeTxUtxo` = `getConwayMinFeeTxUtxo` = `getMinFeeTx pp tx 0` → reference script fee = `tierRefScriptFee ... 0` = **0 lovelace**.
6. Alice pays only the base linear fee (`a * txSize + b`), with zero reference script surcharge, despite nodes deserializing 200 KiB of Plutus scripts.

The correct fee for 200 KiB at default parameters would be approximately `tierRefScriptFee 1.2 25600 15 204800` ≈ several ADA, which Alice avoids entirely. [1](#0-0) [3](#0-2) [6](#0-5) [4](#0-3) [7](#0-6)

### Citations

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs (L124-141)
```haskell
instance EraUTxO DijkstraEra where
  type ScriptsNeeded DijkstraEra = AlonzoScriptsNeeded DijkstraEra

  consumed = conwayConsumed

  getConsumedValue = getConsumedDijkstraValue

  getProducedValue = getProducedDijkstraValue

  getScriptsProvided = getDijkstraScriptsProvided

  getScriptsNeeded = getDijkstraScriptsNeeded

  getScriptsHashesNeeded = getAlonzoScriptsHashesNeeded

  getWitsVKeyNeeded _ = getConwayWitsVKeyNeeded

  getMinFeeTxUtxo = getConwayMinFeeTxUtxo
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs (L264-277)
```haskell
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

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/UTxO.hs (L166-187)
```haskell
getConwayMinFeeTxUtxo ::
  ( EraTx era
  , BabbageEraTxBody era
  ) =>
  PParams era ->
  Tx l era ->
  UTxO era ->
  Coin
getConwayMinFeeTxUtxo pparams tx utxo =
  getMinFeeTx pparams tx $ txNonDistinctRefScriptsSize utxo tx

-- | Calculate the total size of reference scripts used by the transactions. Duplicate
-- scripts will be counted as many times as they occur, since there is never a reason to
-- include an input with the same reference script.
--
-- Any input that appears in both regular inputs and reference inputs of a transaction is
-- only used once in this computation.
txNonDistinctRefScriptsSize :: (EraTx era, BabbageEraTxBody era) => UTxO era -> Tx l era -> Int
txNonDistinctRefScriptsSize utxo tx = getSum $ foldMap (Sum . originalBytesSize . snd) refScripts
  where
    inputs = (tx ^. bodyTxL . referenceInputsTxBodyL) `Set.union` (tx ^. bodyTxL . inputsTxBodyL)
    refScripts = getReferenceScriptsNonDistinct utxo inputs
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Ledger.hs (L313-329)
```haskell
validateAllRefScriptSize ::
  ( EraTx era
  , DijkstraEraTxBody era
  ) =>
  PParams era ->
  UTxO era ->
  Tx TopTx era ->
  Test (DijkstraLedgerPredFailure era)
validateAllRefScriptSize pp utxo tx =
  let totalRefScriptSize = batchNonDistinctRefScriptsSize utxo tx
      maxRefScriptSizePerTx = fromIntegral @Word32 @Int $ pp ^. ppMaxRefScriptSizePerTxG
   in failureUnless (totalRefScriptSize <= maxRefScriptSizePerTx) $
        DijkstraTxRefScriptsSizeTooBig
          Mismatch
            { mismatchSupplied = totalRefScriptSize
            , mismatchExpected = maxRefScriptSizePerTx
            }
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Tx.hs (L103-112)
```haskell
getConwayMinFeeTx pp tx refScriptsSize =
  alonzoMinFeeTx pp tx <+> refScriptsFee
  where
    refScriptCostPerByte = unboundRational (pp ^. ppMinFeeRefScriptCostPerByteL)
    refScriptsFee =
      tierRefScriptFee
        (unboundRational $ pp ^. ppRefScriptCostMultiplierG)
        (fromIntegral @Word32 @Int . unNonZero $ pp ^. ppRefScriptCostStrideG)
        refScriptCostPerByte
        refScriptsSize
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Tx.hs (L127-136)
```haskell
tierRefScriptFee multiplier sizeIncrement
  | multiplier <= 0 || sizeIncrement <= 0 = error "Size increment and multiplier must be positive"
  | otherwise = go 0
  where
    go !acc !curTierPrice !n
      | n < sizeIncrement =
          Coin $ floor (acc + toRational n * curTierPrice)
      | otherwise =
          go (acc + sizeIncrementRational * curTierPrice) (multiplier * curTierPrice) (n - sizeIncrement)
    sizeIncrementRational = toRational sizeIncrement
```
