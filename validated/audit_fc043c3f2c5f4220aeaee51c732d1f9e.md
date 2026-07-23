Looking at the three factory contracts in `smart-contracts-poc/contracts/`, I can identify a direct structural analog to the OwnableSmartWallet bug.

---

### Title
Stale Updater Permissions Persist After Provider Ownership Transfer, Enabling Unauthorized `confidenceParam` Manipulation — (File: `smart-contracts-poc/contracts/PriceProviderFactory.sol`, `PriceProviderFactoryL2.sol`, `AnchoredProviderFactory.sol`)

---

### Summary

`transferProviderOwnership` in all three factory contracts updates `providerOwner` and `_providersByCreator` but never touches the `isUpdater[provider][updater]` mapping. Updaters granted by the previous owner retain the ability to call `setConfidence` after ownership changes hands. Because `isUpdater` is a nested mapping with no enumeration, the new owner cannot discover or revoke stale grants. A stale updater can set `confidenceParam` to `CONFIDENCE_MAX`, widening the bid/ask spread fed to `MetricOmmPool` and causing bad-price execution for every swap against that provider.

---

### Finding Description

In `PriceProviderFactory.transferProviderOwnership`:

```solidity
function transferProviderOwnership(address provider, address newOwner)
    external override onlyProviderOwner(provider)
{
    require(_providers.contains(provider), ProviderNotTracked());
    require(newOwner != address(0));
    address previousOwner = providerOwner[provider];

    providerOwner[provider] = newOwner;                          // ← only this changes
    _providersByCreator[previousOwner].remove(provider);
    _providersByCreator[newOwner].add(provider);
    // isUpdater[provider][*] is NEVER cleared
    emit ProviderOwnershipTransferred(provider, previousOwner, newOwner);
}
``` [1](#0-0) 

The identical omission exists in `PriceProviderFactoryL2`: [2](#0-1) 

And in `AnchoredProviderFactory`: [3](#0-2) 

The updater check in `_requireUpdater` passes for any address where `isUpdater[provider][msg.sender] == true`, regardless of who the current owner is: [4](#0-3) 

`setConfidence` calls `_requireUpdater` and then directly sets `confidenceParam` on the provider: [5](#0-4) 

`confidenceParam` is the multiplier applied to the oracle's raw spread before computing bid/ask in `PriceProvider._getBidAndAskPrice`:

```solidity
uint256 adjustedSpread = spread * confidenceParam;
(uint256 bid, uint256 ask) = _getBidAskFrom(mid, adjustedSpread);
``` [6](#0-5) 

`_getBidAskFrom` computes:

```solidity
uint256 delta = midPrice * confidence / CONFIDENCE_BASE;  // CONFIDENCE_BASE = 1e10
bid = delta >= midPrice ? 0 : midPrice - delta;
ask = midPrice + delta;
``` [7](#0-6) 

With `confidenceParam = CONFIDENCE_MAX = 1_000_000` and a typical oracle spread of `1000` (10 bps):

- `adjustedSpread = 1000 × 1_000_000 = 1e9`
- `delta = midPrice × 1e9 / 1e10 = midPrice × 0.1`
- `bid = midPrice × 0.9`, `ask = midPrice × 1.1`

The pool now quotes a ±10% spread instead of the intended ±0.001% spread. Every swap against this provider executes at a price 10% worse than the oracle mid, directly draining trader principal.

At extreme values (e.g., `spread = 9999`, `confidenceParam = 1_000_000`), `delta ≈ midPrice`, making `bid ≈ 0` and `ask ≈ 2 × midPrice`. The `getBidAndAskPrice` guard then fires `FeedStalled()`, rendering the pool's swap path completely unusable. [8](#0-7) 

The new owner cannot enumerate stale updaters because `isUpdater` is a plain nested mapping with no associated set: [9](#0-8) 

`revokeUpdater` requires knowing the specific address to revoke, which the new owner cannot discover on-chain: [10](#0-9) 

---

### Impact Explanation

A stale updater — an address that was granted updater rights by a previous owner but never revoked — can call `setConfidence` at any time (subject only to a 1-minute cooldown) to set `confidenceParam` to `CONFIDENCE_MAX`. This widens the bid/ask spread delivered to `MetricOmmPool` by up to 1,000,000×, causing:

1. **Bad-price execution**: every swap against the pool executes at a price far from the oracle mid, with traders losing up to ~10% of principal per swap at typical oracle spreads.
2. **Swap DoS**: at extreme spread values, `bid ≈ 0` triggers `FeedStalled()`, making the pool's swap path completely unusable until the owner (who may not know the stale updater exists) intervenes.

Both outcomes are within the allowed impact gate ("bad-price execution" and "broken core pool functionality causing unusable swap flows").

---

### Likelihood Explanation

- Provider ownership transfers are an expected, documented operation (`transferProviderOwnership` is a public interface function).
- The previous owner may have granted updater rights to automated bots, partners, or employees — none of whom are known to the new owner.
- The new owner has no on-chain mechanism to enumerate stale updaters; they can only revoke addresses they already know about.
- The stale updater needs no special privilege beyond the stale `isUpdater` flag, which persists indefinitely.
- The 1-minute cooldown does not prevent the attack; it only limits the frequency of re-adjustment.

---

### Recommendation

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

---

### Proof of Concept

1. Owner A deploys a provider and calls `grantUpdater(provider, B)` and `grantUpdater(provider, C)`.
   - `isUpdater[provider][B] = true`, `isUpdater[provider][C] = true`.
2. Owner A calls `transferProviderOwnership(provider, D)`.
   - `providerOwner[provider] = D`. `isUpdater` entries are **not** cleared.
3. D is now the owner. D does not know about B or C.
4. B calls `setConfidence([provider], [1_000_000])`.
   - `_requireUpdater` passes because `isUpdater[provider][B] = true`.
   - `PriceProvider(provider).setConfidenceParam(1_000_000)` executes.
5. `MetricOmmPool` calls `getBidAndAskPrice()` on the provider. With a 10 bps oracle spread, the returned bid/ask is now ±10% of mid. Every swap executes at a price 10% worse than the oracle mid.
6. D cannot revoke B or C because they do not know these addresses exist. Even if D calls `revokeUpdater(provider, B)`, C remains active.
7. After 1 minute, B can call `setConfidence` again to restore the manipulated value if D managed to reset it.

### Citations

**File:** smart-contracts-poc/contracts/PriceProviderFactory.sol (L20-20)
```text
    mapping(address provider => mapping(address updater => bool)) public isUpdater;
```

**File:** smart-contracts-poc/contracts/PriceProviderFactory.sol (L34-37)
```text
    function _requireUpdater(address provider) internal view {
        if (msg.sender != providerOwner[provider] && !isUpdater[provider][msg.sender])
            revert NotProviderUpdater();
    }
```

**File:** smart-contracts-poc/contracts/PriceProviderFactory.sol (L86-90)
```text
    function revokeUpdater(address provider, address updater) external override onlyProviderOwner(provider) {
        require(_providers.contains(provider), ProviderNotTracked());
        isUpdater[provider][updater] = false;
        emit UpdaterRevoked(provider, updater);
    }
```

**File:** smart-contracts-poc/contracts/PriceProviderFactory.sol (L92-102)
```text
    function transferProviderOwnership(address provider, address newOwner) external override onlyProviderOwner(provider) {
        require(_providers.contains(provider), ProviderNotTracked());
        require(newOwner != address(0));
        address previousOwner = providerOwner[provider];

        providerOwner[provider] = newOwner;
        _providersByCreator[previousOwner].remove(provider);
        _providersByCreator[newOwner].add(provider);

        emit ProviderOwnershipTransferred(provider, previousOwner, newOwner);
    }
```

**File:** smart-contracts-poc/contracts/PriceProviderFactory.sol (L130-142)
```text
    function setConfidence(
        address[] calldata providers,
        uint256[] calldata values
    ) external override {
        uint256 l = providers.length;
        if (l != values.length) revert LengthMismatch();

        for (uint256 i; i < l; ++i) {
            require(_providers.contains(providers[i]), ProviderNotTracked());
            _requireUpdater(providers[i]);
            PriceProvider(providers[i]).setConfidenceParam(values[i]);
        }
    }
```

**File:** smart-contracts-poc/contracts/PriceProviderFactoryL2.sol (L95-105)
```text
    function transferProviderOwnership(address provider, address newOwner) external override onlyProviderOwner(provider) {
        require(_providers.contains(provider), ProviderNotTracked());
        require(newOwner != address(0));
        address previousOwner = providerOwner[provider];

        providerOwner[provider] = newOwner;
        _providersByCreator[previousOwner].remove(provider);
        _providersByCreator[newOwner].add(provider);

        emit ProviderOwnershipTransferred(provider, previousOwner, newOwner);
    }
```

**File:** smart-contracts-poc/contracts/AnchoredProviderFactory.sol (L230-240)
```text
    function transferProviderOwnership(address provider, address newOwner) external override onlyProviderOwner(provider) {
        require(_providers.contains(provider), ProviderNotTracked());
        require(newOwner != address(0));
        address previousOwner = providerOwner[provider];

        providerOwner[provider] = newOwner;
        _providersByCreator[previousOwner].remove(provider);
        _providersByCreator[newOwner].add(provider);

        emit ProviderOwnershipTransferred(provider, previousOwner, newOwner);
    }
```

**File:** smart-contracts-poc/contracts/PriceProvider.sol (L115-120)
```text
    function getBidAndAskPrice()
        external override returns (uint128 bid, uint128 ask)
    {
        (bid, ask) = _getBidAndAskPrice();
        if (bid == 0 || ask == type(uint128).max) revert FeedStalled();
    }
```

**File:** smart-contracts-poc/contracts/PriceProvider.sol (L137-141)
```text
    function _getBidAskFrom(uint256 midPrice, uint256 confidence) internal pure returns (uint256 bid, uint256 ask) {
        uint256 delta = midPrice * confidence / CONFIDENCE_BASE;
        bid = delta >= midPrice ? 0 : midPrice - delta;
        ask = midPrice + delta;
    }
```

**File:** smart-contracts-poc/contracts/PriceProvider.sol (L215-217)
```text
        //    confidenceParam multiplies oracle spread; 0 means no spread
        uint256 adjustedSpread = spread * confidenceParam;
        (uint256 bid, uint256 ask) = _getBidAskFrom(mid, adjustedSpread);
```
