Looking at the Dijkstra era's fee accounting for sub-transactions, I found a concrete analog.

### Title
Sub-Transaction Reference Script Sizes Excluded from Minimum Fee Calculation — (File: `eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs`)

---

### Summary

The Dijkstra era introduces nested sub-transactions. A `batchNonDistinctRefScriptsSize` helper was created specifically to measure reference-script overhead across the top-level transaction **and all its sub-transactions**, but the minimum-fee validation still delegates to `getConwayMinFeeTxUtxo`, which only measures the top-level transaction's reference scripts. Sub-transaction reference-script sizes are therefore silently treated as zero in the fee check, allowing an attacker to pay less than the protocol-intended minimum fee.

---

### Finding Description

**Root cause — `getMinFeeTxUtxo` is wired to the Conway implementation:** [1](#0-0) 

```haskell
getMinFeeTxUtxo = getConwayMinFeeTxUtxo
```

`getConwayMinFeeTxUtxo` internally calls `txNonDistinctRefScriptsSize`, which only inspects the top-level transaction's `inputsTxBodyL` and `referenceInputsTxBodyL`. Sub-transactions carry their own independent input sets (`dstbrSpendInputs`, `dstbrReferenceInputs`) that are never visited by this function.

**The batch-aware helper exists but is unused in fee validation:** [2](#0-1) 

```haskell
-- | Total size of reference scripts across a top-level transaction
--   and all its subtransactions.
batchNonDistinctRefScriptsSize utxo tx =
  txNonDistinctRefScriptsSize utxo tx
    + getSum
      ( foldMap'
          (Sum . txNonDistinctRefScriptsSize utxo)
          (tx ^. bodyTxL . subTransactionsTxBodyL)
      )
```

`batchNonDistinctRefScriptsSize` was written precisely to handle the Dijkstra batch case, yet the fee-validation path never calls it.

**Fee validation in the Dijkstra UTXO rule:** [3](#0-2) 

```haskell
{- minfee pp txTop utxo₀ ≤ txfee txb -}
runTest $ Shelley.validateFeeTooSmallUTxO pp tx originalUtxo
```

`validateFeeTooSmallUTxO` calls `getMinFeeTxUtxo`, which resolves to `getConwayMinFeeTxUtxo`. The reference-script component of the minimum fee is therefore computed without any contribution from sub-transaction reference inputs.

**Sub-transaction body has no fee field** — the design is that the single top-level fee covers the whole batch: [4](#0-3) 

Because sub-transactions have no fee field, the top-level fee is the only place where the protocol can recover the cost of deserialising sub-transaction reference scripts. The missing `batchNonDistinctRefScriptsSize` call means that cost is never enforced.

**Analogy to the external report:** just as `Ext01Handler` hardcodes `executionFee: uint128(0)` instead of `_params.executionFee`, the Dijkstra fee check effectively treats sub-transaction reference-script sizes as zero by using `getConwayMinFeeTxUtxo` instead of a Dijkstra-aware variant that calls `batchNonDistinctRefScriptsSize`.

---

### Impact Explanation

An attacker can craft a top-level Dijkstra transaction whose sub-transactions carry reference inputs pointing to UTxOs that hold large Plutus scripts. The top-level fee is validated only against the top-level transaction's reference-script overhead; the sub-transaction overhead is invisible to the check. The attacker therefore pays a fee that is lower than the protocol-intended minimum for the actual deserialization work imposed on every validating node. This is the same class of under-priced reference-script attack that caused the June 2024 DDoS on Cardano mainnet and motivated the introduction of `minFeeRefScriptCostPerByte` in Conway.

**Allowed impact matched:** *Medium — attacker-controlled transactions modify fees outside design parameters.* [5](#0-4) 

---

### Likelihood Explanation

Any unprivileged transaction author can construct a Dijkstra top-level transaction with sub-transactions that include reference inputs. No special privilege, key, or governance majority is required. The attacker only needs to have (or create) UTxOs containing large scripts and reference them from sub-transaction `referenceInputsTxBodyL`. The entry path is fully attacker-controlled.

---

### Recommendation

Replace the Dijkstra `EraUTxO` instance's `getMinFeeTxUtxo` with a Dijkstra-specific implementation that substitutes `batchNonDistinctRefScriptsSize` for `txNonDistinctRefScriptsSize` when computing the reference-script component of the minimum fee:

```haskell
-- In Cardano.Ledger.Dijkstra.UTxO
getDijkstraMinFeeTxUtxo :: PParams DijkstraEra -> Tx TopTx DijkstraEra -> UTxO DijkstraEra -> Coin
getDijkstraMinFeeTxUtxo pp tx utxo =
  getMinFeeTx pp tx (batchNonDistinctRefScriptsSize utxo tx)

instance EraUTxO DijkstraEra where
  ...
  getMinFeeTxUtxo = getDijkstraMinFeeTxUtxo
```

The same correction should be applied to any per-block reference-script size limit check if it also uses the single-transaction variant.

---

### Proof of Concept

1. Deploy a UTxO `U` containing a large Plutus script (e.g., 100 KiB), making it available as a reference input.
2. Construct a Dijkstra top-level transaction `T` whose body contains a sub-transaction `S`. `S` includes `U` in its `referenceInputsTxBodyL`.
3. Set `T`'s fee to the value returned by `getConwayMinFeeTxUtxo pp T utxo` — this ignores `U`'s script size.
4. Submit `T`. The ledger calls `Shelley.validateFeeTooSmallUTxO pp T originalUtxo`, which calls `getMinFeeTxUtxo = getConwayMinFeeTxUtxo`. The check passes because `U` is only reachable via `S`'s reference inputs, which `txNonDistinctRefScriptsSize` never visits.
5. Every validating node must deserialise the 100 KiB script from `U` to validate `S`, but the fee collected does not include the `minFeeRefScriptCostPerByte` surcharge for that script — the attacker has paid less than the protocol-intended minimum fee. [6](#0-5)

### Citations

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs (L124-141)
```haskell
instance EraUTxO DijkstraEra where
  type ScriptsNeeded DijkstraEra = AlonzoScriptsNeeded DijkstraEra

  consumed = conwayConsumed

  getConsumedValue = getConsumedDijkstraValue

  getProducedValue = getProducedDijkstraValue

  getScriptsProvided = getDijkstraScriptsProvided

  getScriptsNeeded = getDijkstraScriptsNeeded

  getScriptsHashesNeeded = getAlonzoScriptsHashesNeeded

  getWitsVKeyNeeded _ = getConwayWitsVKeyNeeded

  getMinFeeTxUtxo = getConwayMinFeeTxUtxo
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

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Utxo.hs (L372-373)
```haskell
  {- minfee pp txTop utxo₀ ≤ txfee txb -}
  runTest $ Shelley.validateFeeTooSmallUTxO pp tx originalUtxo
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxBody.hs (L189-208)
```haskell
  DijkstraSubTxBodyRaw ::
    { dstbrSpendInputs :: !(Set TxIn)
    , dstbrReferenceInputs :: !(Set TxIn)
    , dstbrOutputs :: !(StrictSeq (Sized (TxOut era)))
    , dstbrCerts :: !(OSet.OSet (TxCert era))
    , dstbrWithdrawals :: !Withdrawals
    , dstbrVldt :: !ValidityInterval
    , dstbrGuards :: !(OSet (Credential Guard))
    , dstbrMint :: !MultiAsset
    , dstbrScriptIntegrityHash :: !(StrictMaybe ScriptIntegrityHash)
    , dstbrAuxDataHash :: !(StrictMaybe TxAuxDataHash)
    , dstbrNetworkId :: !(StrictMaybe Network)
    , dstbrVotingProcedures :: !(VotingProcedures era)
    , dstbrProposalProcedures :: !(OSet.OSet (ProposalProcedure era))
    , dstbrCurrentTreasuryValue :: !(StrictMaybe Coin)
    , dstbrTreasuryDonation :: !Coin
    , dstbrRequiredTopLevelGuards :: !(Map (Credential Guard) (StrictMaybe (Data era)))
    , dstbrDirectDeposits :: !DirectDeposits
    , dstbrAccountBalanceIntervals :: !(AccountBalanceIntervals era)
    } ->
```

**File:** docs/adr/2024-08-14_009-refscripts-fee-change.md (L15-19)
```markdown
It was identified a while ago that there is an overhead associated with deserializing scripts, so much so that it would be possible to create a very large script that was fairly expensive to deserialize, but very cheap to execute. This opened up an attack vector when such a Plutus script would be used as a reference script. This problem was exacerbated by the fact that there was no real limit on the total size of reference scripts that could be used in a transaction, thus being limited only by the size of the transaction itself. Therefore this opened up Cardano to a DDoS attack where an attacker could submit many such transactions that would cost very little, but would be expensive for a `cardano-node` to validate.

In order to prevent such an attack a `"minFeeRefScriptCostPerByte"` protocol parameter was introduced in the Conway era. The idea was fairly simple: we would calculate the total size of reference scripts used by a transaction and multiply it by the value specified by this protocol parameter. Result would be added to the transaction fee. However, in order for this approach to be a definite deterrent of such attacks this parameter would have to be set to a fairly high value. Community was not very keen on having this value set to a high enough value, since that could make reference scripts almost as expensive to use as regular scripts, thus significantly reducing their usability. Taking this fact into consideration, a decision was made to set this parameter to a fairly moderate value to at l ... (truncated)

Unfortunately things did not go exactly as planned, because on [June 25th 2024 an attack like this actually took place](https://cardanospot.io/news/ddos-attack-on-the-cardano-blockchain-mRIKAzZTNnzq5NGd). This attack forced us to make a quick decision on implementing a definite prevention of such attacks, while trying to not have a drastic impact on the common use case that DApp developers rely on so much.
```
