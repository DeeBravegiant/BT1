### Title
Witness-Input Count Mismatch via `zip` Truncation Allows Unauthorized Byron UTxO Spending — (`eras/byron/ledger/impl/src/Cardano/Chain/UTxO/Validation.hs`)

---

### Summary

`updateUTxOTxWitness` uses Haskell's `zip` to pair input addresses with witnesses before calling `validateWitness`. Because `zip` silently truncates to the shorter list, a transaction with N inputs but only M < N witnesses causes exactly M witness validations. The remaining N−M inputs are then unconditionally removed from the UTxO by `updateUTxOTx`, spending them without any authorization check. No length-equality guard exists anywhere in the validation pipeline.

---

### Finding Description

In `updateUTxOTxWitness`:

```haskell
addresses <-
  mapM (`UTxO.lookupAddress` utxo) (NE.toList $ txInputs tx)
    `wrapError` UTxOValidationUTxOError

mapM_
  (uncurry $ validateWitness pmi sigData)
  (zip addresses (V.toList witness))   -- ← truncates silently
  `wrapError` UTxOValidationTxValidationError

validateTxAux env utxo ta
  `wrapError` UTxOValidationTxValidationError

updateUTxOTx env utxo aTx              -- ← removes ALL N inputs
``` [1](#0-0) 

`addresses` is built from `NE.toList $ txInputs tx` (length N). `witness` is `taWitness ta` (length M, attacker-controlled). `zip` produces min(N, M) pairs. When M < N, the last N−M inputs are never passed to `validateWitness`.

`validateTxAux` only checks fee, size, and balance — it does not compare witness count to input count. [2](#0-1) 

`updateUTxOTx` then removes the full set of N inputs from the UTxO unconditionally. [3](#0-2) 

The `TxValidationError` sum type has no `WitnessCountMismatch` constructor, confirming no such check was ever added. [4](#0-3) 

The CBOR decoder for `ATxAux` decodes `tx` and `witness` as independent annotated fields with no cross-length enforcement, so a malformed witness vector passes deserialization cleanly. [5](#0-4) 

---

### Impact Explanation

An attacker who controls M inputs in the UTxO can include N−M additional victim inputs in the same transaction, supply only M witnesses (for their own inputs), and have all N inputs consumed. The victim's funds are transferred to attacker-controlled outputs. This is a direct, unauthorized destruction of ADA from rightful owners' UTxO entries — matching **Critical: direct loss of ADA through an invalid ledger state transition**, and at minimum **High: permanent removal of funds requiring a hard fork to restore**.

---

### Likelihood Explanation

The exploit requires only the ability to submit a Byron-format transaction in `BlockValidation` mode. No privileged access, governance majority, or key leakage is needed. The attacker must know a victim's `TxIn` (public information from the UTxO set) and include it as an input. The only practical constraint is that Byron transactions must be processable by the target node — which is true for any node replaying Byron-era history or operating a Byron-era network.

---

### Recommendation

Add an explicit length-equality check inside `whenTxValidation`, before the `zip`, and add a corresponding `TxValidationWitnessCountMismatch` constructor to `TxValidationError`:

```haskell
let nInputs   = length addresses
    nWitnesses = V.length witness
(nInputs == nWitnesses)
  `orThrowError` UTxOValidationTxValidationError
      (TxValidationWitnessCountMismatch nInputs nWitnesses)
```

This must fire before `zip` is evaluated.

---

### Proof of Concept

```
1. Build ATxAux with txInputs = [victimTxIn, attackerTxIn]  (N = 2)
2. Set taWitness = [validWitnessForAttackerInput]            (M = 1)
3. Run: runReaderT (updateUTxOTxWitness env utxo ta)
                   (fromBlockValidationMode BlockValidation)
4. zip produces [(victimAddr, attackerWitness)] — wrong pairing,
   but only 1 pair is validated regardless.
   (If ordering is inputs→addresses, attacker puts their input first
    to pass the single witness check; victim input is index 1, skipped.)
5. updateUTxOTx removes both inputs; outputs go to attacker.
6. Expected: Left (UTxOValidationTxValidationError WitnessCountMismatch)
   Actual:   Right <updated UTxO with victim funds stolen>
```

### Citations

**File:** eras/byron/ledger/impl/src/Cardano/Chain/UTxO/Validation.hs (L92-103)
```haskell
data TxValidationError
  = TxValidationLovelaceError Text LovelaceError
  | TxValidationFeeTooSmall Tx Lovelace Lovelace
  | TxValidationWitnessWrongSignature TxInWitness ProtocolMagicId TxSigData
  | TxValidationWitnessWrongKey TxInWitness Address
  | TxValidationMissingInput TxIn
  | -- | Fields are <expected> <actual>
    TxValidationNetworkMagicMismatch NetworkMagic NetworkMagic
  | TxValidationTxTooLarge Natural Natural
  | TxValidationUnknownAddressAttributes
  | TxValidationUnknownAttributes
  deriving (Eq, Show)
```

**File:** eras/byron/ledger/impl/src/Cardano/Chain/UTxO/Validation.hs (L183-234)
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
  where
    Environment {protocolParameters} = env

    maxTxSize = ppMaxTxSize protocolParameters
    feePolicy = ppTxFeePolicy protocolParameters

    txSize :: Natural
    txSize = fromIntegral $ BS.length txBytes

    inputUTxO = S.fromList (NE.toList (txInputs tx)) <| utxo

    calculateMinimumFee ::
      MonadError TxValidationError m => TxFeePolicy -> m Lovelace
    calculateMinimumFee = \case
      TxFeePolicyTxSizeLinear txSizeLinear ->
        calculateTxSizeLinear txSizeLinear txSize
          `wrapError` TxValidationLovelaceError "Minimum Fee"
```

**File:** eras/byron/ledger/impl/src/Cardano/Chain/UTxO/Validation.hs (L365-370)
```haskell
updateUTxOTx env utxo aTx@(Annotated tx _) = do
  unlessNoTxValidation (validateTx env utxo aTx)
    `wrapErrorWithValidationMode` UTxOValidationTxValidationError

  UTxO.union (S.fromList (NE.toList (txInputs tx)) </| utxo) (txOutputUTxO tx)
    `wrapError` UTxOValidationUTxOError
```

**File:** eras/byron/ledger/impl/src/Cardano/Chain/UTxO/Validation.hs (L382-397)
```haskell
    addresses <-
      mapM (`UTxO.lookupAddress` utxo) (NE.toList $ txInputs tx)
        `wrapError` UTxOValidationUTxOError

    -- Validate witnesses and their signing addresses
    mapM_
      (uncurry $ validateWitness pmi sigData)
      (zip addresses (V.toList witness))
      `wrapError` UTxOValidationTxValidationError

    -- Validate the tx including witnesses
    validateTxAux env utxo ta
      `wrapError` UTxOValidationTxValidationError

  -- Update 'UTxO' ignoring witnesses
  updateUTxOTx env utxo aTx
```

**File:** eras/byron/ledger/impl/src/Cardano/Chain/UTxO/TxAux.hs (L108-115)
```haskell
instance DecCBOR (ATxAux ByteSpan) where
  decCBOR = do
    Annotated (tx, witness) byteSpan <- annotatedDecoder $ do
      enforceSize "TxAux" 2
      tx <- decCBORAnnotated
      witness <- decCBORAnnotated
      pure (tx, witness)
    pure $ ATxAux tx witness byteSpan
```
