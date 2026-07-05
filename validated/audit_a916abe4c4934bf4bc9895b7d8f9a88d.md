### Title
Sub-Transaction Plutus Script ExUnits Bypass Per-Transaction and Per-Block Limits, Enabling Fee Underpayment and Block Validation Overload - (`eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Utxo.hs`, `eras/alonzo/impl/src/Cardano/Ledger/Alonzo/Tx.hs`)

---

### Summary

In the Dijkstra era, Plutus scripts embedded in sub-transactions are executed by the ledger but their declared `ExUnits` are never validated against `maxTxExUnits`, are never summed into the block-level `maxBlockExUnits` check, and are never included in the minimum fee calculation. An unprivileged transaction author can craft a top-level transaction whose own redeemer set is empty (or minimal) while embedding arbitrarily many sub-transactions each carrying Plutus scripts that collectively declare and consume far more execution units than the protocol parameters permit per transaction or per block, while paying fees only for the top-level transaction's (near-zero) ExUnits.

---

### Finding Description

**Root cause — `totExUnits` only reads the top-level witness set**

`totExUnits` is defined as:

```haskell
totExUnits tx = foldMap snd $ tx ^. witsTxL . rdmrsTxWitsL . unRedeemersL
``` [1](#0-0) 

`tx ^. witsTxL` accesses only the top-level transaction's witness set. Sub-transactions carry their own independent witness sets (accessed via `subTransactionsTxBodyL`), so their redeemers — and therefore their declared `ExUnits` — are invisible to `totExUnits`.

**Per-transaction ExUnits check skips sub-transactions**

`dijkstraUtxoTransition` calls:

```haskell
{- totExunits tx ≤ maxTxExUnits pp -}
runTest $ Alonzo.validateExUnitsTooBigUTxO pp tx
``` [2](#0-1) 

`validateExUnitsTooBigUTxO` calls `totExUnits tx`, which, as shown above, returns only the top-level redeemers. Sub-transaction redeemers are not summed.

**The SUBUTXO rule explicitly excludes the ExUnits check**

The conversion function `dijkstraUtxoToDijkstraSubUtxoPredFailure` maps `ExUnitsTooBigUTxO` to a runtime `error "Impossible"`:

```haskell
ExUnitsTooBigUTxO _ -> error "Impossible: `ExUnitsTooBigUTxO` for SUBUTXO"
``` [3](#0-2) 

This confirms that the `ExUnitsTooBigUTxO` predicate failure is structurally absent from `DijkstraSubUtxoPredFailure` and is never raised for sub-transactions.

**Block-level ExUnits check also misses sub-transactions**

`validateExUnits` in the BBODY rule sums `totExUnits` over the sequence of top-level transactions in the block:

```haskell
let txTotal = foldMap totExUnits txs
``` [4](#0-3) 

Sub-transactions are embedded inside top-level transactions, not separate entries in `txs`, so their ExUnits are again invisible.

**Fee calculation omits sub-transaction ExUnits**

`getConwayMinFeeTxUtxo` (inherited by Dijkstra) computes the minimum fee using `getMinFeeTx pparams tx`, which internally calls `totExUnits tx`: [5](#0-4) 

Sub-transaction script fees are therefore not included in the minimum fee, allowing the attacker to execute Plutus scripts in sub-transactions without paying the `txscriptfee` for them.

**Exploit path**

1. Attacker constructs a Dijkstra top-level transaction with zero redeemers in its own witness set.
2. The top-level transaction body contains N sub-transactions, each with one or more Plutus scripts whose redeemers declare `ExUnits` close to `maxTxExUnits`.
3. The top-level transaction passes `validateExUnitsTooBigUTxO` (its own `totExUnits` = 0).
4. The block passes `validateExUnits` (block total = 0 from this transaction).
5. The SUBLEDGERS/SUBUTXOW/UTXOS pipeline executes all sub-transaction Plutus scripts, consuming N × `maxTxExUnits` worth of CPU/memory.
6. The minimum fee charged to the attacker is computed without any sub-transaction script fees.

---

### Impact Explanation

**Fee manipulation outside design parameters (Medium):** The attacker pays zero `txscriptfee` for Plutus scripts executed in sub-transactions, violating the invariant that every script execution is paid for by the submitter. This directly modifies fees outside design parameters.

**Exceeding intended validation limits (Medium):** The block-level `maxBlockExUnits` guard is bypassed. A single block can be forced to execute arbitrarily more Plutus computation than the protocol parameter intends, degrading block validation performance for all honest nodes in a deterministic, reproducible way. This matches the allowed impact: "Attacker-controlled transactions... exceed intended validation limits."

---

### Likelihood Explanation

The Dijkstra era introduces sub-transactions as a new feature. Any unprivileged user who can submit a valid Dijkstra transaction can exploit this — no special keys, governance access, or privileged role is required. The attacker only needs to pay the base transaction fee (which excludes sub-transaction script fees due to the same bug). The attack is deterministic and reproducible across all honest nodes.

---

### Recommendation

1. **Extend `totExUnits` to include sub-transaction redeemers** in Dijkstra, or introduce a dedicated `totBatchExUnits` that sums ExUnits across the top-level transaction and all sub-transactions.
2. **Apply `validateExUnitsTooBigUTxO` to the batch total** in `dijkstraUtxoTransition`, replacing the current per-level check.
3. **Include sub-transaction ExUnits in `getMinFeeTx`** so that `minfee` correctly charges for all Plutus script execution in the batch.
4. **Include sub-transaction ExUnits in the block-level `validateExUnits` check** in BBODY, or add a separate batch-aware block ExUnits check for Dijkstra.

---

### Proof of Concept

```
Top-level tx:
  inputs: [some UTxO]
  fee: minimal (no script fee charged)
  sub_transactions: [subTx_1, subTx_2, ..., subTx_N]
  witnesses: {} (no redeemers)

Each subTx_i:
  inputs: [UTxO locked by PlutusV3 script]
  witnesses:
    redeemers: { SpendingPurpose(0) -> (datum, ExUnits { mem = maxTxExUnitsMem, steps = maxTxExUnitsSteps }) }

Validation outcome:
  validateExUnitsTooBigUTxO on top-level tx: totExUnits = 0 ≤ maxTxExUnits  ✓ (passes)
  BBODY validateExUnits: txTotal = 0 ≤ maxBlockExUnits                       ✓ (passes)
  SUBLEDGERS executes all N sub-transactions, running N × maxTxExUnits of Plutus
  minfee charged: base fee only, no txscriptfee for sub-transaction scripts
``` [2](#0-1) [1](#0-0) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** eras/alonzo/impl/src/Cardano/Ledger/Alonzo/Tx.hs (L391-394)
```haskell
  (EraTx era, AlonzoEraTxWits era) =>
  Tx l era ->
  ExUnits
totExUnits tx = foldMap snd $ tx ^. witsTxL . rdmrsTxWitsL . unRedeemersL
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Utxo.hs (L414-415)
```haskell
  {- totExunits tx ≤ maxTxExUnits pp -}
  runTest $ Alonzo.validateExUnitsTooBigUTxO pp tx
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubUtxo.hs (L340-340)
```haskell
  ExUnitsTooBigUTxO _ -> error "Impossible: `ExUnitsTooBigUTxO` for SUBUTXO"
```

**File:** eras/alonzo/impl/src/Cardano/Ledger/Alonzo/Rules/Bbody.hs (L158-160)
```haskell
validateExUnits txs ppMax =
  let txTotal = foldMap totExUnits txs
   in pointWiseExUnits (<=) txTotal ppMax
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/UTxO.hs (L174-175)
```haskell
getConwayMinFeeTxUtxo pparams tx utxo =
  getMinFeeTx pparams tx $ txNonDistinctRefScriptsSize utxo tx
```
