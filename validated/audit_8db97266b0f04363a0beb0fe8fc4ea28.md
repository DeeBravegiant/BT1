Audit Report

## Title
Stale Updater Permissions Persist After `transferProviderOwnership`, Enabling Unauthorized `confidenceParam` Manipulation — (`smart-contracts-poc/contracts/PriceProviderFactory.sol`, `smart-contracts-poc/contracts/PriceProviderFactoryL2.sol`, `smart-contracts-poc/contracts/AnchoredProviderFactory.sol`)

## Summary
All three factory contracts maintain an `isUpdater[provider][updater]` mapping that is never cleared when `transferProviderOwnership` is called. Any address previously granted updater rights by the old owner retains the ability to call `setConfidence()` on the transferred provider indefinitely, without the new owner's knowledge or consent. This allows a stale updater to manipulate `confidenceParam` — either widening the bid/ask spread delivered to pool swaps or collapsing it to zero to halt the oracle entirely.

## Finding Description
In `PriceProviderFactory` (L20, L92–102), `PriceProviderFactoryL2` (L20, L95–105), and `AnchoredProviderFactory` (L46, L230–240), `transferProviderOwnership` updates only `providerOwner` and the `_providersByCreator` enumerable sets:

```solidity
providerOwner[provider] = newOwner;
_providersByCreator[previousOwner].remove(provider);
_providersByCreator[newOwner].add(provider);
// isUpdater[provider][*] is never touched
```

`_requireUpdater` (PriceProviderFactory L34–37, AnchoredProviderFactory L61–64) passes for any address still set in `isUpdater`, regardless of whether the granting owner still holds `providerOwner`:

```solidity
if (msg.sender != providerOwner[provider] && !isUpdater[provider][msg.sender])
    revert NotProviderUpdater();
```

`setConfidence` (PriceProviderFactory L130–142, AnchoredProviderFactory L262–274) calls `_requireUpdater` then forwards to `PriceProvider.setConfidenceParam` / `AnchoredPriceProvider.setConfidenceParam`. The new owner has no on-chain mechanism to enumerate which updater addresses exist (no enumerable set for updaters), so they cannot bulk-revoke stale permissions.

**Attack path:**
1. Owner A creates a provider and calls `grantUpdater(provider, staleBotAddress)` — a normal operational pattern (the factory ships a `SetConfidence` deployment script that assumes a dedicated updater key).
2. Owner A calls `transferProviderOwnership(provider, ownerB)`. `isUpdater[provider][staleBotAddress]` remains `true`.
3. `staleBotAddress` waits for `CONFIDENCE_COOLDOWN` (1 minute) to elapse, then calls `factory.setConfidence([provider], [0])`.
4. In `PriceProvider._getBidAndAskPrice`, `adjustedSpread = spread * 0 = 0`, so `delta = 0`, `bid = ask = midPrice`. The `bidOut >= askOut` guard at L228 fires, returning `(0, type(uint128).max)` → `getBidAndAskPrice` reverts with `FeedStalled`. Every pool swap using this provider is blocked.
5. Alternatively, `staleBotAddress` sets `confidenceParam = CONFIDENCE_MAX (1_000_000)`, maximizing the spread: `delta = midPrice * spread * 1_000_000 / 1e10`. For a 50 bps oracle spread this yields a 0.5% bid/ask band, giving traders materially worse execution prices.

The existing test `testOldOwnerCannotUpdateAfterTransfer` only verifies that the previous *owner* loses direct update rights; it does not test whether previously-granted updaters retain access after transfer.

## Impact Explanation
Two concrete impacts reach the allowed gate:

**Oracle halt (broken core pool functionality):** Setting `confidenceParam = 0` collapses `adjustedSpread` to zero, making `bid == ask`, which triggers the `bidOut >= askOut` sentinel in `PriceProvider._getBidAndAskPrice` (L228) and causes `getBidAndAskPrice` to revert with `FeedStalled`. Every swap against any pool using this provider fails until the new owner discovers and revokes the stale updater address — which requires knowing it.

**Bad-price execution:** Setting `confidenceParam = CONFIDENCE_MAX` maximizes `delta = midPrice * spread * 1_000_000 / 1e10`. For a 50 bps oracle spread this produces a 0.5% bid/ask band. Sellers receive a bid 0.5% below mid; buyers pay an ask 0.5% above mid. In `AnchoredPriceProvider`, the `_computeBidAsk` clamp (`Math.min(refBid, cBid)` / `Math.max(refAsk, cAsk)`) widens the quote beyond the reference band rather than constraining it, so the inflated spread passes through to pool swaps.

## Likelihood Explanation
The precondition — that the previous owner granted at least one updater before transferring — is a normal operational pattern explicitly anticipated by the factory's deployment scripts. The stale updater need not be malicious at grant time; a legitimately-granted key that is later compromised, or one deliberately retained by a malicious previous owner, both satisfy the precondition. The new owner has no on-chain visibility into which updater addresses exist and no way to enumerate or bulk-revoke them. The `CONFIDENCE_COOLDOWN` of 1 minute limits update frequency but does not prevent the attack from being sustained indefinitely.

## Recommendation
Scope `isUpdater` to the current owner epoch so permissions expire automatically on transfer:

```solidity
mapping(address provider => mapping(address owner => mapping(address updater => bool))) public isUpdater;
```

All `grantUpdater`, `revokeUpdater`, and `_requireUpdater` calls use `providerOwner[provider]` as the middle key, so permissions granted under a previous owner are automatically invalidated when `providerOwner[provider]` changes. Alternatively, maintain an enumerable set of updaters per provider and clear it in `transferProviderOwnership`.

## Proof of Concept
```
1. Deploy PriceProviderFactory; Owner A calls createPriceProvider() → provider P.
2. Owner A calls grantUpdater(P, staleUpdater).
   → isUpdater[P][staleUpdater] = true
3. Owner A calls transferProviderOwnership(P, ownerB).
   → providerOwner[P] = ownerB
   → isUpdater[P][staleUpdater] still = true  ← BUG
4. ownerB is unaware of staleUpdater.
5. After CONFIDENCE_COOLDOWN (1 min) elapses:
   staleUpdater calls factory.setConfidence([P], [0])
   → _requireUpdater passes (isUpdater[P][staleUpdater] == true)
   → PriceProvider(P).setConfidenceParam(0) succeeds
   → confidenceParam = 0
6. Any pool swap calling P.getBidAndAskPrice():
   adjustedSpread = spread * 0 = 0 → delta = 0 → bid == ask
   → bidOut >= askOut guard fires → returns (0, type(uint128).max)
   → getBidAndAskPrice reverts FeedStalled → swap reverts
7. Repeat every minute to keep the oracle halted.
   OR: set confidenceParam = 1_000_000 to maximize spread and
   deliver bad-price execution to all swaps against the pool.
```

Foundry test skeleton:
```solidity
function testStaleUpdaterRetainsAccessAfterTransfer() public {
    address provider = factory.createPriceProvider(...);
    factory.grantUpdater(provider, staleUpdater);
    factory.transferProviderOwnership(provider, ownerB);
    vm.warp(block.timestamp + 61);
    vm.prank(staleUpdater);
    address[] memory ps = new address[](1); ps[0] = provider;
    uint256[] memory vs = new uint256[](1); vs[0] = 0;
    factory.setConfidence(ps, vs); // must succeed — demonstrates the bug
}
```