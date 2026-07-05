### Title
`tierRefScriptFee` Rounds Down (Floor) Instead of Up (Ceiling), Undercharging Reference Script Fees in Favor of Transaction Submitter — (`File: eras/conway/impl/src/Cardano/Ledger/Conway/Tx.hs`)

---

### Summary

The Conway-era reference script fee function `tierRefScriptFee` uses `floor` when converting the final rational fee amount to an integer `Coin`. All other fee-related calculations in the ledger (e.g., `txscriptfee`, `validateInsufficientCollateral`) round **up** (ceiling) in favor of the protocol. Using `floor` here rounds in favor of the transaction submitter, allowing every transaction that uses reference scripts to underpay the minimum fee by up to 1 lovelace. This is the direct Cardano analog of the ERC-4626 `previewMint`/`previewWithdraw` rounding-direction bug.

---

### Finding Description

`tierRefScriptFee` is the function that computes the additional minimum fee charged for reference scripts in the Conway era. It is called from `getConwayMinFeeTx`, which is the `getMinFeeTx` implementation for `ConwayEra`. [1](#0-0) 

The terminal case of the recursion is:

```haskell
go !acc !curTierPrice !n
  | n < sizeIncrement =
      Coin $ floor (acc + toRational n * curTierPrice)   -- ← floor, rounds DOWN
```

The exact rational value `acc + toRational n * curTierPrice` is truncated toward zero. When this value is non-integer (which is the common case, since `curTierPrice` is a `Rational` derived from `ppMinFeeRefScriptCostPerByteL`), the computed minimum fee is **strictly less** than the true rational fee. A transaction submitter can therefore pay 1 lovelace less than the protocol intends to collect.

This is inconsistent with every other fee-rounding site in the codebase:

- The Alonzo formal spec mandates `ceiling` for `txscriptfee`: [2](#0-1) 

- The Alonzo implementation uses `rationalToCoinViaCeiling` for the required collateral amount: [3](#0-2) 

- `rationalToCoinViaFloor` and `rationalToCoinViaCeiling` are both defined in the core library precisely to make the rounding direction explicit: [4](#0-3) 

The established protocol convention is: **fees round up (ceiling) in favor of the protocol; rewards round down (floor) in favor of the protocol**. `tierRefScriptFee` violates this convention by rounding a fee down.

---

### Impact Explanation

**Allowed impact matched:** *Medium — Attacker-controlled transactions modify fees outside design parameters.*

Every Conway-era transaction that includes reference scripts has its minimum fee computed via `tierRefScriptFee`. Because the result is floored, the minimum fee is at most 1 lovelace below the exact rational value. The transaction submitter can include exactly the floored amount as the fee field, and the ledger will accept it. The fee pot therefore collects up to 1 lovelace less per such transaction than the protocol intends.

Over the lifetime of the chain, with millions of reference-script transactions, this represents a continuous, systematic underpayment to the fee pot (which feeds into the reward pot and treasury). The fee is modified outside design parameters on every affected transaction.

---

### Likelihood Explanation

**High.** Reference scripts are a widely used feature in Conway. Any transaction that spends a UTxO locked by a Plutus script via a reference input triggers this code path. No special privileges, keys, or governance actions are required — any unprivileged transaction submitter benefits automatically. The rounding error occurs on every such transaction where the exact rational fee is non-integer, which is the common case given that `ppMinFeeRefScriptCostPerByteL` is a `NonNegativeInterval` (rational) parameter.

---

### Recommendation

Replace `floor` with `ceiling` in the terminal case of `tierRefScriptFee`:

```haskell
-- Before (rounds in favor of user):
Coin $ floor (acc + toRational n * curTierPrice)

-- After (rounds in favor of protocol, consistent with txscriptfee):
Coin $ ceiling (acc + toRational n * curTierPrice)
```

This aligns `tierRefScriptFee` with the established ledger convention that fee calculations round up, and with the formal spec's use of `ceiling` for `txscriptfee`.

---

### Proof of Concept

Consider `ppMinFeeRefScriptCostPerByteL = 15 % 1` (the current mainnet value) and a reference script of size `n = 1` byte (within the first tier, so `sizeIncrement = 25600`):

```
acc = 0
curTierPrice = 15 % 1
n = 1

exact = 0 + 1 * (15 % 1) = 15 % 1 = 15.0  (integer, no difference here)
```

Now consider `ppMinFeeRefScriptCostPerByteL = 15 % 2` (a rational value) and `n = 1`:

```
exact = 0 + 1 * (15 % 2) = 15 % 2 = 7.5

floor(7.5)   = 7   ← current behavior, user pays 7 lovelace
ceiling(7.5) = 8   ← correct behavior, user pays 8 lovelace
```

The user underpays by 1 lovelace. With `n = 25599` bytes (just under one full tier):

```
exact = 25599 * (15 % 2) = 383985 % 2 = 191992.5

floor(191992.5)   = 191992  ← current
ceiling(191992.5) = 191993  ← correct
```

Still 1 lovelace underpayment per transaction. Across 10 million such transactions, the fee pot is short ~10 ADA — a slow, continuous, protocol-wide value leak triggered by any unprivileged transaction submitter using reference scripts. [5](#0-4)

### Citations

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Tx.hs (L116-136)
```haskell
tierRefScriptFee ::
  HasCallStack =>
  -- | Growth factor or step multiplier
  Rational ->
  -- | Increment size in which price grows linearly according to the price
  Int ->
  -- | Base fee. Currently this is customizable by `ppMinFeeRefScriptCostPerByteL`
  Rational ->
  -- | Total RefScript size in bytes
  Int ->
  Coin
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

**File:** eras/alonzo/formal-spec/utxo.tex (L32-34)
```tex
    & \fun{txscriptfee} : \Prices \to \ExUnits \to \Coin \\
    & \fun{txscriptfee}~(pr_{mem}, pr_{steps})~ (\var{mem, steps})
    = \fun{ceiling}~(\var{pr_{mem}}*\var{mem} + \var{pr_{steps}}*\var{steps})
```

**File:** eras/alonzo/impl/src/Cardano/Ledger/Alonzo/Rules/Utxo.hs (L343-350)
```haskell
validateInsufficientCollateral pp txBody bal =
  failureUnless (Val.scale (100 :: Int) bal >= Val.scale collPerc (toDeltaCoin txfee)) $
    InsufficientCollateral bal $
      rationalToCoinViaCeiling $
        (fromIntegral collPerc * unCoin txfee) %. knownNonZero @100
  where
    txfee = txBody ^. feeTxBodyL -- Coin supplied to pay fees
    collPerc = pp ^. ppCollateralPercentageL
```

**File:** libs/cardano-ledger-core/src/Cardano/Ledger/Coin.hs (L141-145)
```haskell
rationalToCoinViaFloor :: Rational -> Coin
rationalToCoinViaFloor = Coin . floor

rationalToCoinViaCeiling :: Rational -> Coin
rationalToCoinViaCeiling = Coin . ceiling
```
