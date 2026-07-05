### Title
Sub-Transaction ExUnits Not Aggregated in Block-Level Execution Budget Check — (`eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Utxo.hs`)

---

### Summary

In the Dijkstra era, `totExUnits` only sums the top-level transaction's redeemer ExUnits. The BBODY rule's block-level check uses `foldMap totExUnits txs`, which is blind to sub-transaction ExUnits. A block producer can craft a Dijkstra top-level transaction with many sub-transactions, each consuming up to `maxTxExUnits` in script execution, causing the total block script execution to far exceed `maxBlockExUnits` without triggering the block-level limit.

---

### Finding Description

**Root cause — `totExUnits` only reads the top-level witness set:**

`totExUnits` is defined as:

```haskell
totExUnits tx = foldMap snd $ tx ^. witsTxL . rdmrsTxWitsL . unRedeemersL
``` [1](#0-0) 

`rdmrsTxWitsL` reaches only the top-level transaction's witness set. In the Dijkstra era, a `Tx TopTx era` embeds sub-transactions (`Tx SubTx era`) via `dtbrSubTransactions`, each carrying its own witness set and redeemers. [2](#0-1) 

Sub-transaction redeemers are stored in separate witness sets and are never reached by `totExUnits`.

**Block-level check uses `totExUnits` directly:**

The BBODY `validateExUnits` function computes the block total as:

```haskell
validateExUnits txs ppMax =
  let txTotal = foldMap totExUnits txs
   in pointWiseExUnits (<=) txTotal ppMax
``` [3](#0-2) 

Because `totExUnits` is blind to sub-transactions, `txTotal` reflects only top-level redeemer ExUnits. Sub-transaction script execution is invisible to this check.

**Per-transaction check in Dijkstra UTXO also uses `totExUnits`:**

```haskell
{- totExunits tx ≤ maxTxExUnits pp -}
runTest $ Alonzo.validateExUnitsTooBigUTxO pp tx
``` [4](#0-3) 

This checks only the top-level tx. Sub-transactions are individually validated through the `SUBLEDGERS → LEDGER → UTXOW → UTXO` chain, where each sub-tx is checked against `maxTxExUnits` in isolation. However, the aggregate of all sub-tx ExUnits is never checked against `maxBlockExUnits`.

**`validateExUnitsTooBigUTxO` implementation:**

```haskell
validateExUnitsTooBigUTxO pp tx =
  failureUnless (pointWiseExUnits (<=) totalExUnits maxTxExUnits) $
    ExUnitsTooBigUTxO Mismatch {mismatchSupplied = totalExUnits, mismatchExpected = maxTxExUnits}
  where
    maxTxExUnits = pp ^. ppMaxTxExUnitsL
    totalExUnits = totExUnits tx
``` [5](#0-4) 

**`ExUnits` uses `Natural` (unbounded), so no integer overflow occurs** — the bypass is purely structural: sub-tx ExUnits are never included in the block-level sum. [6](#0-5) 

---

### Impact Explanation

A block producer crafts a Dijkstra top-level transaction with zero top-level redeemers (ExUnits = 0 at the top level) and N sub-transactions, each declaring ExUnits = `maxTxExUnits`. The BBODY check sees `txTotal = 0`, which trivially satisfies `0 ≤ maxBlockExUnits`. The block is accepted, but nodes must execute N × `maxTxExUnits` of Plutus script computation — far exceeding the intended `maxBlockExUnits` bound. This violates the protocol's resource-bounding guarantee and causes honest nodes to spend significantly more time validating the block than the parameters are designed to allow.

**Impact**: Medium — attacker-controlled transactions exceed intended validation limits (`maxBlockExUnits`), as the block-level execution budget is bypassed for sub-transaction scripts.

---

### Likelihood Explanation

Any stake pool operator (block producer) can exploit this. No privileged access, leaked keys, or governance majority is required. The attacker only needs to be a legitimate block producer — a normal participant in the Cardano protocol. The Dijkstra era introduces sub-transactions as a new attack surface that the existing ExUnits accounting was not updated to cover.

---

### Recommendation

Introduce a recursive ExUnits aggregation function for Dijkstra transactions that includes sub-transaction redeemers:

```haskell
totExUnitsWithSubTxs tx =
  totExUnits tx <>
  foldMap totExUnits (OMap.elems $ tx ^. bodyTxL . subTransactionsTxBodyL)
```

The Dijkstra BBODY rule should use this function instead of `totExUnits` when computing the block-level ExUnits total, so that `maxBlockExUnits` bounds the aggregate script execution across the entire transaction batch (top-level + all sub-transactions).

---

### Proof of Concept

1. Craft a Dijkstra top-level transaction with **zero** top-level redeemers (`totExUnits topTx = ExUnits 0 0`).
2. Embed N sub-transactions, each with a Plutus script redeemer declaring `ExUnits = maxTxExUnits`.
3. Each sub-transaction individually passes `validateExUnitsTooBigUTxO` (each sub-tx ExUnits ≤ `maxTxExUnits`).
4. The BBODY check evaluates `foldMap totExUnits [topTx] = ExUnits 0 0`, which satisfies `0 ≤ maxBlockExUnits`.
5. The block is accepted by honest nodes, which must execute N × `maxTxExUnits` of Plutus computation.
6. For sufficiently large N, this far exceeds `maxBlockExUnits`, violating the intended resource bound enforced by the protocol parameters. [7](#0-6) [8](#0-7)

### Citations

**File:** eras/alonzo/impl/src/Cardano/Ledger/Alonzo/Tx.hs (L390-394)
```haskell
totExUnits ::
  (EraTx era, AlonzoEraTxWits era) =>
  Tx l era ->
  ExUnits
totExUnits tx = foldMap snd $ tx ^. witsTxL . rdmrsTxWitsL . unRedeemersL
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxBody.hs (L184-184)
```haskell
    , dtbrSubTransactions :: !(OMap TxId (Tx SubTx era))
```

**File:** eras/alonzo/impl/src/Cardano/Ledger/Alonzo/Rules/Bbody.hs (L147-167)
```haskell
-- | Validate that total execution units (all transactions) do not exceed block limit.
-- ∑(tx ∈ txs)(totExunits tx) ≤ maxBlockExUnits pp
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

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Utxo.hs (L249-280)
```haskell
-- | For each account, the total withdrawals across the entire batch should not exceed the original account balance.
-- Unregistered accounts are treated as having 0 balance.
validateBatchWithdrawals ::
  ( EraTx era
  , EraAccounts era
  , DijkstraEraTxBody era
  ) =>
  Accounts era ->
  Tx TopTx era ->
  Test (DijkstraUtxoPredFailure era)
validateBatchWithdrawals accounts tx =
  let allWithdrawals =
        Map.unionsWith (<>) $
          unWithdrawals (tx ^. bodyTxL . withdrawalsTxBodyL)
            : [ unWithdrawals $ subTx ^. bodyTxL . withdrawalsTxBodyL
              | subTx <- OMap.elems $ tx ^. bodyTxL . subTransactionsTxBodyL
              ]
      badWithdrawals =
        Map.mapMaybeWithKey
          ( \acctAddr withdrawn ->
              let balance = getAccountBalance acctAddr
               in if withdrawn > balance
                    then Just Mismatch {mismatchSupplied = withdrawn, mismatchExpected = balance}
                    else Nothing
          )
          allWithdrawals
   in failureOnNonEmptyMap badWithdrawals WithdrawalsExceedAccountBalance
  where
    getAccountBalance (AccountAddress _ (AccountId cred)) =
      case lookupAccountState cred accounts of
        Nothing -> mempty -- unregistered account, 0 balance
        Just accountState -> fromCompact $ accountState ^. balanceAccountStateL
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

**File:** libs/cardano-ledger-core/src/Cardano/Ledger/Plutus/ExUnits.hs (L102-104)
```haskell
newtype ExUnits = WrapExUnits {unWrapExUnits :: ExUnits' Natural}
  deriving (Eq, Generic, Show)
  deriving newtype (Monoid, Semigroup)
```
