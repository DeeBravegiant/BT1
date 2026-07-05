### Title
Sub-Transaction Plutus Script Execution Costs Excluded from Minimum Fee and ExUnits Limit Validation in Dijkstra Batch Transactions — (`eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Utxo.hs`)

---

### Summary

In the Dijkstra era, the `dijkstraUtxoTransition` rule validates the top-level transaction fee and ExUnits budget using functions that only inspect the top-level transaction's redeemers. Sub-transactions embedded in a batch can carry Plutus script redeemers with arbitrary ExUnits, but those ExUnits are never counted toward the minimum fee or the per-transaction ExUnits cap. An unprivileged transaction sender can therefore submit a batch whose sub-transactions execute expensive Plutus scripts while the declared top-level fee covers only the (cheap or empty) top-level script costs, paying less than the protocol-mandated minimum fee and bypassing the `maxTxExUnits` resource limit.

---

### Finding Description

**Vulnerability class:** Fee/resource validation bypass due to conditional skip of sub-transaction ExUnits — the direct analog of the external report's "early return that leaves gas-consumed checks unevaluated."

**Root cause — `totExUnits` only counts top-level redeemers**

`totExUnits` is defined in `eras/alonzo/impl/src/Cardano/Ledger/Alonzo/Tx.hs`:

```haskell
totExUnits tx = foldMap snd $ tx ^. witsTxL . rdmrsTxWitsL . unRedeemersL
``` [1](#0-0) 

It folds only over the top-level transaction's witness set. Sub-transactions are stored in the body (`subTransactionsTxBodyL`) and carry their own witness sets; those are never visited here.

**`alonzoMinFeeTx` inherits the blind spot**

```haskell
alonzoMinFeeTx pp tx =
  (tx ^. sizeTxF <×> ...) <+> (pp ^. ppTxFeeFixedL)
    <+> txscriptfee (pp ^. ppPricesL) allExunits
  where
    allExunits = totExUnits tx   -- top-level only
``` [2](#0-1) 

The script-execution component of the minimum fee is therefore zero whenever the top-level transaction carries no redeemers, regardless of how many redeemers sub-transactions carry.

**`getConwayMinFeeTxUtxo` (used by Dijkstra) builds on `alonzoMinFeeTx`**

```haskell
getConwayMinFeeTxUtxo pparams tx utxo =
  getMinFeeTx pparams tx $ txNonDistinctRefScriptsSize utxo tx
``` [3](#0-2) 

`getMinFeeTx` calls `getConwayMinFeeTx` which calls `alonzoMinFeeTx pp tx <+> refScriptsFee`. The `refScriptsFee` component uses `txNonDistinctRefScriptsSize` (top-level inputs only), not the Dijkstra-specific `batchNonDistinctRefScriptsSize` that was introduced precisely to account for sub-transaction reference scripts. [4](#0-3) 

**Fee check in `dijkstraUtxoTransition` uses the underestimating function**

```haskell
{- minfee pp txTop utxo₀ ≤ txfee txb -}
runTest $ Shelley.validateFeeTooSmallUTxO pp tx originalUtxo
``` [5](#0-4) 

`validateFeeTooSmallUTxO` calls `getMinFeeTxUtxo pp tx utxo`, which resolves to `getConwayMinFeeTxUtxo` and ultimately to `alonzoMinFeeTx` with `totExUnits tx` (top-level only). Sub-transaction script costs are never added to the required minimum fee.

**ExUnits cap check is equally blind**

```haskell
{- totExunits tx ≤ maxTxExUnits pp -}
runTest $ Alonzo.validateExUnitsTooBigUTxO pp tx
``` [6](#0-5) 

`validateExUnitsTooBigUTxO` also calls `totExUnits tx`, so sub-transaction ExUnits are never compared against `maxTxExUnits`. [7](#0-6) 

**Sub-transactions demonstrably carry redeemers**

`validateBatchCollateral` explicitly checks sub-transaction redeemers to decide whether collateral is required:

```haskell
hasAnyRedeemers t =
  hasRedeemers t || any hasRedeemers (t ^. bodyTxL . subTransactionsTxBodyL)
hasRedeemers = not . null . (^. witsTxL . rdmrsTxWitsL . unRedeemersL)
``` [8](#0-7) 

The collateral check is correctly extended to the batch, but the fee and ExUnits checks are not.

**No ExUnits validation in the sub-transaction UTXO rule**

`DijkstraSubUtxoPredFailure` has no `ExUnitsTooBigUTxO` constructor, and the injection mapping explicitly marks it as impossible:

```haskell
ExUnitsTooBigUTxO _ -> error "Impossible: `ExUnitsTooBigUTxO` for SUBUTXO"
``` [9](#0-8) 

There is no compensating check anywhere in the sub-transaction processing path.

---

### Impact Explanation

**Fee underestimation (Medium):** An attacker sets the top-level transaction fee to the minimum required for a script-free (or cheap) top-level body, then embeds sub-transactions with arbitrarily expensive Plutus scripts. The ledger accepts the batch because `validateFeeTooSmallUTxO` only measures top-level script costs. The attacker pays a fraction of the fee that the protocol intends to charge for the actual computational work performed, modifying fees outside design parameters.

**ExUnits limit bypass (Medium):** Because `validateExUnitsTooBigUTxO` and the block-level `validateExUnits` both use `totExUnits` (top-level only), sub-transactions can carry ExUnits that individually or collectively exceed `maxTxExUnits` and `maxBlockExUnits` without triggering a predicate failure. This exceeds the intended per-transaction and per-block resource validation limits.

Both impacts fall within: *"Medium. Attacker-controlled transactions… exceed intended validation limits or modify fees… outside design parameters."*

---

### Likelihood Explanation

**Medium.** The Dijkstra era is production-bound and sub-transactions are a first-class feature. Any unprivileged user who can submit a transaction can craft a batch with expensive sub-transaction scripts. No special access, key compromise, or governance majority is required. The attack is analogous to the reference-script DDoS that occurred on mainnet in June 2024 (documented in `docs/adr/2024-08-14_009-refscripts-fee-change.md`), which exploited a similar fee-underestimation gap. [10](#0-9) 

---

### Recommendation

1. **Extend `totExUnits` (or introduce a batch variant)** to fold over sub-transaction redeemers in addition to the top-level transaction's redeemers, so that `alonzoMinFeeTx` and `validateExUnitsTooBigUTxO` account for the full batch cost.

2. **Override `getMinFeeTxUtxo` in the `EraUTxO DijkstraEra` instance** to use `batchNonDistinctRefScriptsSize` (already defined in `eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs`) instead of `txNonDistinctRefScriptsSize`, so that reference-script fees from sub-transactions are also included. [4](#0-3) 

3. **Add an ExUnits cap check for sub-transactions** in the SUBUTXO rule (or aggregate sub-transaction ExUnits into the top-level check), mirroring the pattern used for `validateBatchCollateral` and `validateBatchWithdrawals`.

---

### Proof of Concept

```
Attacker constructs a Dijkstra batch transaction T:
  - Top-level body: one spend input, one output, fee = minfee(pp, T_top, utxo)
    where T_top has no redeemers → totExUnits(T_top) = 0
    → txscriptfee contribution = 0
    → fee is accepted as valid by validateFeeTooSmallUTxO

  - Sub-transactions S₁, S₂, …, Sₙ (embedded in subTransactionsTxBodyL):
    each carries redeemers with ExUnits = (maxTxExUnits.mem, maxTxExUnits.steps)
    → actual computational cost = n × maxTxExUnits
    → none of this is counted by totExUnits(T_top)
    → validateExUnitsTooBigUTxO passes (0 ≤ maxTxExUnits)
    → validateFeeTooSmallUTxO passes (fee covers only size + fixed component)

Result:
  - Ledger accepts T with fee ≈ a × txSize + b (no script fee component)
  - Nodes execute n × maxTxExUnits worth of Plutus scripts
  - Attacker pays O(1) fee for O(n) computational work
  - Block-level ExUnits cap is also bypassed because validateExUnits
    sums totExUnits across transactions, which is 0 for T
```

### Citations

**File:** eras/alonzo/impl/src/Cardano/Ledger/Alonzo/Tx.hs (L375-388)
```haskell
alonzoMinFeeTx ::
  ( EraTx era
  , AlonzoEraTxWits era
  , AlonzoEraPParams era
  ) =>
  PParams era ->
  Tx l era ->
  Coin
alonzoMinFeeTx pp tx =
  (tx ^. sizeTxF <×> (fromCompact . unCoinPerByte) (pp ^. ppTxFeePerByteL))
    <+> (pp ^. ppTxFeeFixedL)
    <+> txscriptfee (pp ^. ppPricesL) allExunits
  where
    allExunits = totExUnits tx
```

**File:** eras/alonzo/impl/src/Cardano/Ledger/Alonzo/Tx.hs (L390-394)
```haskell
totExUnits ::
  (EraTx era, AlonzoEraTxWits era) =>
  Tx l era ->
  ExUnits
totExUnits tx = foldMap snd $ tx ^. witsTxL . rdmrsTxWitsL . unRedeemersL
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/UTxO.hs (L174-175)
```haskell
getConwayMinFeeTxUtxo pparams tx utxo =
  getMinFeeTx pparams tx $ txNonDistinctRefScriptsSize utxo tx
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

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Utxo.hs (L296-302)
```haskell
  when (hasAnyRedeemers tx) $
    Babbage.validateTotalCollateral pp (tx ^. bodyTxL) utxoCollateral
  where
    utxoCollateral = Map.restrictKeys utxo (tx ^. bodyTxL . collateralInputsTxBodyL)
    hasAnyRedeemers t =
      hasRedeemers t || any hasRedeemers (t ^. bodyTxL . subTransactionsTxBodyL)
    hasRedeemers = not . null . (^. witsTxL . rdmrsTxWitsL . unRedeemersL)
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Utxo.hs (L372-373)
```haskell
  {- minfee pp txTop utxo₀ ≤ txfee txb -}
  runTest $ Shelley.validateFeeTooSmallUTxO pp tx originalUtxo
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Utxo.hs (L414-415)
```haskell
  {- totExunits tx ≤ maxTxExUnits pp -}
  runTest $ Alonzo.validateExUnitsTooBigUTxO pp tx
```

**File:** eras/alonzo/impl/src/Cardano/Ledger/Alonzo/Rules/Utxo.hs (L459-465)
```haskell
validateExUnitsTooBigUTxO pp tx =
  failureUnless (pointWiseExUnits (<=) totalExUnits maxTxExUnits) $
    ExUnitsTooBigUTxO Mismatch {mismatchSupplied = totalExUnits, mismatchExpected = maxTxExUnits}
  where
    maxTxExUnits = pp ^. ppMaxTxExUnitsL
    -- This sums up the ExUnits for all embedded Plutus Scripts anywhere in the transaction:
    totalExUnits = totExUnits tx
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubUtxo.hs (L324-351)
```haskell
dijkstraUtxoToDijkstraSubUtxoPredFailure ::
  DijkstraUtxoPredFailure era -> DijkstraSubUtxoPredFailure era
dijkstraUtxoToDijkstraSubUtxoPredFailure = \case
  UtxosFailure _ -> error "Impossible: `UtxosFailure` for SUBUTXO"
  BadInputsUTxO x -> SubBadInputsUTxO x
  OutsideValidityIntervalUTxO vi slotNo -> SubOutsideValidityIntervalUTxO vi slotNo
  MaxTxSizeUTxO m -> SubMaxTxSizeUTxO m
  InputSetEmptyUTxO -> SubInputSetEmptyUTxO
  FeeTooSmallUTxO _ -> error "Impossible: `FeeTooSmallUTxO` for SUBUTXO"
  ValueNotConservedUTxO _ -> error "Impossible: `ValueNotConservedUTxO` for SUBUTXO"
  WrongNetwork x y -> SubWrongNetwork x y
  WrongNetworkWithdrawal x y -> SubWrongNetworkWithdrawal x y
  OutputBootAddrAttrsTooBig xs -> SubOutputBootAddrAttrsTooBig xs
  OutputTooBigUTxO xs -> SubOutputTooBigUTxO xs
  InsufficientCollateral _ _ -> error "Impossible: `InsufficientCollateral` for SUBUTXO"
  ScriptsNotPaidUTxO _ -> error "Impossible: `ScriptsNotPaidUTxO` for SUBUTXO"
  ExUnitsTooBigUTxO _ -> error "Impossible: `ExUnitsTooBigUTxO` for SUBUTXO"
  CollateralContainsNonADA _ -> error "Impossible: `CollateralContainsNonADA` for SUBUTXO"
  WrongNetworkInTxBody m -> SubWrongNetworkInTxBody m
  OutsideForecast sno -> SubOutsideForecast sno
  TooManyCollateralInputs _ -> error "Impossible: `TooManyCollateralInputs` for SUBUTXO"
  NoCollateralInputs -> error "Impossible: `NoCollateralInputs` for SUBUTXO"
  IncorrectTotalCollateralField _ _ -> error "Impossible: `IncorrectTotalCollateralField` for SUBUTXO"
  BabbageOutputTooSmallUTxO outs -> SubBabbageOutputTooSmallUTxO outs
  BabbageNonDisjointRefInputs _ -> error "Impossible: `BabbageNonDisjointRefInputs` for SUBUTXO"
  PtrPresentInCollateralReturn _ -> error "Impossible: `PtrPresentInCollateralReturn` for SUBUTXO"
  WrongNetworkInDirectDeposit x y -> SubWrongNetworkInDirectDeposit x y
  WithdrawalsExceedAccountBalance _ -> error "Impossible: `WithdrawalsExceedAccountBalance` for SUBUTXO"
```

**File:** docs/adr/2024-08-14_009-refscripts-fee-change.md (L15-19)
```markdown
It was identified a while ago that there is an overhead associated with deserializing scripts, so much so that it would be possible to create a very large script that was fairly expensive to deserialize, but very cheap to execute. This opened up an attack vector when such a Plutus script would be used as a reference script. This problem was exacerbated by the fact that there was no real limit on the total size of reference scripts that could be used in a transaction, thus being limited only by the size of the transaction itself. Therefore this opened up Cardano to a DDoS attack where an attacker could submit many such transactions that would cost very little, but would be expensive for a `cardano-node` to validate.

In order to prevent such an attack a `"minFeeRefScriptCostPerByte"` protocol parameter was introduced in the Conway era. The idea was fairly simple: we would calculate the total size of reference scripts used by a transaction and multiply it by the value specified by this protocol parameter. Result would be added to the transaction fee. However, in order for this approach to be a definite deterrent of such attacks this parameter would have to be set to a fairly high value. Community was not very keen on having this value set to a high enough value, since that could make reference scripts almost as expensive to use as regular scripts, thus significantly reducing their usability. Taking this fact into consideration, a decision was made to set this parameter to a fairly moderate value to at l ... (truncated)

Unfortunately things did not go exactly as planned, because on [June 25th 2024 an attack like this actually took place](https://cardanospot.io/news/ddos-attack-on-the-cardano-blockchain-mRIKAzZTNnzq5NGd). This attack forced us to make a quick decision on implementing a definite prevention of such attacks, while trying to not have a drastic impact on the common use case that DApp developers rely on so much.
```
