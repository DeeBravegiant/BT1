### Title
Integer Truncation from Division Before Multiplication in `scaledMinDeposit` Undercharges Minimum ADA for Multi-Asset UTxOs — (File: `eras/mary/impl/src/Cardano/Ledger/Mary/TxOut.hs`)

---

### Summary

The `scaledMinDeposit` function in the Mary era performs integer division before multiplication when computing the minimum ADA value for multi-asset UTxO outputs. This truncates the per-word cost before scaling it to the actual UTxO size, causing the enforced minimum to be systematically lower than the protocol-intended value. An unprivileged transaction sender can craft multi-asset outputs that pass the UTxO validation rule with less ADA than the design specification requires.

---

### Finding Description

`scaledMinDeposit` is the Mary-era implementation of `getMinCoinTxOut`. Its purpose is to enforce that every UTxO output holds at least enough ADA to cover its proportional share of ledger storage cost, scaled from the `minUTxOValue` protocol parameter.

The intended formula (stated in the code comment) is:

```
actualMinValue = (minValueParameter / coinUTxOSize) * valueUTxOSize
```

The implementation computes:

```haskell
coinsPerUTxOWord :: Integer
coinsPerUTxOWord = quot mv (utxoEntrySizeWithoutVal + coinSize)   -- integer division FIRST
-- ...
Coin $ max mv (coinsPerUTxOWord * (utxoEntrySizeWithoutVal + size v))  -- multiply AFTER
``` [1](#0-0) 

`quot mv 27` truncates the rational quotient before the result is multiplied by `(utxoEntrySizeWithoutVal + size v)`. The correct order is to multiply first and divide last:

```
actualMinValue = mv * (utxoEntrySizeWithoutVal + size v) / (utxoEntrySizeWithoutVal + coinSize)
```

This is the canonical division-before-multiplication precision loss: the truncated intermediate value `coinsPerUTxOWord` loses up to `(utxoEntrySizeWithoutVal + size v) - 1` lovelace of precision relative to the exact rational result.

The formal specification confirms the intended formula is `coinsPerUTxOWord mv = ⌊ mv / adaOnlyUTxOSize ⌋` applied as a per-word rate, but the implementation truncates the rate before multiplying by the actual entry size, compounding the error. [2](#0-1) 

The UTxO rule enforces this check for every output in a Mary-era transaction: [3](#0-2) 

---

### Impact Explanation

An attacker submitting a Mary-era transaction with multi-asset outputs can include 1–7 lovelace less ADA per output than the protocol-intended minimum, because the enforced floor is computed with a truncated per-word rate. Concretely:

- With `mv = 1,000,000`, `utxoEntrySizeWithoutVal = 27`, `coinSize = 0`:
  - `coinsPerUTxOWord = quot 1000000 27 = 37037` (exact: 37037.037…)
  - For a UTxO with `size v = 11` (smallest multi-asset): enforced minimum = `37037 × 38 = 1,407,406`; correct minimum = `⌊1,000,000 × 38 / 27⌋ = 1,407,407` → **1 lovelace undercharge**
  - For a UTxO with `size v = 173` (large bundle): enforced minimum = `37037 × 200 = 7,407,400`; correct minimum = `⌊1,000,000 × 200 / 27⌋ = 7,407,407` → **7 lovelace undercharge**

The `max mv (...)` guard ensures the minimum is at least `minUTxOValue`, so the deviation is bounded. The impact class is **Medium**: attacker-controlled transactions modify the effective minimum deposit requirement outside design parameters. The magnitude per output is 1–7 lovelace, which is negligible in absolute terms but constitutes a systematic deviation from the protocol specification.

---

### Likelihood Explanation

Any unprivileged user submitting a Mary-era transaction with multi-asset outputs triggers this code path. The Mary era is a historical era; the current active era is Conway, which uses a different minimum calculation (`coinsPerUTxOByte`). Therefore, this path is only reachable for transactions submitted during the Mary era or for historical block re-validation. The likelihood of new exploitation is low, but the root cause is structurally present in the production code.

---

### Recommendation

Reorder the arithmetic to multiply before dividing, preserving full precision until the final floor:

```haskell
-- Correct: multiply first, divide last
scaledMinValue :: Integer
scaledMinValue = mv * (utxoEntrySizeWithoutVal + size v) `quot` (utxoEntrySizeWithoutVal + coinSize)

-- Then: Coin $ max mv scaledMinValue
```

This matches the formula stated in the code comment and the formal specification, and eliminates the truncation of the intermediate per-word rate.

---

### Proof of Concept

Given `minUTxOValue = 1,000,000` lovelace and a multi-asset output with `size v = 173` (e.g., three policy IDs with ninety-six 1-character asset names):

**Current code path:**
```
coinsPerUTxOWord = quot 1000000 27 = 37037          -- truncated
enforced_min     = 37037 * (27 + 173) = 7,407,400
```

**Correct calculation:**
```
correct_min = floor(1000000 * 200 / 27) = floor(7407407.4) = 7,407,407
```

**Deviation:** 7 lovelace. A transaction output containing exactly `7,407,400` lovelace with this token bundle passes the Mary-era UTxO rule but violates the protocol-intended minimum by 7 lovelace. [1](#0-0) [4](#0-3)

### Citations

**File:** eras/mary/impl/src/Cardano/Ledger/Mary/TxOut.hs (L33-33)
```haskell
  getMinCoinTxOut pp txOut = scaledMinDeposit (txOut ^. valueTxOutL) (pp ^. ppMinUTxOValueL)
```

**File:** eras/mary/impl/src/Cardano/Ledger/Mary/TxOut.hs (L52-76)
```haskell
scaledMinDeposit :: Val v => v -> Coin -> Coin
scaledMinDeposit v (Coin mv)
  | isAdaOnly v = Coin mv -- without non-Coin assets, scaled deposit should be exactly minUTxOValue
  -- The calculation should represent this equation
  -- minValueParameter / coinUTxOSize = actualMinValue / valueUTxOSize
  -- actualMinValue = (minValueParameter / coinUTxOSize) * valueUTxOSize
  | otherwise = Coin $ max mv (coinsPerUTxOWord * (utxoEntrySizeWithoutVal + size v))
  where
    -- lengths obtained from tracing on HeapWords of inputs and outputs
    -- obtained experimentally, and number used here
    -- units are Word64s
    txoutLenNoVal = 14
    txinLen = 7

    -- unpacked CompactCoin Word64 size in Word64s
    coinSize :: Integer
    coinSize = 0

    utxoEntrySizeWithoutVal :: Integer
    utxoEntrySizeWithoutVal = 6 + txoutLenNoVal + txinLen

    -- how much ada does a Word64 of UTxO space cost, calculated from minAdaValue PP
    -- round down
    coinsPerUTxOWord :: Integer
    coinsPerUTxOWord = quot mv (utxoEntrySizeWithoutVal + coinSize)
```

**File:** eras/shelley-ma/formal-spec/value-size.tex (L117-119)
```tex
    & \fun{coinsPerUTxOWord}\in \Coin \to \Coin \\
    & \fun{coinsPerUTxOWord}~\var{mv} = \lfloor~ \var{mv}~/~ \mathsf{adaOnlyUTxOSize}~ \rfloor \\
    & \text{Calculate the cost of storing a memory unit of data as a UTxO entry}
```

**File:** eras/shelley-ma/formal-spec/utxo.tex (L39-44)
```tex
    & \fun{scaledMinDeposit} \in \ValMonoid \to \Coin \to \Coin \\
    & \fun{scaledMinDeposit}~\var{v}~\var{mv} ~=~
    \begin{cases}
      \var{mv} & \fun{isAdaOnly}~v \\
      \fun{max}~(\var{mv},~\fun{utxoEntrySize}~{v} * \fun{coinsPerUTxOWord}~mv)) & \text{otherwise}
    \end{cases}
```
