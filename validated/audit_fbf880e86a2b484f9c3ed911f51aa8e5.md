### Title
Hardcoded Reference Script Size Limit and Fee Parameters in Conway Era Cannot Be Adjusted by Governance, Enabling Permanent Fund Freezing via Crafted Scripts - (File: eras/conway/impl/src/Cardano/Ledger/Conway/PParams.hs)

### Summary
In the Conway era, the maximum reference script size per transaction, per block, and the fee-tier multiplier and stride are hardcoded as compile-time constants in the `ConwayEraPParams` instance. They cannot be changed by any governance action and require a hard fork to modify. A script author can craft a Plutus script that requires reference scripts totaling more than 200 KiB to execute; any ADA or native assets locked in such a script address are permanently unspendable in the Conway era.

### Finding Description
In `eras/conway/impl/src/Cardano/Ledger/Conway/PParams.hs`, the `ConwayEraPParams` instance implements four critical parameters as hardcoded constants via `L.to . const`:

```haskell
ppMaxRefScriptSizePerTxG    = L.to . const $ 200 * 1024
ppMaxRefScriptSizePerBlockG = L.to . const $ 1024 * 1024
ppRefScriptCostMultiplierG  = L.to . const . fromJust $ boundRational 1.2
ppRefScriptCostStrideG      = L.to . const $ knownNonZeroBounded @25_600
``` [1](#0-0) 

These fields are **not** stored in the `ConwayPParams` data structure and are therefore invisible to the governance update mechanism. No `PParamsUpdate` can touch them.

The enforcement point is `validateRefScriptSize` in `eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Ledger.hs`:

```haskell
validateRefScriptSize pp utxo tx =
  let totalRefScriptSize = txNonDistinctRefScriptsSize utxo tx
      maxRefScriptSizePerTx = fromIntegral @Word32 @Int $ pp ^. ppMaxRefScriptSizePerTxG
   in failureUnless (totalRefScriptSize <= maxRefScriptSizePerTx) $
        ConwayTxRefScriptsSizeTooBig ...
``` [2](#0-1) 

The fee calculation in `getConwayMinFeeTx` (`eras/conway/impl/src/Cardano/Ledger/Conway/Tx.hs`) similarly reads the hardcoded multiplier and stride:

```haskell
getConwayMinFeeTx pp tx refScriptsSize =
  alonzoMinFeeTx pp tx <+> refScriptsFee
  where
    refScriptsFee =
      tierRefScriptFee
        (unboundRational $ pp ^. ppRefScriptCostMultiplierG)
        (fromIntegral @Word32 @Int . unNonZero $ pp ^. ppRefScriptCostStrideG)
        ...
``` [3](#0-2) 

The ADR at `docs/adr/2024-08-14_009-refscripts-fee-change.md` explicitly acknowledges this design choice: *"we had to hard code some values, which will be turned into proper protocol parameters in the next era."* The Dijkstra era corrects this by introducing `dppMaxRefScriptSizePerTx`, `dppMaxRefScriptSizePerBlock`, `dppRefScriptCostStride`, and `dppRefScriptCostMultiplier` as proper, governance-adjustable protocol parameters. [4](#0-3) 

**Attack path via script authorship:** A script author deploys a Plutus script `S` whose spending logic requires reference scripts `R1 … Rn` with `sum(size(Ri)) > 200 KiB`. Any ADA or native assets sent to `addr(S)` are permanently unspendable in Conway: every spending transaction that includes `R1 … Rn` as reference inputs is unconditionally rejected by `validateRefScriptSize` with `ConwayTxRefScriptsSizeTooBig`. Because the 200 KiB cap is a compile-time constant, no governance proposal can raise it; recovery requires a hard fork.

### Impact Explanation
**High. Permanent freezing of funds where recovery requires a hard fork.**

Funds locked in a script whose execution requires reference scripts exceeding the hardcoded 200 KiB per-transaction cap are irrecoverably frozen in the Conway era. The `validateRefScriptSize` predicate is unconditional: it reads the constant via `ppMaxRefScriptSizePerTxG` and rejects the transaction regardless of any protocol parameter state. The only remediation path is a hard fork that either raises the constant or, as the Dijkstra era does, promotes it to a proper protocol parameter.

### Likelihood Explanation
**Medium.** The script author role is an unprivileged, attacker-controlled entry point. Crafting a script that requires large reference scripts is straightforward for any Plutus developer. Complex multi-protocol DeFi applications that compose several large Plutus scripts as reference inputs are realistic targets. The 200 KiB limit is not prominently surfaced in user-facing tooling, increasing the risk that legitimate DApp developers inadvertently deploy scripts that cross the threshold, or that a malicious script author exploits the immutability of the cap to trap user funds.

### Recommendation
Promote `ppMaxRefScriptSizePerTxG`, `ppMaxRefScriptSizePerBlockG`, `ppRefScriptCostMultiplierG`, and `ppRefScriptCostStrideG` to proper protocol parameters in the Conway era, following the pattern already implemented in the Dijkstra era. This allows governance to adjust these values without a hard fork, eliminating the permanent-freeze risk and the inability to respond to changing network conditions.

### Proof of Concept
1. Script author writes a Plutus script `S` whose validator logic calls `findOwnInput` and checks a condition that can only be satisfied when reference scripts `R1, R2, …, Rn` (each ~30 KiB, totalling ~210 KiB) are present in the transaction's reference inputs.
2. Users lock ADA at `addr(S)` (e.g., by interacting with a DeFi protocol built on `S`).
3. Any spending transaction `tx` that includes `R1 … Rn` as reference inputs has `txNonDistinctRefScriptsSize utxo tx ≈ 210 KiB > 200 KiB` and is rejected by `validateRefScriptSize` with `ConwayTxRefScriptsSizeTooBig`.
4. Any spending transaction `tx` that omits some `Ri` fails Plutus phase-2 validation because the script's condition is unsatisfied.
5. The funds in `addr(S)` are permanently frozen in the Conway era; recovery requires a hard fork to raise or parameterize the limit.

### Citations

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/PParams.hs (L980-983)
```haskell
  ppMaxRefScriptSizePerTxG = L.to . const $ 200 * 1024
  ppMaxRefScriptSizePerBlockG = L.to . const $ 1024 * 1024
  ppRefScriptCostMultiplierG = L.to . const . fromJust $ boundRational 1.2
  ppRefScriptCostStrideG = L.to . const $ knownNonZeroBounded @25_600
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Ledger.hs (L456-471)
```haskell
validateRefScriptSize ::
  ( EraTx era
  , BabbageEraTxBody era
  , ConwayEraPParams era
  ) =>
  PParams era -> UTxO era -> Tx l era -> Test (ConwayLedgerPredFailure era)
validateRefScriptSize pp utxo tx =
  let totalRefScriptSize = txNonDistinctRefScriptsSize utxo tx
      maxRefScriptSizePerTx = fromIntegral @Word32 @Int $ pp ^. ppMaxRefScriptSizePerTxG
   in failureUnless (totalRefScriptSize <= maxRefScriptSizePerTx) $
        ( ConwayTxRefScriptsSizeTooBig
            Mismatch
              { mismatchSupplied = totalRefScriptSize
              , mismatchExpected = maxRefScriptSizePerTx
              }
        )
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

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/PParams.hs (L151-157)
```haskell
  , dppMaxRefScriptSizePerBlock :: !(THKD ('PPGroups 'NetworkGroup 'SecurityGroup) f Word32)
  -- ^ Limit on the total number of bytes of all reference scripts combined from
  -- all transactions within a block.
  , dppMaxRefScriptSizePerTx :: !(THKD ('PPGroups 'NetworkGroup 'SecurityGroup) f Word32)
  -- ^ Limit on the total number of bytes of reference scripts that a transaction can use.
  , dppRefScriptCostStride :: !(THKD ('PPGroups 'NetworkGroup 'SecurityGroup) f (NonZero Word32))
  , dppRefScriptCostMultiplier :: !(THKD ('PPGroups 'NetworkGroup 'SecurityGroup) f PositiveInterval)
```
