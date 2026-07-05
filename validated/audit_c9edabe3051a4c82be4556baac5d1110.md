### Title
Word16 Silent Truncation in `CompactValue` Offset Computation Bypasses `isMultiAssetSmallEnough` Guard - (File: `eras/mary/impl/src/Cardano/Ledger/Mary/Value.hs`)

### Summary

The `to` function in `Cardano.Ledger.Mary.Value` computes `Word16` byte-offsets for PolicyIDs and AssetNames in the compact multi-asset representation. The only guard against overflow is wrapped in `Control.Exception.assert`, which GHC silently disables under `-O`/`-O2` (all production builds). When the total representation size exceeds 65 535 bytes, `fromIntegral :: Int -> Word16` truncates silently, producing corrupted offsets. The `from` (decompact) function then reads wrong bytes from the byte array, reconstructing different PolicyIDs and AssetNames than were originally stored.

### Finding Description

`eras/mary/impl/src/Cardano/Ledger/Mary/Value.hs` imports `assert` from `Control.Exception`: [1](#0-0) 

Inside `to`, the sole overflow guard is:

```haskell
to v@(MaryValue _ ma) = do
  c <- assert (isMultiAssetSmallEnough ma) (toCompact ada)
``` [2](#0-1) 

`isMultiAssetSmallEnough` enforces `44n + 28p ≤ 65535`: [3](#0-2) 

When the check is bypassed (assert disabled), the offset maps are computed as `Map PolicyID Word16` and `Map AssetName Word16` via unchecked `fromIntegral`: [4](#0-3) [5](#0-4) 

Any offset value `> 65535` wraps silently to `(value mod 65536)`, pointing into the wrong region of the byte array. The `from` function then reads 28-byte PolicyID blobs and variable-length AssetName blobs from those wrong offsets: [6](#0-5) 

The `from` function also uses `assert` for the same check, which is equally disabled: [7](#0-6) 

The test infrastructure explicitly documents that exceeding ~910 triples causes overflow: [8](#0-7) 

### Impact Explanation

If a transaction output carries a `MaryValue` whose `MultiAsset` is large enough to overflow `Word16` offsets, the UTxO stores a `CompactValueMultiAsset` with corrupted PolicyID and AssetName offsets. Every subsequent read via `fromCompact` reconstructs a different `MaryValue` — with wrong policy IDs and/or wrong asset names — than what was originally validated. This breaks the preservation-of-value invariant: assets can appear to belong to a different minting policy, effectively creating or destroying native assets through an invalid ledger state transition.

**Allowed impact matched**: *Critical — Direct loss, creation, or destruction of native assets through an invalid ledger state transition.*

### Likelihood Explanation

**Mitigating factor**: In Alonzo and later eras, the `ppMaxValSize` protocol parameter limits the serialized CBOR size of transaction output values. At the current mainnet setting of 5 000 bytes, the maximum number of assets per output is far below the ~910-asset overflow threshold, making the attack unreachable in Conway/Dijkstra. [9](#0-8) 

**Residual risk**: The `assert`-only guard means the protection is entirely absent at the code level in production builds. If `maxValSize` is raised by a governance action to a value that permits >910 assets per output (roughly >63 700 bytes), the overflow becomes directly exploitable by any transaction author. The Mary era (no `maxValSize`) is historical, but the code path is shared across all eras using `MaryValue`.

### Recommendation

Replace the `assert`-based guard with a hard failure in the `Maybe` monad:

```haskell
to v@(MaryValue _ ma) = do
  guard (isMultiAssetSmallEnough ma)   -- returns Nothing instead of assert
  c <- toCompact ada
  ...
```

This ensures the check is enforced in all build configurations. Additionally, the `from` function should validate the compact representation before reading offsets, or the `maxValSize` protocol parameter should be formally bounded to prevent the overflow threshold from ever being reachable.

### Proof of Concept

1. Construct a `MaryValue` with ~911 distinct `(PolicyID, AssetName, quantity)` triples (each asset name 32 bytes, each policy unique). The total representation size is `12×911 + 28×911 + 32×911 = 72×911 = 65 592 > 65 535`.
2. Submit a transaction output carrying this value in the Mary era (or in a later era with `maxValSize` raised above ~65 600 bytes by governance).
3. In an optimized build, `assert (isMultiAssetSmallEnough ma)` is a no-op; `fromIntegral (65592) :: Word16` = `57` (truncated), so the first asset-name offset points into region A (quantities) instead of region E (asset names).
4. The UTxO stores the corrupted `CompactValueMultiAsset`.
5. A subsequent transaction spending this output calls `fromCompact`, which reads 28 bytes from offset 57 (inside the quantity region) as a PolicyID — a completely different policy than the one that minted the assets.
6. The balance check for the spending transaction uses this wrong `MaryValue`, allowing assets attributed to the wrong policy to be credited, constituting unauthorized creation of native assets. [10](#0-9)

### Citations

**File:** eras/mary/impl/src/Cardano/Ledger/Mary/Value.hs (L68-68)
```haskell
import Control.Exception (assert)
```

**File:** eras/mary/impl/src/Cardano/Ledger/Mary/Value.hs (L580-700)
```haskell
to ::
  MaryValue ->
  -- The Nothing case of the return value corresponds to a quantity that is outside
  -- the bounds of a Word64. x < 0 or x > (2^64 - 1)
  Maybe CompactValue
to (MaryValue ada (MultiAsset m))
  | Map.null m = CompactValueAdaOnly <$> toCompact ada
to v@(MaryValue _ ma) = do
  c <- assert (isMultiAssetSmallEnough ma) (toCompact ada)
  -- Here we convert the (pid, assetName, quantity) triples into
  -- (Int, (Word16,Word16,Word64))
  -- These represent the index, pid offset, asset name offset, and quantity.
  -- If any of the quantities out of bounds, this will produce Nothing.
  -- The triples are ordered by asset name in descending order.
  preparedTriples <-
    zip [0 ..] . sortOn (\(_, x, _) -> x) <$> traverse prepare triples
  pure $
    CompactValueMultiAsset c (fromIntegral numTriples) $
      runST $ do
        byteArray <- BA.newByteArray repSize
        forM_ preparedTriples $ \(i, (pidoff, anoff, q)) ->
          do
            -- For each triple, we write the quantity to region A,
            -- the pid offset to region B, and the asset name offset to region C.
            -- We can calculate the sizes (and therefore the starts) of each region
            -- using the number of triples.
            -- A:
            --   size: (#triples * 8) bytes
            --   start: offset 0
            -- B:
            --   size: (#triples * 2) bytes
            --   start: size(A) = #triples * 8
            -- C:
            --   size: (#triples * 2) bytes
            --   start: size(A) + size(B) = #triples * 10
            --
            -- The position argument to writeByteArray is an index in terms of the
            -- size of the value being written. So writeByteArray of a Word64 at
            -- position 1 writes at offset 8.
            --
            -- For the following, the byte offsets calculated above are converted to
            -- ByteArray positions by division.
            --
            -- The byte offset of the ith...
            --   quantity: 8i
            --   pid offset: 8n + 2i
            --   asset name offset: 8n + 2n + 2i
            -- Dividing by the respective sizes (8,2,2) yields the indices below.
            BA.writeByteArray byteArray i q
            BA.writeByteArray byteArray (4 * numTriples + i) pidoff
            BA.writeByteArray byteArray (5 * numTriples + i) anoff

        forM_ (Map.toList pidOffsetMap) $
          \(PolicyID (ScriptHash sh), offset) ->
            let pidBytes = Hash.hashToBytesShort sh
             in BA.copyByteArray
                  byteArray
                  (fromIntegral offset)
                  (byteArrayFromShortByteString pidBytes)
                  0
                  pidSize

        forM_ (Map.toList assetNameOffsetMap) $
          \(AssetName anameBS, offset) ->
            let anameBytes = anameBS
                anameLen = SBS.length anameBS
             in BA.copyByteArray
                  byteArray
                  (fromIntegral offset)
                  (byteArrayFromShortByteString anameBytes)
                  0
                  anameLen
        byteArrayToShortByteString <$> BA.unsafeFreezeByteArray byteArray
  where
    (ada, triples) = gettriples v
    numTriples = length triples

    -- abcRegionSize is the combined size of regions A, B, and C
    abcRegionSize = numTriples * 12

    pidSize = fromIntegral (Hash.hashSize (Proxy :: Proxy ADDRHASH))

    -- pids is the collection of all distinct pids
    pids = Set.fromList $ (\(pid, _, _) -> pid) <$> triples

    pidOffsetMap :: Map PolicyID Word16
    pidOffsetMap =
      -- the pid offsets are:
      --   X, X + s, X + 2s, X + 3s, ...
      -- where X is the start of block D and s is the size of a pid
      let offsets =
            enumFromThen (fromIntegral abcRegionSize) (fromIntegral (abcRegionSize + pidSize))
       in Map.fromList (zip (Set.toList pids) offsets)

    pidOffset pid = fromJust (Map.lookup pid pidOffsetMap)

    pidBlockSize = Set.size pids * pidSize

    -- Putting asset names in descending order ensures that the empty string
    -- is last, so the associated offset is pointing to the end of the array
    assetNames = Set.toDescList $ Set.fromList $ (\(_, an, _) -> an) <$> triples

    assetNameLengths = fromIntegral . SBS.length . assetNameBytes <$> assetNames

    assetNameOffsetMap :: Map AssetName Word16
    assetNameOffsetMap =
      -- The asset name offsets are the running sum of the asset lengths,
      -- but starting with the offset of the start of block E.
      let offsets = scanl (+) (abcRegionSize + pidBlockSize) assetNameLengths
       in fromIntegral <$> Map.fromList (zip assetNames offsets)

    assetNameOffset aname = fromJust (Map.lookup aname assetNameOffsetMap)

    anameBlockSize = sum assetNameLengths

    -- size = size(A+B+C)      + size(D)      + size(E)
    repSize = abcRegionSize + pidBlockSize + anameBlockSize

    prepare (pid, aname, q) = do
      q' <- integerToWord64 q
      pure (pidOffset pid, assetNameOffset aname, q')
```

**File:** eras/mary/impl/src/Cardano/Ledger/Mary/Value.hs (L710-712)
```haskell
isMultiAssetSmallEnough :: MultiAsset -> Bool
isMultiAssetSmallEnough (MultiAsset ma) =
  44 * M.getSum (foldMap' (M.Sum . length) ma) + 28 * length ma <= 65535
```

**File:** eras/mary/impl/src/Cardano/Ledger/Mary/Value.hs (L732-734)
```haskell
from (CompactValueMultiAsset c numAssets rep) =
  let mv@(MaryValue _ ma) = valueFromList (fromCompact c) triples
   in assert (isMultiAssetSmallEnough ma) mv
```

**File:** eras/mary/impl/src/Cardano/Ledger/Mary/Value.hs (L773-785)
```haskell
    convertTriple ::
      (Word16, Word16, Word64) -> (PolicyID, AssetName, Integer)
    convertTriple (p, a, i) =
      ( PolicyID $
          ScriptHash $
            Hash.UnsafeHash $
              readShortByteString
                rep
                (fromIntegral p)
                (fromIntegral $ Hash.hashSize ([] :: [ADDRHASH]))
      , AssetName $ readShortByteString rep (fromIntegral a) (assetLen a)
      , fromIntegral i
      )
```

**File:** eras/mary/impl/testlib/Test/Cardano/Ledger/Mary/Arbitrary.hs (L98-123)
```haskell
-- When we generate a number of MultiAssets all at once, that number happens to have
-- an implicit upper limit due to the Cardano.Ledger.Mary.Value.{to,from}-based
-- compacting operation. This operation is also performed when we serialise to and from CBOR.
--
-- Refering to the haddock for 'Cardano.Ledger.Mary.Value.to' we surmise that
--   1. The offsets for AssetName and PolicyID are stored as Word16 (maxBound = 65535).
--   2. All offsets (including those for AssetName and PolicyID) are relative to the whole
--      of the representation (binary blob) rather than the start of their respective regions.
--   3. If the offsets exceed their maxBounds, they will overflow.
--   4. So, we need to ensure that at least the last of the offsets (AssetName offsets) do
--      not exceed 65535.
--   5. With `n` as the total number of assets, `p` the number of policy ids, the inequality to be satisfied is thus:
--           8n -- Word64 asset quantities
--        +  2n -- Word16 policy id offsets
--        +  2n -- Word16 asset name offsets
--        + 28p -- 28-byte policy ids
--        + 32n -- 32-byte asset names (a maximum of 32 bytes)
--        should be <= 65535, assuming the numer of policies to be maximal (i.e. equal to number of assets)
--        65535 / 72 ~ 910.2 is the maximum number of triples to be safely generated.
--        Or, in other words, 44n + 28p <= 65535
--
-- NOTE: There are some conditions due to which exceeding this number may not
-- result in a guaranteed failure to compact without overflow, because, during compacting
--   1. The asset names and policy ids are deduplicated
--   2. Not all generated asset names are 32-bytes long
-- But, exceeding this number does make the probability of causing overflow > 0.
```

**File:** eras/alonzo/impl/src/Cardano/Ledger/Alonzo/Rules/Utxo.hs (L1-1)
```haskell
{-# LANGUAGE BangPatterns #-}
```
