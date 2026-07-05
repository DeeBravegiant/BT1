### Title
Block-Level ExUnits Limit Checked After Script Execution, Allowing Block Producers to Force Nodes to Exceed `maxBlockExUnits` - (File: `eras/alonzo/impl/src/Cardano/Ledger/Alonzo/Rules/Bbody.hs`)

---

### Summary

In `alonzoBbodyTransition`, the `validateExUnits` check (enforcing `maxBlockExUnits`) is performed **after** `trans @(EraRule "LEDGERS" era)` has already executed all Plutus scripts in the block. A malicious block producer can craft a block whose transactions collectively declare ExUnits exceeding `maxBlockExUnits`, forcing all honest nodes to execute those scripts before the block is ultimately rejected. This violates the protocol's intended computational bound per block and constitutes a resource-limit bypass analogous to the Nibiru gas-not-consumed-on-failure issue.

---

### Finding Description

The `alonzoBbodyTransition` rule in `Bbody.hs` processes a block in this order:

```haskell
-- 1. Execute all transactions (including Plutus scripts)
ls' <- trans @(EraRule "LEDGERS" era) $
  TRC (Shelley.LedgersEnv bhSlot curEpoch pp account, ls, StrictSeq.fromStrict txs)

-- 2. Only THEN check the block-level ExUnits limit
validateExUnits @era txs $ pp ^. ppMaxBlockExUnitsL
``` [1](#0-0) 

`validateExUnits` sums `totExUnits` across all transactions and checks against `ppMaxBlockExUnitsL`:

```haskell
validateExUnits txs ppMax =
  let txTotal = foldMap totExUnits txs
   in pointWiseExUnits (<=) txTotal ppMax
        ?! injectFailure (TooManyExUnits ...)
``` [2](#0-1) 

If `validateExUnits` fails, the entire block transition fails and the state is rolled back — but the Plutus scripts were **already evaluated** during `trans @(EraRule "LEDGERS" era)`. The same ordering exists in the Dijkstra era: [3](#0-2) 

The `maxBlockExUnits` protocol parameter is explicitly designed to bound the time and memory required to validate a block:

> *"The `maxTxExUnits` and `maxBlockExUnits` protocol parameters are used to limit the total per-transaction and per-block resource use. These only apply to phase-2 scripts. The parameters are used to ensure that the time and memory that are required to verify a block are bounded."* [4](#0-3) 

Because the check is post-execution, this bound is not enforced at the right time.

The per-transaction check `validateExUnitsTooBigUTxO` (enforcing `maxTxExUnits`) runs during the UTXO rule and is correctly ordered before script execution: [5](#0-4) 

However, the **block-level** check has no such protection. A block producer can include up to `floor(maxBlockBodySize / minTxSize)` transactions each declaring `maxTxExUnits`, collectively totaling far more than `maxBlockExUnits`, and all nodes will execute every script before rejecting the block.

The fee/collateral mechanism does not mitigate this: fees and collateral are paid per-transaction and are based on declared ExUnits, but the block-level limit is what bounds aggregate per-block computation. The attacker (block producer) loses their slot reward, but the cost to the network (all nodes executing excess scripts) can greatly exceed that cost.

---

### Impact Explanation

**Medium — Attacker-controlled blocks exceed intended validation limits.**

The `maxBlockExUnits` parameter is the protocol's primary mechanism for bounding block validation time. By placing the check after execution, a malicious block producer can force all honest nodes to execute scripts totaling up to `floor(maxBlockBodySize / minTxSize) × maxTxExUnits` ExUnits per slot — potentially an order of magnitude more than `maxBlockExUnits` — before the block is rejected. This degrades node performance, increases block processing latency, and can be repeated every slot the attacker is elected, constituting a sustained resource-consumption attack against the network.

---

### Likelihood Explanation

A block producer below the consensus threshold can exploit this without any special privileges beyond being elected to a slot. The attacker needs only to:
1. Construct transactions with valid phase-1 structure and scripts that declare `maxTxExUnits` each (either `IsValid True` with expensive scripts, or `IsValid False` with scripts that still get evaluated to verify the tag).
2. Fill a block with such transactions up to the block size limit.
3. Submit the block.

The attacker loses their slot reward, but this cost is bounded and predictable, while the damage to the network scales with the ratio `(maxBlockBodySize / minTxSize) × maxTxExUnits / maxBlockExUnits`.

---

### Recommendation

Move `validateExUnits` **before** `trans @(EraRule "LEDGERS" era)` in `alonzoBbodyTransition` (and the equivalent Dijkstra transition), so that blocks exceeding `maxBlockExUnits` are rejected without executing any scripts:

```haskell
-- Check block-level ExUnits BEFORE executing scripts
validateExUnits @era txs $ pp ^. ppMaxBlockExUnitsL

ls' <- trans @(EraRule "LEDGERS" era) $
  TRC (Shelley.LedgersEnv bhSlot curEpoch pp account, ls, StrictSeq.fromStrict txs)
```

This mirrors the correct ordering already used for `validateBlockBodySize` and `validateBlockBodyHash`, which are checked before any ledger transitions. [6](#0-5) 

---

### Proof of Concept

1. Obtain a block-producing slot (any honest or adversarial pool operator).
2. Construct `K = floor(maxBlockBodySize / minTxSize)` transactions, each with:
   - A valid phase-1 structure (inputs, outputs, fees, collateral).
   - A redeemer declaring `ExUnits = maxTxExUnits`.
   - `IsValid = False` (so the script is evaluated to verify the tag, consuming the declared budget).
3. Pack all `K` transactions into a single block. Each transaction individually passes `validateExUnitsTooBigUTxO` (≤ `maxTxExUnits`), but collectively they declare `K × maxTxExUnits >> maxBlockExUnits`.
4. Broadcast the block. Every honest node will:
   - Execute all `K` scripts (each consuming up to `maxTxExUnits` computation).
   - Only then reach `validateExUnits`, which fails.
   - Reject the block and roll back state.
5. The attacker repeats this every slot they are elected, continuously forcing nodes to perform excess computation. [6](#0-5) [7](#0-6)

### Citations

**File:** eras/alonzo/impl/src/Cardano/Ledger/Alonzo/Rules/Bbody.hs (L149-167)
```haskell
validateExUnits ::
  forall era.
  ( AlonzoEraTx era
  , InjectRuleFailure "BBODY" AlonzoBbodyPredFailure era
  ) =>
  StrictSeq.StrictSeq (Tx TopTx era) ->
  -- | Max block exunits protocol parameter.
  ExUnits ->
  Rule (EraRule "BBODY" era) 'Transition ()
validateExUnits txs ppMax =
  let txTotal = foldMap totExUnits txs
   in pointWiseExUnits (<=) txTotal ppMax
        ?! injectFailure
          ( TooManyExUnits $
              Mismatch
                { mismatchSupplied = txTotal
                , mismatchExpected = ppMax
                }
          )
```

**File:** eras/alonzo/impl/src/Cardano/Ledger/Alonzo/Rules/Bbody.hs (L186-214)
```haskell
alonzoBbodyTransition = do
  TRC
    ( Shelley.BbodyEnv pp account
      , Shelley.BbodyState ls blocksMade
      , Shelley.BbodySignal block@Block {blockBody}
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
        , StrictSeq.fromStrict txs
        )

  validateExUnits @era txs $ pp ^. ppMaxBlockExUnitsL

  pure $ Shelley.BbodyState ls' $ incrBlocks block firstSlot (pp ^. ppDG) blocksMade
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

**File:** eras/alonzo/formal-spec/protocol-parameters.tex (L99-102)
```tex
\textbf{Limiting Script Execution Costs.}
The $\var{maxTxExUnits}$ and $\var{maxBlockExUnits}$ protocol parameters are
used to limit the total per-transaction and per-block resource use. These only apply to phase-2 scripts.
The parameters are used to ensure that the time and memory that are required to verify a block are bounded.
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
