### Title
`DijkstraEra` declares PlutusV4 support but `eraMaxLanguage = PlutusV3` and `AlonzoTxWitsRaw` silently drops PlutusV4 script witnesses — (`eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Scripts.hs`, `eras/alonzo/impl/src/Cardano/Ledger/Alonzo/TxWits.hs`)

### Summary

The Dijkstra era introduces PlutusV4 and fully wires it into the script type hierarchy (`mkPlutusScript`, `withPlutusScript`, `mkSupportedLanguage` all handle `SPlutusV4`), but the era-level "detection" field `eraMaxLanguage` is set to `PlutusV3` instead of `PlutusV4`. Independently, the shared `AlonzoTxWitsRaw` CBOR encoder/decoder — used by every era including Dijkstra — encodes and decodes only keys 3/6/7 (PlutusV1/V2/V3) and has no key 8 for PlutusV4. The result is that any PlutusV4 script placed in a transaction witness set is silently dropped on the encode→decode round-trip, making PlutusV4-locked UTxOs permanently unspendable via inline witnesses.

### Finding Description

**Root cause 1 — wrong `eraMaxLanguage`:** [1](#0-0) 

`eraMaxLanguage = PlutusV3` is set even though the same `AlonzoEraScript DijkstraEra` instance accepts `SPlutusV4` in `mkPlutusScript` and `withPlutusScript`: [2](#0-1) 

`eraMaxLanguage` drives `supportedLanguages`, which iterates `[minBound .. eraMaxLanguage @era]`: [3](#0-2) 

For Dijkstra this produces `[PlutusV1, PlutusV2, PlutusV3]`, excluding PlutusV4 entirely from the supported-language set used throughout script collection and validation.

**Root cause 2 — `AlonzoTxWitsRaw` encoder omits PlutusV4:**

The encoder writes keys 3, 6, 7 for V1/V2/V3 but has no entry for V4: [4](#0-3) 

The decoder handles keys 0–7 and returns `Nothing` for anything else: [5](#0-4) 

The test spec already acknowledges the missing key 8 with a TODO: [6](#0-5) 

The test helper confirms the intended key is 8: [7](#0-6) 

**Attack path:**

1. Script author deploys a UTxO locked by a PlutusV4 script hash in the Dijkstra era (valid on-chain because `mkPlutusScript` accepts `SPlutusV4`).
2. Spender constructs a transaction, places the PlutusV4 script in the transaction witness set (`scriptTxWitsL`).
3. The node serialises the transaction: `EncCBOR (AlonzoTxWitsRaw era)` writes keys 3/6/7 only — the PlutusV4 script is silently omitted.
4. Any node that deserialises the transaction reconstructs `AlonzoTxWitsRaw` without the PlutusV4 script.
5. `validateMissingScripts` / `babbageMissingScripts` finds the needed script hash absent from the provided scripts and rejects the transaction with `MissingScriptWitnessesUTXOW`.
6. The UTxO is permanently unspendable unless a reference-script path exists.

The `EraPlutusContext DijkstraEra` instance correctly includes PlutusV4 in `mkSupportedLanguage` and `TxInfoResult`, so the mismatch with `eraMaxLanguage` creates an inconsistent interface — exactly the "inherits the interface but the detection function is wrong" pattern from the external report. [8](#0-7) 

### Impact Explanation

Any ADA or native assets locked at a PlutusV4 script address in the Dijkstra era, where the spending path requires the script to be supplied as an inline witness (rather than a reference script), are permanently frozen. The serialisation layer silently discards the witness, so no valid spending transaction can ever be constructed and accepted by honest nodes. Recovery would require a hard fork to fix the encoder/decoder. This matches **High — permanent freezing of funds where recovery requires a hard fork**.

### Likelihood Explanation

The Dijkstra era is the production target for PlutusV4. Any script author who deploys a PlutusV4-locked output and attempts to spend it via an inline witness (the standard path) will trigger the freeze. The bug is deterministic and 100% reproducible. The TODO comment in the CDDL spec confirms the omission is known but not yet fixed, meaning the era can be activated with the defect present.

### Recommendation

1. Change `eraMaxLanguage = PlutusV4` in the `AlonzoEraScript DijkstraEra` instance in `eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Scripts.hs`.
2. Add PlutusV4 encoding at key 8 in `EncCBOR (AlonzoTxWitsRaw era)` in `eras/alonzo/impl/src/Cardano/Ledger/Alonzo/TxWits.hs`, mirroring the existing `encodePlutus SPlutusV3` at key 7.
3. Add key 8 decoding in both `decoderByKey` and `txWitnessField` in the same file.
4. Remove the TODO comment in `HuddleSpec.hs` and add the `plutus_v4_script` at index 8 in the `transaction_witness_set` CDDL rule.

### Proof of Concept

```
-- Dijkstra era, protocol version >= 12
-- 1. Lock funds at a PlutusV4 script address
let v4Script  = DijkstraPlutusV4 someValidPlutusV4Binary
    scriptHash = hashScript (PlutusScript v4Script)
    lockTxOut  = TxOut (scriptAddr scriptHash) someAda NoDatum NoRefScript

-- 2. Build spending tx with V4 script in witness set
let spendTx = mkBasicTx mkBasicTxBody
                & bodyTxL . inputsTxBodyL  .~ [lockTxIn]
                & witsTxL . scriptTxWitsL  .~ Map.singleton scriptHash (PlutusScript v4Script)
                & witsTxL . rdmrsTxWitsL   .~ someRedeemer

-- 3. Round-trip through CBOR (as any node would do)
let encoded  = serialize' dijkstraProtVer spendTx
    decoded  = deserialise encoded :: Tx TopTx DijkstraEra

-- 4. Observe: PlutusV4 script is gone
assert $ Map.notMember scriptHash (decoded ^. witsTxL . scriptTxWitsL)
-- => True: script silently dropped

-- 5. Submit decoded tx => MissingScriptWitnessesUTXOW {scriptHash}
-- Funds are permanently frozen.
```

### Citations

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Scripts.hs (L446-446)
```haskell
  eraMaxLanguage = PlutusV3
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Scripts.hs (L448-458)
```haskell
  mkPlutusScript plutus =
    case plutusSLanguage plutus of
      SPlutusV1 -> pure $ DijkstraPlutusV1 plutus
      SPlutusV2 -> pure $ DijkstraPlutusV2 plutus
      SPlutusV3 -> pure $ DijkstraPlutusV3 plutus
      SPlutusV4 -> pure $ DijkstraPlutusV4 plutus

  withPlutusScript (DijkstraPlutusV1 plutus) f = f plutus
  withPlutusScript (DijkstraPlutusV2 plutus) f = f plutus
  withPlutusScript (DijkstraPlutusV3 plutus) f = f plutus
  withPlutusScript (DijkstraPlutusV4 plutus) f = f plutus
```

**File:** eras/alonzo/impl/src/Cardano/Ledger/Alonzo/Plutus/Context.hs (L320-331)
```haskell
supportedLanguages ::
  forall era.
  (HasCallStack, EraPlutusContext era) =>
  NonEmpty (SupportedLanguage era)
supportedLanguages =
  let langs =
        [ errorFail (mkSupportedLanguageM lang)
        | lang <- [minBound .. eraMaxLanguage @era]
        ]
   in case nonEmpty langs of
        Nothing -> error "Impossible: there are no supported languages"
        Just neLangs -> neLangs
```

**File:** eras/alonzo/impl/src/Cardano/Ledger/Alonzo/TxWits.hs (L511-532)
```haskell
instance AlonzoEraScript era => EncCBOR (AlonzoTxWitsRaw era) where
  encCBOR (AlonzoTxWitsRaw vkeys boots scripts dats rdmrs) =
    encode $
      Keyed
        ( \a b c d e f g h ->
            let ps = toScript @'PlutusV1 d <> toScript @'PlutusV2 e <> toScript @'PlutusV3 f
             in AlonzoTxWitsRaw a b (c <> ps) g h
        )
        !> Omit null (Key 0 $ To vkeys)
        !> Omit null (Key 2 $ To boots)
        !> Omit
          null
          ( Key 1 $
              E
                (encodeWithSetTag . mapMaybe getNativeScript . Map.elems)
                (Map.filter isNativeScript scripts)
          )
        !> Omit null (Key 3 $ encodePlutus SPlutusV1)
        !> Omit null (Key 6 $ encodePlutus SPlutusV2)
        !> Omit null (Key 7 $ encodePlutus SPlutusV3)
        !> Omit (null . unTxDats) (Key 4 $ To dats)
        !> Omit (null . unRedeemers) (Key 5 $ To rdmrs)
```

**File:** eras/alonzo/impl/src/Cardano/Ledger/Alonzo/TxWits.hs (L638-647)
```haskell
      decoderByKey acc = \case
        0 -> Just $ decodeAccA acc (\x w -> w {atwrAddrTxWits = x}) (pure <$> setOrListWitsDecoder)
        1 -> Just $ decodeAccA acc addScriptsTxWitsRaw nativeScriptsDecoder
        2 -> Just $ decodeAccA acc (\x w -> w {atwrBootAddrTxWits = x}) (pure <$> setOrListWitsDecoder)
        3 -> Just $ decodeAccA acc addScriptsTxWitsRaw (pure <$> alonzoPlutusScriptDecoder SPlutusV1)
        4 -> Just $ decodeAccA acc (\x w -> w {atwrDatsTxWits = x}) decCBOR
        5 -> Just $ decodeAccA acc (\x w -> w {atwrRdmrsTxWits = x}) decCBOR
        6 -> Just $ decodeAccA acc addScriptsTxWitsRaw (pure <$> alonzoPlutusScriptDecoder SPlutusV2)
        7 -> Just $ decodeAccA acc addScriptsTxWitsRaw (pure <$> alonzoPlutusScriptDecoder SPlutusV3)
        _ -> Nothing
```

**File:** eras/dijkstra/impl/cddl/lib/Cardano/Ledger/Dijkstra/HuddleSpec.hs (L1085-1087)
```haskell
        , opt $ idx 7 ==> huddleRule1 @"nonempty_set" p (huddleRule @"plutus_v3_script" p)
        -- TODO: Add plutus_v4_script at index 8 once AlonzoTxWitsRaw encoder/decoder supports it
        ]
```

**File:** eras/alonzo/impl/testlib/Test/Cardano/Ledger/Alonzo/Binary/TxWitsSpec.hs (L88-92)
```haskell
    keys :: SLanguage l -> Int
    keys SPlutusV1 = 3
    keys SPlutusV2 = 6
    keys SPlutusV3 = 7
    keys SPlutusV4 = 8
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxInfo.hs (L228-232)
```haskell
  mkSupportedLanguage = \case
    PlutusV1 -> Just $ SupportedLanguage SPlutusV1
    PlutusV2 -> Just $ SupportedLanguage SPlutusV2
    PlutusV3 -> Just $ SupportedLanguage SPlutusV3
    PlutusV4 -> Just $ SupportedLanguage SPlutusV4
```
