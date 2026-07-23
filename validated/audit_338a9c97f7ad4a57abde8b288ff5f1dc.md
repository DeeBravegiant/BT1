Audit Report

## Title
Stale Updater Permissions Persist After Provider Ownership Transfer, Enabling Unauthorized `confidenceParam` Manipulation — (File: `smart-contracts-poc/contracts/PriceProviderFactory.sol`, `PriceProviderFactoryL2.sol`, `AnchoredProviderFactory.sol`)

## Summary

`transferProviderOwnership` in all three factory contracts updates `providerOwner` and `_providersByCreator` but never clears the `isUpdater[provider][updater]` mapping. Any address granted updater rights by a previous owner retains the ability to call `setConfidence` after ownership changes hands. For `PriceProvider`-backed providers (deployed via `PriceProviderFactory` and `PriceProviderFactoryL2`), a stale updater can set `confidenceParam` to `CONFIDENCE_MAX`, widening the bid/ask spread by up to 1,000,000× and causing bad-price execution or swap DoS for every pool using that provider.

## Finding Description

`transferProviderOwnership` in `PriceProviderFactory` (lines 92–102) updates only `providerOwner` and `_providersByCreator`; `isUpdater[provider][*]` is never touched:

```solidity
providerOwner[provider] = newOwner;
_providersByCreator[previousOwner].remove(provider);
_providersByCreator[newOwner].add(provider);
// isUpdater[provider][*] is NEVER cleared
emit ProviderOwnershipTransferred(provider, previousOwner, newOwner);
```

The identical omission exists in `PriceProviderFactoryL2` (lines 95–105) and `AnchoredProviderFactory` (lines 230–240).

`_requireUpdater` (line 34–37 in each factory) passes for any address where `isUpdater[provider][msg.sender] == true`, regardless of who the current owner is:

```solidity
function _requireUpdater(address provider) internal view {
    if (msg.sender != providerOwner[provider] && !isUpdater[provider][msg.sender])
        revert NotProviderUpdater();
}
```

`setConfidence` calls `_requireUpdater` and then directly calls `PriceProvider(providers[i]).setConfidenceParam(values[i])` (lines 130–142). In `PriceProvider.setConfidenceParam`, the only guards are an upper-bound check (`newValue > CONFIDENCE_MAX`) and a 1-minute cooldown — no ownership check.

In `PriceProvider._getBidAndAskPrice` (lines 215–217), `confidenceParam` is a direct multiplier on the oracle spread with no clamp:

```solidity
uint256 adjustedSpread = spread * confidenceParam;
(uint256 bid, uint256 ask) = _getBidAskFrom(mid, adjustedSpread);
```

`_getBidAskFrom` (lines 137–141) computes `delta = midPrice * confidence / CONFIDENCE_BASE` and returns `bid = midPrice - delta`, `ask = midPrice + delta`. With `confidenceParam = CONFIDENCE_MAX = 1_000_000` and a typical oracle `spread = 1000` (10 bps): `adjustedSpread = 1e9`, `delta = midPrice × 0.1`, yielding a ±10% spread instead of ±0.001%. At `spread = 9999`, `delta ≈ midPrice`, making `bid ≈ 0`, which triggers `FeedStalled()` in `getBidAndAskPrice` (line 119), rendering the swap path unusable.

The new owner cannot enumerate stale updaters because `isUpdater` is a plain nested mapping with no associated enumerable set. `revokeUpdater` (lines 86–90) requires knowing the specific address to revoke, which the new owner cannot discover on-chain without off-chain event indexing.

**Note on `AnchoredProviderFactory`:** `AnchoredPriceProvider` applies a reference band clamp in `_computeBidAsk` (`bidOut = Math.min(refBid, cBid)`, `askOut = Math.max(refAsk, cAsk)`). At `confidenceParam = CONFIDENCE_MAX`, the shaped quote approximately equals the reference band (not wider), so the clamp limits the impact to the reference band width. Additionally, `setConfidenceParam` reverts with `ImmutableProvider()` for providers deployed with `mutableParams = false`. The primary bad-price execution impact therefore applies to `PriceProviderFactory` and `PriceProviderFactoryL2`.

## Impact Explanation

A stale updater can call `setConfidence` at any time (subject only to a 1-minute cooldown) to set `confidenceParam` to `CONFIDENCE_MAX` on any `PriceProvider`-backed provider they were previously granted rights over. This causes:

1. **Bad-price execution**: every swap against a pool using that provider executes at a price up to ±10% from the oracle mid at typical spreads, directly draining trader principal.
2. **Swap DoS**: at extreme oracle spread values, `bid ≈ 0` triggers `FeedStalled()`, making the pool's swap path completely unusable until the new owner (who may not know the stale updater exists) intervenes.

Both outcomes fall within the allowed impact gate ("bad-price execution" and "broken core pool functionality causing unusable swap flows").

## Likelihood Explanation

- Provider ownership transfers are an expected, documented operation (`transferProviderOwnership` is a public interface function).
- The previous owner may have granted updater rights to automated bots, partners, or employees — none of whom are known to the new owner.
- The new owner has no on-chain mechanism to enumerate stale updaters; they can only revoke addresses they already know about.
- The stale updater needs no special privilege beyond the stale `isUpdater` flag, which persists indefinitely.
- The 1-minute cooldown does not prevent the attack; it only limits the frequency of re-adjustment.

## Recommendation

Maintain an enumerable set of updaters per provider alongside the `isUpdater` mapping, and clear all entries on ownership transfer:

```solidity
// Add to storage:
mapping(address provider => EnumerableSet.AddressSet) private _updaters;

// In grantUpdater:
_updaters[provider].add(updater);
isUpdater[provider][updater] = true;

// In revokeUpdater:
_updaters[provider].remove(updater);
isUpdater[provider][updater] = false;

// In transferProviderOwnership — add before emitting:
EnumerableSet.AddressSet storage updaterSet = _updaters[provider];
uint256 len = updaterSet.length();
for (uint256 i = len; i > 0; ) {
    unchecked { --i; }
    address u = updaterSet.at(i);
    updaterSet.remove(u);
    isUpdater[provider][u] = false;
}
```

Apply the same fix to `PriceProviderFactoryL2` and `AnchoredProviderFactory`.

## Proof of Concept

1. Owner A deploys a provider via `PriceProviderFactory.createPriceProvider` and calls `grantUpdater(provider, B)`.
   - `isUpdater[provider][B] = true`.
2. Owner A calls `transferProviderOwnership(provider, D)`.
   - `providerOwner[provider] = D`. `isUpdater[provider][B]` is **not** cleared.
3. D is now the owner. D does not know about B.
4. B calls `setConfidence([provider], [1_000_000])`.
   - `_requireUpdater` passes because `isUpdater[provider][B] == true`.
   - `PriceProvider(provider).setConfidenceParam(1_000_000)` executes.
5. Any pool using this provider calls `getBidAndAskPrice()`. With a 10 bps oracle spread, the returned bid/ask is now ±10% of mid. Every swap executes at a price 10% worse than the oracle mid.
6. D cannot revoke B because D does not know B's address. Even if D discovers B and calls `revokeUpdater(provider, B)`, any other stale updaters remain active.
7. After 1 minute, B can call `setConfidence` again to restore the manipulated value if D managed to reset it.