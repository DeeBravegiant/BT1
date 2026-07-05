### Title
Per-Block Reference Script Size Limit Bypassed via Sub-Transaction Reference Scripts in Dijkstra Era - (File: `eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Bbody.hs`)

### Summary
In the Dijkstra era, the per-block reference script size limit (`maxRefScriptSizePerBlock`) is enforced using a function that only counts reference scripts from top-level transactions. Sub-transactions introduced in Dijkstra are not included in this per-block accounting. An unprivileged transaction sender can craft top-level transactions whose sub-transactions collectively reference far more script bytes than `maxRefScriptSizePerBlock` allows, forcing all honest nodes to deserialize an unbounded amount of reference script data per block.

### Finding Description

The Dijkstra era introduces nested ("sub") transactions embedded inside a top-level transaction body via the `dtbrSubTransactions` field. [1](#0-0) 

Two separate resource-limit checks exist for reference script sizes:

**Per-transaction check (Dijkstra LEDGER rule)** — uses `batchNonDistinctRefScriptsSize`, which correctly sums reference script bytes across the top-level transaction **and all its sub-transactions**: [2](#0-1) [3](#0-2) 

**Per-block check (Dijkstra BBODY rule)** — delegates to `Conway.validateBodyRefScriptsSizeTooBig`, which calls `totalRefScriptSizeInBlock`. That function iterates over the `StrictSeq (Tx TopTx era)` and calls `txNonDistinctRefScriptsSize` per top-level transaction — a function that is **unaware of sub-transactions**: [4](#0-3) [5](#0-4) 

The result is an asymmetry: the per-tx limit is computed over `top-level + sub-txs`, but the per-block limit is computed over `top-level only`. Sub-transaction reference scripts are invisible to the per-block enforcement.

### Impact Explanation

This matches the **Medium** allowed impact: *"Attacker-controlled transactions, blocks, certificates, votes, proposals, scripts, witnesses, or serialized inputs exceed intended validation limits."*

The `maxRefScriptSizePerBlock` parameter (1 MiB in the Dijkstra genesis example) was introduced specifically to bound the total script deserialization work per block, following the real DDoS attack of June 25, 2024 documented in the ADR: [6](#0-5) [7](#0-6) 

Because the per-block check ignores sub-transaction reference scripts, an attacker can force every honest node to deserialize a multiple of `maxRefScriptSizePerBlock` bytes of Plutus scripts per block, exceeding the intended bound without triggering any predicate failure.

### Likelihood Explanation

**Likelihood: 3/5.** Any unprivileged transaction sender can submit a top-level transaction containing sub-transactions. No special role, key, or governance majority is required. The attacker must pre-fund UTxO outputs containing large Plutus scripts (which is cheap relative to the deserialization cost imposed on validators), then reference those outputs from sub-transactions. The Dijkstra era is the current production-target era in this repository, making the attack surface live.

### Recommendation

Replace the call to `Conway.validateBodyRefScriptsSizeTooBig` in the Dijkstra BBODY rule with a Dijkstra-aware variant that uses `batchNonDistinctRefScriptsSize` (or an equivalent block-level accumulator) so that sub-transaction reference scripts are included in the per-block total. The per-tx check already has the correct implementation (`validateAllRefScriptSize` / `batchNonDistinctRefScriptsSize`) and can serve as a template.

### Proof of Concept

1. Deploy N large Plutus scripts (e.g., each ~200 KiB) into UTxO outputs as reference scripts.
2. Construct a top-level transaction with M sub-transactions; each sub-transaction's `referenceInputsTxBodyL` points to one of those UTxO outputs.
3. Keep the top-level transaction's own `referenceInputsTxBodyL` empty, so `txNonDistinctRefScriptsSize` returns 0 for the top-level tx.
4. Keep `batchNonDistinctRefScriptsSize` (top-level + sub-txs) ≤ `maxRefScriptSizePerTx` per top-level tx (e.g., one sub-tx per top-level tx, each referencing one 200 KiB script).
5. Pack enough such top-level transactions into a single block so that the aggregate sub-transaction reference script bytes exceed `maxRefScriptSizePerBlock` (e.g., 6 top-level txs × 200 KiB = 1.2 MiB > 1 MiB limit).
6. The BBODY rule's `validateBodyRefScriptsSizeTooBig` sees 0 bytes from each top-level tx and accepts the block; nodes must nonetheless deserialize 1.2 MiB of Plutus scripts.

The asymmetry is rooted in:
- `validateAllRefScriptSize` (per-tx, Dijkstra LEDGER): uses `batchNonDistinctRefScriptsSize` [8](#0-7) 
- `validateBodyRefScriptsSizeTooBig` (per-block, Dijkstra BBODY via Conway): uses `totalRefScriptSizeInBlock` → `txNonDistinctRefScriptsSize` [9](#0-8)

### Citations

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxBody.hs (L184-184)
```haskell
    , dtbrSubTransactions :: !(OMap TxId (Tx SubTx era))
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

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Bbody.hs (L361-363)
```haskell
  Alonzo.validateExUnits @era txs $ pp ^. ppMaxBlockExUnitsL

  Conway.validateBodyRefScriptsSizeTooBig @era pp blockBody (ls ^. utxoL)
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Bbody.hs (L329-370)
```haskell
-- | Validate that total reference script size does not exceed block limit.
validateBodyRefScriptsSizeTooBig ::
  forall era.
  ( AlonzoEraTx era
  , BabbageEraTxBody era
  , InjectRuleFailure "BBODY" ConwayBbodyPredFailure era
  , EraBlockBody era
  , ConwayEraPParams era
  ) =>
  PParams era ->
  BlockBody era ->
  UTxO era ->
  Rule (EraRule "BBODY" era) 'Transition ()
validateBodyRefScriptsSizeTooBig pp blockBody utxo =
  let protVer = pp ^. ppProtocolVersionL
      txs = blockBody ^. txSeqBlockBodyL
      totalSize = totalRefScriptSizeInBlock protVer txs utxo
      maxSize = fromIntegral @Word32 @Int $ pp ^. ppMaxRefScriptSizePerBlockG
   in totalSize
        <= maxSize
          ?! injectFailure
            ( BodyRefScriptsSizeTooBig $
                Mismatch
                  { mismatchSupplied = totalSize
                  , mismatchExpected = maxSize
                  }
            )

totalRefScriptSizeInBlock ::
  (AlonzoEraTx era, BabbageEraTxBody era) => ProtVer -> StrictSeq (Tx TopTx era) -> UTxO era -> Int
totalRefScriptSizeInBlock protVer txs (UTxO utxo)
  | pvMajor protVer <= natVersion @10 =
      getSum $ foldMap' (Monoid.Sum . txNonDistinctRefScriptsSize (UTxO utxo)) txs
  | otherwise =
      snd $ F.foldl' accum (utxo, 0) txs
  where
    accum (!accUtxo, !accSum) tx =
      let updatedUtxo = accUtxo `Map.union` unUTxO toAdd
          toAdd
            | IsValid True <- tx ^. isValidTxL = txouts $ tx ^. bodyTxL
            | otherwise = collOuts $ tx ^. bodyTxL
       in (updatedUtxo, accSum + txNonDistinctRefScriptsSize (UTxO accUtxo) tx)
```

**File:** docs/adr/2024-08-14_009-refscripts-fee-change.md (L15-19)
```markdown
It was identified a while ago that there is an overhead associated with deserializing scripts, so much so that it would be possible to create a very large script that was fairly expensive to deserialize, but very cheap to execute. This opened up an attack vector when such a Plutus script would be used as a reference script. This problem was exacerbated by the fact that there was no real limit on the total size of reference scripts that could be used in a transaction, thus being limited only by the size of the transaction itself. Therefore this opened up Cardano to a DDoS attack where an attacker could submit many such transactions that would cost very little, but would be expensive for a `cardano-node` to validate.

In order to prevent such an attack a `"minFeeRefScriptCostPerByte"` protocol parameter was introduced in the Conway era. The idea was fairly simple: we would calculate the total size of reference scripts used by a transaction and multiply it by the value specified by this protocol parameter. Result would be added to the transaction fee. However, in order for this approach to be a definite deterrent of such attacks this parameter would have to be set to a fairly high value. Community was not very keen on having this value set to a high enough value, since that could make reference scripts almost as expensive to use as regular scripts, thus significantly reducing their usability. Taking this fact into consideration, a decision was made to set this parameter to a fairly moderate value to at l ... (truncated)

Unfortunately things did not go exactly as planned, because on [June 25th 2024 an attack like this actually took place](https://cardanospot.io/news/ddos-attack-on-the-cardano-blockchain-mRIKAzZTNnzq5NGd). This attack forced us to make a quick decision on implementing a definite prevention of such attacks, while trying to not have a drastic impact on the common use case that DApp developers rely on so much.
```

**File:** docs/adr/2024-08-14_009-refscripts-fee-change.md (L71-78)
```markdown
### Reference script size limit

In order to further increase the resilience to this sort of attacks we added hard limits on the total size of reference scripts that can be used per transaction and per block.

Hard caps that are currently hard coded, but will be turned into actual protocol parameters in the next era after Conway:

* Limit per transaction: `200KiB` (or `204800` bytes)
* Limit per block: `1MiB` (or `1048576` bytes)
```
