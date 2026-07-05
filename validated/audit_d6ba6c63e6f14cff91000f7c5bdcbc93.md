### Title
Dijkstra Era `getMinFeeTxUtxo` Omits Sub-Transaction Reference Script Sizes from Fee Calculation - (File: `eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs`)

---

### Summary

The `EraUTxO DijkstraEra` instance delegates `getMinFeeTxUtxo` to the Conway-era function `getConwayMinFeeTxUtxo`, which only measures reference-script bytes from the **top-level transaction**. In the Dijkstra era a `TopTx` may embed an arbitrary number of sub-transactions (`dtbrSubTransactions`), each of which can carry its own reference inputs pointing to large Plutus scripts. Those sub-transaction reference scripts are never included in the fee calculation, so an attacker can submit a batch whose actual deserialization cost far exceeds the fee charged.

---

### Finding Description

The Dijkstra era introduces nested transactions. A `DijkstraTxBodyRaw TopTx` contains a field `dtbrSubTransactions :: OMap TxId (Tx SubTx era)`, and each sub-transaction body (`DijkstraSubTxBodyRaw`) has its own `dstbrReferenceInputs :: Set TxIn`.

The codebase already provides a correct aggregate helper:

```haskell
-- eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs
batchNonDistinctRefScriptsSize utxo tx =
  txNonDistinctRefScriptsSize utxo tx          -- top-level tx
    + getSum
      ( foldMap'
          (Sum . txNonDistinctRefScriptsSize utxo)
          (tx ^. bodyTxL . subTransactionsTxBodyL)  -- each sub-tx
      )
```

However, the `EraUTxO DijkstraEra` instance wires `getMinFeeTxUtxo` to the Conway function, which only calls the single-level helper:

```haskell
-- eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs  line 141
getMinFeeTxUtxo = getConwayMinFeeTxUtxo
```

```haskell
-- eras/conway/impl/src/Cardano/Ledger/Conway/UTxO.hs  lines 174-175
getConwayMinFeeTxUtxo pparams tx utxo =
  getMinFeeTx pparams tx $ txNonDistinctRefScriptsSize utxo tx
```

`txNonDistinctRefScriptsSize` only unions the top-level `referenceInputsTxBodyL` and `inputsTxBodyL`:

```haskell
-- eras/conway/impl/src/Cardano/Ledger/Conway/UTxO.hs  lines 183-187
txNonDistinctRefScriptsSize utxo tx = getSum $ foldMap (Sum . originalBytesSize . snd) refScripts
  where
    inputs = (tx ^. bodyTxL . referenceInputsTxBodyL) `Set.union` (tx ^. bodyTxL . inputsTxBodyL)
    refScripts = getReferenceScriptsNonDistinct utxo inputs
```

Sub-transaction reference inputs are never visited. The `feesOK` predicate in the UTXO rule calls `getMinFeeTxUtxo`, so the enforced minimum fee is computed without the sub-transaction reference-script component. The `tierRefScriptFee` surcharge — the exponentially-growing term introduced specifically to deter large-script DDoS — is therefore zero for all reference scripts that live exclusively in sub-transactions.

Note that `batchNonDistinctRefScriptsSize` **is** imported and used in the Ledger rule, but only for the `DijkstraTxRefScriptsSizeTooBig` size-cap check, not for fee enforcement.

---

### Impact Explanation

**Medium — Attacker-controlled transactions modify fees outside design parameters.**

An unprivileged transaction author can craft a `TopTx` whose sub-transactions each carry reference inputs pointing to large Plutus scripts stored in the UTxO. The byte-size component of the fee (`a × txSize`) does include the serialized sub-transactions (because `sizeDijkstraTxF` serializes the whole body including `dtbrSubTransactions`), but the reference-script surcharge (`tierRefScriptFee`) is computed only over the top-level inputs. The attacker therefore pays the base byte fee but avoids the exponential reference-script surcharge for every script referenced exclusively through sub-transactions. This re-opens the same DDoS vector that the June 2024 attack exploited and that the Conway tiered-pricing mechanism was designed to close.

---

### Likelihood Explanation

**High.** Any transaction submitter can exploit this without any privileged access. The Dijkstra era is the current development target. Constructing a `TopTx` with sub-transactions that reference large scripts is a straightforward operation requiring only knowledge of UTxO entries containing large reference scripts (which are publicly visible on-chain). No key compromise, governance majority, or Sybil attack is required.

---

### Recommendation

Replace the `getMinFeeTxUtxo` binding in the `EraUTxO DijkstraEra` instance with a Dijkstra-specific implementation that uses `batchNonDistinctRefScriptsSize` instead of `txNonDistinctRefScriptsSize`:

```haskell
-- eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs
getDijkstraMinFeeTxUtxo ::
  ( EraTx era
  , DijkstraEraTxBody era
  , BabbageEraTxBody era
  ) =>
  PParams era ->
  Tx l era ->
  UTxO era ->
  Coin
getDijkstraMinFeeTxUtxo pparams tx utxo =
  getMinFeeTx pparams tx $ batchNonDistinctRefScriptsSize utxo tx

instance EraUTxO DijkstraEra where
  ...
  getMinFeeTxUtxo = getDijkstraMinFeeTxUtxo
```

---

### Proof of Concept

The discrepancy is directly visible by tracing the call chain:

1. `feesOK` (Alonzo/Babbage UTXO rule) calls `getMinFeeTxUtxo pp tx u`. [1](#0-0) 

2. For `DijkstraEra`, `getMinFeeTxUtxo` is bound to `getConwayMinFeeTxUtxo`. [2](#0-1) 

3. `getConwayMinFeeTxUtxo` passes only `txNonDistinctRefScriptsSize utxo tx` (top-level inputs only) to `getMinFeeTx`. [3](#0-2) 

4. `txNonDistinctRefScriptsSize` unions only the top-level `referenceInputsTxBodyL` and `inputsTxBodyL`; sub-transaction inputs are never visited. [4](#0-3) 

5. `batchNonDistinctRefScriptsSize` — the correct aggregate — exists but is only used for the size-cap check, not for fee enforcement. [5](#0-4) 

6. `getConwayMinFeeTx` (used by `getMinFeeTx` for DijkstraEra) passes the reference-script size to `tierRefScriptFee`, which computes the exponential surcharge — but receives `0` for any scripts referenced only through sub-transactions. [6](#0-5) 

A concrete exploit: place a 200 KiB Plutus script as a reference script in a UTxO. Submit a `TopTx` with a single sub-transaction whose `dstbrReferenceInputs` points to that UTxO. The top-level transaction has no reference inputs. `txNonDistinctRefScriptsSize` returns 0; `tierRefScriptFee` returns `Coin 0`; the node accepts the transaction at base byte-fee only, while still deserializing the 200 KiB script for sub-transaction validation.

### Citations

**File:** eras/alonzo/impl/src/Cardano/Ledger/Alonzo/Rules/Utxo.hs (L293-298)
```haskell
      minFee = getMinFeeTxUtxo pp tx u
   in sequenceA_
        [ -- Part 1: minfee pp tx ≤ txfee txb
          failureUnless
            (minFee <= theFee)
            (FeeTooSmallUTxO Mismatch {mismatchSupplied = theFee, mismatchExpected = minFee})
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
