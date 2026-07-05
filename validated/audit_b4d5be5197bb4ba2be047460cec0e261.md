### Title
Missing Witness-Count Validation in Byron UTxO Allows Unauthorized Spending via Silent `zip` Truncation - (File: `eras/byron/ledger/impl/src/Cardano/Chain/UTxO/Validation.hs`)

---

### Summary

The Byron-era concrete witness validation in `updateUTxOTxWitness` uses Haskell's `zip` to pair transaction inputs with their witnesses before calling `validateWitness`. Because `zip` silently truncates to the shorter list, a transaction that supplies fewer witnesses than inputs will have only the first `length witness` inputs validated. All remaining inputs are accepted and spent with no witness check whatsoever. The abstract executable specification explicitly guards against this with a `length wits == length ins` check, but the concrete production implementation omits it entirely.

---

### Finding Description

In `updateUTxOTxWitness` the witness validation loop is:

```haskell
mapM_
  (uncurry $ validateWitness pmi sigData)
  (zip addresses (V.toList witness))
  `wrapError` UTxOValidationTxValidationError
```

`addresses` is built from `NE.toList $ txInputs tx` and always has exactly N elements (one per input). `witness = taWitness ta` is a `Vector TxInWitness` whose length is entirely attacker-controlled. Haskell's `zip` produces a list of length `min(N, M)` where M is the witness vector length. When M < N the extra `N − M` inputs are silently dropped from the validation loop and are never checked.

The abstract specification in `Byron.Spec.Ledger.STS.UTXOW` explicitly prevents this:

```haskell
witnessed (Tx tx wits) utxo =
  length wits == length ins && all (isWitness tx utxo) (zip ins wits)
```

The concrete production code has no equivalent length-equality guard before the `zip`.

Neither `validateTx` nor `validateTxAux` fills this gap: `validateTx` checks only that inputs exist in the UTxO, network magic, and attribute size; `validateTxAux` checks only fee, transaction size, and Lovelace balance. None of these checks require a witness for every input.

---

### Impact Explanation

An attacker who controls one UTxO entry can craft a Byron `TxAux` that:

1. Lists N inputs — one they own plus N−1 they do not own.
2. Provides exactly 1 `TxInWitness` (a valid signature for their own input).
3. Directs all N input values to attacker-controlled outputs.

`zip addresses [attacker_witness]` produces a single pair. `mapM_ validateWitness` validates only that pair. The remaining N−1 inputs pass through `updateUTxOTx` and are removed from the UTxO and credited to the attacker. This is a direct, unconditional loss of ADA for the owners of those N−1 UTxOs — matching the **Critical** impact tier: *Direct loss of ADA through an invalid ledger state transition*.

---

### Likelihood Explanation

The Byron era is the genesis era of the Cardano chain and its UTxO validation path (`updateUTxO` → `updateUTxOTxWitness`) is still exercised by any node that replays the Byron portion of the chain. A serialized `ATxAux` with a mismatched witness vector is trivially constructible by any transaction sender: the CBOR encoding of `TxAux` is a 2-element list `[tx, witness_vector]` and the witness vector length is not constrained by the serialization layer. No privileged access, key leakage, or consensus majority is required.

---

### Recommendation

Insert an explicit length check before the `zip`, mirroring the abstract specification:

```haskell
let nInputs   = length addresses
    nWitnesses = V.length witness
unless (nInputs == nWitnesses) $
  throwError $ UTxOValidationTxValidationError
    (TxValidationWitnessCountMismatch nInputs nWitnesses)
```

A corresponding `TxValidationWitnessCountMismatch` constructor should be added to `TxValidationError`.

---

### Proof of Concept

**Root cause — `zip` truncation (no length guard):** [1](#0-0) 

**Abstract spec that correctly guards with `length wits == length ins`:** [2](#0-1) 

**`validateTx` — no witness-count check:** [3](#0-2) 

**`validateTxAux` — no witness-count check:** [4](#0-3) 

**Attack path:** Construct a `TxAux` where `aTaTx` contains N inputs (1 owned, N−1 foreign) and `aTaWitness` is a vector of length 1. Submit via the standard Byron transaction submission path. `updateUTxOTxWitness` validates only the first (input, witness) pair; the remaining N−1 inputs are spent unconditionally, transferring their Lovelace to attacker-controlled outputs.

### Citations

**File:** eras/byron/ledger/impl/src/Cardano/Chain/UTxO/Validation.hs (L183-217)
```haskell
validateTxAux ::
  MonadError TxValidationError m =>
  Environment ->
  UTxO ->
  ATxAux ByteString ->
  m ()
validateTxAux env utxo (ATxAux (Annotated tx _) _ txBytes) = do
  -- Check that the size of the transaction is less than the maximum
  txSize
    <= maxTxSize
    `orThrowError` TxValidationTxTooLarge txSize maxTxSize

  -- Calculate the minimum fee from the 'TxFeePolicy'
  minFee <-
    if isRedeemUTxO inputUTxO
      then pure $ mkKnownLovelace @0
      else calculateMinimumFee feePolicy

  -- Calculate the balance of the output 'UTxO'
  balanceOut <-
    balance (txOutputUTxO tx)
      `wrapError` TxValidationLovelaceError "Output Balance"

  -- Calculate the balance of the restricted input 'UTxO'
  balanceIn <-
    balance inputUTxO
      `wrapError` TxValidationLovelaceError "Input Balance"

  -- Calculate the 'fee' as the difference of the balances
  fee <-
    subLovelace balanceIn balanceOut
      `wrapError` TxValidationLovelaceError "Fee"

  -- Check that the fee is greater than the minimum
  (minFee <= fee) `orThrowError` TxValidationFeeTooSmall tx minFee fee
```

**File:** eras/byron/ledger/impl/src/Cardano/Chain/UTxO/Validation.hs (L241-260)
```haskell
validateTx ::
  MonadError TxValidationError m =>
  Environment ->
  UTxO ->
  Annotated Tx ByteString ->
  m ()
validateTx env utxo (Annotated tx _) = do
  -- Check that the transaction attributes are less than the max size
  unknownAttributesLength (txAttributes tx)
    < 128
    `orThrowError` TxValidationUnknownAttributes

  -- Check that outputs have valid NetworkMagic
  let nm = makeNetworkMagic protocolMagic
  txOutputs tx `forM_` validateTxOutNM nm

  -- Check that every input is in the domain of 'utxo'
  txInputs tx `forM_` validateTxIn utxoConfiguration utxo
  where
    Environment {protocolMagic, utxoConfiguration} = env
```

**File:** eras/byron/ledger/impl/src/Cardano/Chain/UTxO/Validation.hs (L386-390)
```haskell
    -- Validate witnesses and their signing addresses
    mapM_
      (uncurry $ validateWitness pmi sigData)
      (zip addresses (V.toList witness))
      `wrapError` UTxOValidationTxValidationError
```

**File:** eras/byron/ledger/executable-spec/src/Byron/Spec/Ledger/STS/UTXOW.hs (L97-103)
```haskell
witnessed :: Tx -> UTxO -> Bool
witnessed (Tx tx wits) utxo =
  length wits == length ins && all (isWitness tx utxo) (zip ins wits)
  where
    ins = inputs tx
    isWitness tx' unspent (input, Wit key sig) =
      verify key tx' sig && authTxin key input unspent
```
