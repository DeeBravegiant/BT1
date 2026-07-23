Audit Report

## Title
Missing `baseFeedId != quoteFeedId` guard in synthetic-ratio mode locks pool to 1:1 price — (File: smart-contracts-poc/contracts/AnchoredPriceProvider.sol)

## Summary

`AnchoredPriceProvider` supports a two-feed synthetic-ratio mode where `mid = price(baseFeedId) / price(quoteFeedId)`. Neither the constructor nor `AnchoredProviderFactory.createAnchoredProvider` validates that `baseFeedId != quoteFeedId`. When both IDs are identical, the ratio collapses to exactly `1e8` (1.0 in 8-decimal terms) every block, causing every pool swap to execute at a permanently wrong 1:1 rate and enabling arbitrageurs to drain the pool of the more valuable token.

## Finding Description

`_getBidAndAskPrice` calls `_readLeg` independently on both `baseFeedId` and `quoteFeedId`, then computes:

```solidity
mid = Math.mulDiv(mid, ORACLE_DECIMALS, mid2);
spreadBps += spreadBps2;
``` [1](#0-0) 

When `baseFeedId == quoteFeedId`, both `_readLeg` calls hit the same oracle slot and return the same value `P`. The division becomes `Math.mulDiv(P, 1e8, P) = 1e8`, fixing the mid-price at exactly 1.0 regardless of actual market prices. The `spreadBps` is doubled, but unless the doubled value exceeds `MAX_SPREAD_BPS` (which is not guaranteed for small oracle spreads), `_computeBidAsk` proceeds and returns a valid bid/ask centered on 1.0.

The constructor only guards token addresses:

```solidity
require(_baseToken != address(0) && _quoteToken != address(0) && _baseToken != _quoteToken);
``` [2](#0-1) 

No analogous check exists for `_baseFeedId != _quoteFeedId` anywhere in the constructor. [3](#0-2) 

`createAnchoredProvider` validates only the `baseFeedId` class envelope and passes `quoteFeedId` through unchecked directly to the constructor: [4](#0-3) 

The resulting provider is registered in `_providers` and satisfies `isProvider() == true`, so it can be legitimately attached to a pool whose `token0`/`token1` are still distinct. [5](#0-4) 

## Impact Explanation

Every swap on a pool backed by such a provider executes at a fixed 1:1 mid-price (±doubled spread). For any pair where `token0 ≠ token1` in value, arbitrageurs can drain the pool of the more valuable token by paying the cheaper one at par. LPs suffer direct, unbounded loss of principal proportional to the price divergence from 1:1. This is a clear bad-price execution and pool insolvency impact.

## Likelihood Explanation

`createAnchoredProvider` is permissionless — any caller can invoke it. A curator who copy-pastes the same feed ID into both `baseFeedId` and `quoteFeedId` when setting up a synthetic pair (a plausible mistake) produces a permanently broken provider with no on-chain signal of the error. The factory's envelope validation passes because it only checks `baseFeedId`'s class; `quoteFeedId` is not class-validated at all. [6](#0-5) 

## Recommendation

Add the following guard in `AnchoredPriceProvider`'s constructor after storing the feed IDs, and mirror it in `createAnchoredProvider`:

```solidity
if (_quoteFeedId != bytes32(0)) {
    require(_baseFeedId != _quoteFeedId, "SameFeedId");
}
``` [7](#0-6) 

## Proof of Concept

```solidity
bytes32 FEED = keccak256("ETH/USD");

// Deploy with baseFeedId == quoteFeedId — no revert
AnchoredPriceProvider p = new AnchoredPriceProvider(
    factory, oracle, FEED, FEED,
    minMargin, maxStaleness, maxSpreadBps,
    false, 0, TOKEN0, TOKEN1
);

// Oracle reports ETH/USD = 2000 (8-decimal: 200_000_000_000)
oracle.setData(FEED, 200_000_000_000, 5, 0, block.timestamp);

// _getBidAndAskPrice: mid = mulDiv(200_000_000_000, 1e8, 200_000_000_000) = 1e8
// Pool prices TOKEN0 at 1:1 against TOKEN1 regardless of actual 2000:1 ratio
(uint128 bid, uint128 ask) = p.getBidAndAskPrice();
// bid ≈ ask ≈ Q64(1.0)

// Attacker swaps 1 TOKEN1 (worth $1) → receives ~1 TOKEN0 (worth $2000)
// Repeat until pool is drained of TOKEN0
``` [8](#0-7)

### Citations

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L136-172)
```text
        require(_factory != address(0));
        factory = _factory;

        offchainOracle = IOffchainOracle(_oracle);
        baseFeedId = _baseFeedId;
        quoteFeedId = _quoteFeedId;

        // Tokens live ONLY here (the oracles are token-free): the pair is an explicit,
        // mandatory input — including the synthetic (two-feed) mode, where the factory
        // knows the pair when it creates the pool.
        require(_baseToken != address(0) && _quoteToken != address(0) && _baseToken != _quoteToken);
        baseToken = _baseToken;
        quoteToken = _quoteToken;

        if (_maxRefStaleness > 7 days) revert MaxRefStalenessOutOfBounds(); // 0 allowed = same-block reference
        MAX_REF_STALENESS = _maxRefStaleness;

        if (_maxSpreadBps == 0 || _maxSpreadBps >= ORACLE_BPS) revert MaxSpreadOutOfBounds();
        MAX_SPREAD_BPS = _maxSpreadBps;

        // minMargin 0 is allowed: the band then relies purely on the oracle spreadBps. If spreadBps is
        // also 0 the band degenerates and the read halts via the refBid >= refAsk guard in _computeBidAsk
        // (never a tighter-than-band quote) — the clamp + that halt are the safety net, not a positive floor.
        // Worst-case half-width must stay below 100% so the clamped bid is always positive.
        if (uint256(_maxSpreadBps) * ONE_BPS_E18 + _minMargin >= BPS_BASE_U) revert BandTooWide();
        minMargin = _minMargin;

        MUTABLE_PARAMS = _mutableParams;
        // marginStep bias + derived step factors (immutable). The customizable variant shapes the quote
        // with confidence then this fixed bias; the load-bearing band clamp in _computeBidAsk keeps the
        // final quote no tighter than the band edge for ANY marginStep sign (a negative value tightens or
        // inverts the pre-clamp quote; the clamp neutralizes it). The immutable variant ignores them.
        if (_marginStep <= -BPS_BASE || _marginStep >= BPS_BASE) revert MarginStepOutOfBounds();
        marginStep = _marginStep;
        stepBidFactor = uint256(BPS_BASE - _marginStep);
        stepAskFactor = uint256(BPS_BASE + _marginStep);
    }
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L258-272)
```text
    function _getBidAndAskPrice() internal returns (uint128, uint128) {
        (uint256 mid, uint256 spreadBps, , bool ok) = _readLeg(baseFeedId);
        if (!ok) return (0, type(uint128).max);

        bytes32 _quote = quoteFeedId;
        if (_quote != bytes32(0)) {
            (uint256 mid2, uint256 spreadBps2, , bool ok2) = _readLeg(_quote);
            if (!ok2 || mid2 == 0) return (0, type(uint128).max);
            // Synthetic ratio (8-decimal): mid1 / mid2. Relative uncertainties of a ratio add.
            mid = Math.mulDiv(mid, ORACLE_DECIMALS, mid2);
            spreadBps += spreadBps2;
        }

        return _computeBidAsk(mid, spreadBps);
    }
```

**File:** smart-contracts-poc/contracts/AnchoredProviderFactory.sol (L156-194)
```text
    function createAnchoredProvider(
        address oracle,
        bytes32 baseFeedId,
        bytes32 quoteFeedId,
        uint256 minMargin,
        uint256 maxRefStaleness,
        uint16  maxSpreadBps,
        bool    mutableParams,
        int256  marginStep,
        address baseToken,
        address quoteToken
    ) external override returns (address provider) {
        if (!_oracles.contains(oracle)) revert OracleNotAllowed(oracle);

        // Feeds without an explicit class fall back to the admin-configured DEFAULT_CLASS envelope.
        bytes32 classId = feedClass[baseFeedId];
        if (classId == bytes32(0)) classId = DEFAULT_CLASS;

        Envelope storage env = envelopes[classId];
        if (!env.exists) revert EnvelopeNotFound(classId);
        if (
            minMargin < env.minMarginMin || minMargin > env.minMarginMax
            || maxRefStaleness < env.stalenessMin || maxRefStaleness > env.stalenessMax
            || maxSpreadBps < env.maxSpreadMin || maxSpreadBps > env.maxSpreadMax
        ) revert ParamsOutOfEnvelope();

        AnchoredPriceProvider p = new AnchoredPriceProvider(
            address(this),
            oracle,
            baseFeedId,
            quoteFeedId,
            minMargin,
            maxRefStaleness,
            maxSpreadBps,
            mutableParams,
            marginStep,
            baseToken,
            quoteToken
        );
```

**File:** smart-contracts-poc/contracts/AnchoredProviderFactory.sol (L196-201)
```text
        provider = address(p);
        address creator = msg.sender;

        _providers.add(provider);
        _providersByCreator[creator].add(provider);
        providerOwner[provider] = creator;
```
