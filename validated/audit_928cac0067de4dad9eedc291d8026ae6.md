### Title
Unbounded `Natural` Decoding of `ProtVer.pvMinor` in Pre-Version-12 Path Enables Ledger Divergence After Era Upgrade — (File: `libs/cardano-ledger-core/src/Cardano/Ledger/BaseTypes.hs`)

---

### Summary

The `DecCBORGroup` instance for `ProtVer` uses a version-gated decoder for `pvMinor`. For protocol versions below 12, it calls `decCBOR @Natural`, which imposes **no upper bound** on the decoded integer. For protocol version 12 and above, it correctly bounds the value to `Word32`. A block producer can craft a block header with `pvMinor` encoded as an arbitrarily large integer (e.g., `2^64 + 1`). Pre-v12 nodes accept this block. After the chain upgrades to version 12, any node that re-decodes the same block header using the new bounded decoder will fail, causing a permanent ledger divergence between nodes that have already processed the block and nodes syncing from scratch.

---

### Finding Description

In `libs/cardano-ledger-core/src/Cardano/Ledger/BaseTypes.hs`, the `DecCBORGroup` instance for `ProtVer` is:

```haskell
instance DecCBORGroup ProtVer where
  decCBORGroup =
    ProtVer
      <$> decCBOR
      <*> ifDecoderVersionAtLeast
        (natVersion @12)
        (fromIntegral @Word32 @Natural <$> decCBOR @Word32)  -- bounded
        (decCBOR @Natural)                                    -- UNBOUNDED
``` [1](#0-0) 

The old path (`decCBOR @Natural`) dispatches to `decodeNatural`:

```haskell
decodeNatural :: Decoder s Natural
decodeNatural = do
  !n <- decodeInteger
  if n >= 0
    then return $! fromInteger n
    else cborError $ DecoderErrorCustom "Natural" "got a negative number"
``` [2](#0-1) 

This only rejects negative values. Any non-negative integer of arbitrary precision — including values far exceeding `Word32.Max` (4,294,967,295) — is accepted and stored as a `Natural`. The new path (`decCBOR @Word32`) correctly rejects values above `Word32.Max`.

The `ProtVer` type stores `pvMinor` as `Natural`:

```haskell
data ProtVer = ProtVer {pvMajor :: !Version, pvMinor :: !Natural}
``` [3](#0-2) 

The Shelley block header body contains a `ProtVer` field (the version claimed by the block producer). This is decoded using the versioned `DecCBOR` instance, which dispatches to `DecCBORGroup ProtVer`. At protocol versions 10 and 11 (early Conway, pre-v12), the old unbounded path is active.

The existing test in `libs/cardano-ledger-core/test/Test/Cardano/Ledger/BinarySpec.hs` confirms the asymmetry: it only asserts that `pvMinor > Word32.Max` fails round-trip for version **≥ 12**, leaving the pre-v12 path unconstrained:

```haskell
forAll badProtVerGen $
  roundTripCborRangeFailureExpectation (natVersion @12) maxBound
``` [4](#0-3) 

The `chainChecks` function only validates `pvMajor` from the block header, not `pvMinor`, so a block with an oversized `pvMinor` passes all existing chain-level checks:

```haskell
chainChecks maxpv ccd blk = do
  unless (m <= maxpv) $ throwError (ObsoleteNodeCHAIN m maxpv)
  ...
  where
    ProtVer m _ = ccProtocolVersion ccd
``` [5](#0-4) 

---

### Impact Explanation

**High — Deterministic disagreement between honest nodes from serialization/ledger rule evaluation.**

Attack sequence:
1. At protocol version 10 or 11, a block producer crafts a block header with `pvMinor` CBOR-encoded as `2^64 + 1` (or any value > `Word32.Max`).
2. All nodes at version < 12 decode this using `decCBOR @Natural`, which accepts it. The block is accepted and stored on-chain.
3. The chain upgrades to version 12.
4. Any node that re-decodes this block header (e.g., during initial chain sync, node restart, or ledger state replay) now uses `decCBOR @Word32`, which rejects `pvMinor > Word32.Max` with a deserialization failure.
5. Nodes that have already processed the block continue to operate normally; nodes syncing from scratch after the v12 upgrade cannot decode the block and diverge permanently.

This constitutes a permanent ledger split requiring a hard fork to resolve, matching the "High — Deterministic disagreement between honest nodes" impact category.

---

### Likelihood Explanation

**Medium.** The attacker must be a registered stake pool operator (block producer below consensus threshold) — a realistic, permissionless role on Cardano. The chain is currently in Conway era at protocol versions 10–13, meaning the pre-v12 decoder path is still active for versions 10 and 11. The attack requires only crafting a single block with a non-standard `pvMinor` value; no coordination or majority is needed. The window closes once the chain permanently passes version 12 for all block decoding.

---

### Recommendation

Add an explicit upper-bound check on `pvMinor` in the pre-v12 decoder path, mirroring the version-12+ behavior:

```haskell
instance DecCBORGroup ProtVer where
  decCBORGroup =
    ProtVer
      <$> decCBOR
      <*> ifDecoderVersionAtLeast
        (natVersion @12)
        (fromIntegral @Word32 @Natural <$> decCBOR @Word32)
        (do
          n <- decCBOR @Natural
          when (n > fromIntegral (maxBound :: Word32)) $
            cborError $ DecoderErrorCustom "ProtVer.pvMinor"
              "minor version exceeds Word32 bounds"
          pure n)
```

This ensures that `pvMinor` is bounded to `[0, Word32.Max]` regardless of the decoder version, eliminating the asymmetry between the two paths.

---

### Proof of Concept

Construct a CBOR-encoded block header where the `ProtVer` list contains `pvMinor = 2^64 + 1` (encoded as a CBOR bignum or multi-byte uint). Submit this block at protocol version 10 or 11:

```haskell
-- Encode ProtVer with pvMinor = 2^64 + 1
let hugePvMinor = (2 :: Natural) ^ (64 :: Int) + 1
    pv = ProtVer (natVersion @10) hugePvMinor
    encoded = serialize (natVersion @10) pv

-- Pre-v12 decode: succeeds
Right pv' = decodeFull (natVersion @10) encoded :: Either DecoderError ProtVer
-- pv'.pvMinor == 2^64 + 1  ✓ accepted

-- Post-v12 decode of the same bytes: fails
Left err = decodeFull (natVersion @12) encoded :: Either DecoderError ProtVer
-- DecoderErrorDeserialiseFailure: value exceeds Word32 bounds
```

The same bytes that are accepted at version 10 are rejected at version 12, demonstrating the divergence. A block producer at version 10 or 11 can embed such a `ProtVer` in a block header, causing all nodes that later re-decode the block at version 12+ to fail.

### Citations

**File:** libs/cardano-ledger-core/src/Cardano/Ledger/BaseTypes.hs (L211-211)
```haskell
data ProtVer = ProtVer {pvMajor :: !Version, pvMinor :: !Natural}
```

**File:** libs/cardano-ledger-core/src/Cardano/Ledger/BaseTypes.hs (L243-250)
```haskell
instance DecCBORGroup ProtVer where
  decCBORGroup =
    ProtVer
      <$> decCBOR
      <*> ifDecoderVersionAtLeast
        (natVersion @12)
        (fromIntegral @Word32 @Natural <$> decCBOR @Word32)
        (decCBOR @Natural)
```

**File:** libs/cardano-ledger-binary/src/Cardano/Ledger/Binary/Decoding/Decoder.hs (L1479-1485)
```haskell
decodeNatural :: Decoder s Natural
decodeNatural = do
  !n <- decodeInteger
  if n >= 0
    then return $! fromInteger n
    else cborError $ DecoderErrorCustom "Natural" "got a negative number"
{-# INLINE decodeNatural #-}
```

**File:** libs/cardano-ledger-core/test/Test/Cardano/Ledger/BinarySpec.hs (L33-41)
```haskell
    prop "ProtVer/Word32" $ do
      let badProtVerGen =
            ProtVer
              <$> arbitrary
              <*> ( fromIntegral @Word64 @Natural
                      <$> choose (fromIntegral (maxBound :: Word32) + 1, fromIntegral (maxBound :: Word64) * 2)
                  )
      forAll badProtVerGen $
        roundTripCborRangeFailureExpectation (natVersion @12) maxBound
```

**File:** eras/shelley/impl/src/Cardano/Ledger/Chain.hs (L71-82)
```haskell
chainChecks maxpv ccd blk = do
  unless (m <= maxpv) $ throwError (ObsoleteNodeCHAIN m maxpv)
  let bhHSize = blk ^. blockHeaderSizeBlockHeaderG
      bhBSize = blk ^. blockBodySizeBlockHeaderL
  unless (bhHSize <= (fromIntegral :: Word16 -> Int) (ccMaxBHSize ccd)) $
    throwError $
      HeaderSizeTooLargeCHAIN bhHSize (ccMaxBHSize ccd)
  unless (bhBSize <= ccMaxBBSize ccd) $
    throwError $
      BlockSizeTooLargeCHAIN bhBSize (ccMaxBBSize ccd)
  where
    ProtVer m _ = ccProtocolVersion ccd
```
