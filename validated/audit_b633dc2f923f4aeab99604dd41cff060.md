### Title
Dijkstra-era block-level reference script size limit does not account for sub-transaction reference scripts, allowing the per-block cap to be bypassed - (`File: eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Bbody.hs`)

---

### Summary

In the Dijkstra era, a top-level transaction may embed sub-transactions (`subTransactionsTxBodyL`). The per-transaction reference script size check correctly aggregates reference scripts across the top-level transaction and all its sub-transactions via `batchNonDistinctRefScriptsSize`. However, the block-level check reuses the Conway-era `totalRefScriptSizeInBlock`, which calls `txNonDistinctRefScriptsSize` per top-level transaction and is unaware of sub-transactions. Sub-transaction reference scripts are therefore invisible to the block-level cap, allowing an attacker to force nodes to deserialize far more reference script data per block than the `maxRefScriptSizePerBlock` parameter intends.

---

### Finding Description

**Per-transaction check (correct):**

`dijkstraLedgerTransition` calls `validateAllRefScriptSize`, which uses `batchNonDistinctRefScriptsSize`:

```haskell
batchNonDistinctRefScriptsSize utxo tx =
  txNonDistinctRefScriptsSize utxo tx
    + getSum (foldMap' (Sum . txNonDistinctRefScriptsSize utxo)
                       (tx ^. bodyTxL . subTransactionsTxBodyL))
```

This correctly sums reference scripts from the top-level transaction **and** all embedded sub-transactions, and checks the total against `ppMaxRefScriptSizePerTxG` (200 KiB). [1](#0-0) [2](#0-1) 

**Block-level check (deficient):**

`dijkstraBbodyTransition` delegates to `Conway.validateBodyRefScriptsSizeTooBig`:

```haskell
Conway.validateBodyRefScriptsSizeTooBig @era pp blockBody (ls ^. utxoL)
``` [3](#0-2) 

That Conway function calls `totalRefScriptSizeInBlock`, which iterates over the top-level transaction sequence and calls `txNonDistinctRefScriptsSize` per transaction — **not** `batchNonDistinctRefScriptsSize`:

```haskell
totalRefScriptSizeInBlock protVer txs (UTxO utxo)
  | pvMajor protVer <= natVersion @10 = ...
  | otherwise =
      snd $ F.foldl' accum (utxo, 0) txs
  where
    accum (!accUtxo, !accSum) tx =
      ...
       in (updatedUtxo, accSum + txNonDistinctRefScriptsSize (UTxO accUtxo) tx)
``` [4](#0-3) 

`txNonDistinctRefScriptsSize` only inspects the top-level transaction body's inputs and reference inputs; it has no knowledge of the `subTransactionsTxBodyL` field introduced in Dijkstra. Sub-transaction reference scripts are therefore never added to the block-level running total.

---

### Impact Explanation

An attacker can construct top-level transactions whose top-level body references zero or few reference scripts, but whose embedded sub-transactions collectively reference up to `maxRefScriptSizePerTx` (200 KiB) of reference scripts. The per-transaction check passes (200 KiB ≤ 200 KiB). The block-level check sees only the top-level contribution (≈ 0 bytes). By packing many such transactions into a single block (bounded only by `maxBlockBodySize` = 90,112 bytes), an attacker can force every validating node to deserialize a multiple of 200 KiB of reference scripts per block, far exceeding the intended 1 MiB block cap.

For example, with a minimal top-level transaction of ~100 bytes, a block could contain ~900 top-level transactions, each forcing 200 KiB of sub-transaction reference script deserialization: **~180 MiB** of deserialization work per block against a nominal 1 MiB limit. This is the same class of DDoS attack that occurred on Cardano mainnet on June 25, 2024 and required an emergency protocol fix.

This matches the allowed impact: **Medium — attacker-controlled transactions exceed intended validation limits.** [5](#0-4) 

---

### Likelihood Explanation

The attack requires only:
1. Pre-populating the UTxO with reference script outputs (a normal, permissionless operation).
2. Constructing top-level Dijkstra transactions with sub-transactions that reference those scripts.

No privileged access, governance majority, or key compromise is needed. Any unprivileged transaction sender can execute this once the Dijkstra era is active.

---

### Recommendation

Override `validateBodyRefScriptsSizeTooBig` in the Dijkstra BBODY rule (or introduce a Dijkstra-specific `totalRefScriptSizeInBlock`) that replaces the per-transaction call to `txNonDistinctRefScriptsSize` with `batchNonDistinctRefScriptsSize`, so that sub-transaction reference scripts are included in the block-level accounting.

Concretely, in `eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Bbody.hs`, replace the reuse of `Conway.validateBodyRefScriptsSizeTooBig` with a Dijkstra-aware variant that sums `batchNonDistinctRefScriptsSize` across all top-level transactions in the block. [6](#0-5) 

---

### Proof of Concept

1. Deploy N reference script UTxO entries, each containing a large Plutus script (e.g., 10 KiB each), totalling up to 200 KiB across 20 entries.
2. Construct a Dijkstra top-level transaction `T` with:
   - Top-level body: no reference inputs (0 bytes of ref scripts at the top level).
   - Sub-transactions: each sub-transaction references all 20 UTxO entries as reference inputs → 200 KiB of ref scripts per batch.
3. The per-transaction check in `validateAllRefScriptSize` computes `batchNonDistinctRefScriptsSize = 200 KiB ≤ 200 KiB` → passes.
4. The block check in `totalRefScriptSizeInBlock` computes `txNonDistinctRefScriptsSize(T) = 0` → the block-level counter is not incremented.
5. Pack as many such transactions as fit within `maxBlockBodySize` (90,112 bytes) into a single block.
6. Each validating node must deserialize all referenced scripts for every sub-transaction in every top-level transaction, far exceeding the 1 MiB block-level deserialization budget.

### Citations

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

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Ledger.hs (L321-329)
```haskell
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

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Bbody.hs (L335-371)
```haskell
dijkstraBbodyTransition = do
  TRC
    ( Shelley.BbodyEnv pp account
      , Shelley.BbodyState ls blocksMade
      , DijkstraBbodySignal block@Block {blockBody}
      ) <-
    judgmentContext

  Shelley.validateBlockBodySize block (pp ^. ppProtocolVersionL)

  Shelley.validateBlockBodyHash block

  let bhSlot = block ^. slotNoBlockHeaderL

  (firstSlot, curEpoch) <- liftSTS $ slotToEpochBoundary bhSlot

  let txs = blockBody ^. txSeqBlockBodyL

  ls' <-
    trans @(EraRule "LEDGERS" era) $
      TRC
        ( Shelley.LedgersEnv bhSlot curEpoch pp account
        , ls
        , fromStrict txs
        )

  Alonzo.validateExUnits @era txs $ pp ^. ppMaxBlockExUnitsL

  Conway.validateBodyRefScriptsSizeTooBig @era pp blockBody (ls ^. utxoL)

  case blockBody ^. perasCertBlockBodyL of
    SNothing -> pure ()
    SJust cert ->
      let nonce = block ^. prevNonceBlockHeaderL
       in validatePerasCert nonce PerasKey cert ?! injectFailure (PerasCertValidationFailed cert nonce)

  pure $ Shelley.BbodyState ls' $ incrBlocks block firstSlot (pp ^. ppDG) blocksMade
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Bbody.hs (L357-370)
```haskell
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
