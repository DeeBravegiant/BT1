### Title
Stale Updater Permissions Persist After `transferProviderOwnership`, Allowing Old Updaters to Manipulate Oracle Bid/Ask Spread — (`smart-contracts-poc/contracts/PriceProviderFactory.sol`, `smart-contracts-poc/contracts/AnchoredProviderFactory.sol`)

---

### Summary

`PriceProviderFactory`, `PriceProviderFactoryL2`, and `AnchoredProviderFactory` each maintain an `isUpdater[provider][updater]` mapping that grants addresses the right to call `setConfidence()` and adjust the oracle's `confidenceParam`. When `transferProviderOwnership()` is called, the `providerOwner` record is updated but the `isUpdater` entries for all previously-granted updaters are **never cleared**. Those stale updaters retain the ability to call `setConfidence()` on the transferred provider indefinitely, without the new owner's knowledge or consent.

---

### Finding Description

In all three factory contracts the storage layout is:

```solidity
mapping(address provider => address) public providerOwner;
mapping(address provider => mapping(address updater => bool)) public isUpdater;
```

`transferProviderOwnership` only updates `providerOwner` and the `_providersByCreator` enumerable sets:

```solidity
function transferProviderOwnership(address provider, address newOwner)
    external override onlyProviderOwner(provider)
{
    // ...
    providerOwner[provider] = newOwner;
    _providersByCreator[previousOwner].remove(provider);
    _providersByCreator[newOwner].add(provider);
    // isUpdater[provider][*] is NEVER touched
    emit ProviderOwnershipTransferred(provider, previousOwner, newOwner);
}
```

The updater authorization check in `_requireUpdater` passes for any address still set in `isUpdater`:

```solidity
function _requireUpdater(address provider) internal view {
    if (msg.sender != providerOwner[provider] && !isUpdater[provider][msg.sender])
        revert NotProviderUpdater();
}
```

So after transfer, every address the **previous** owner granted via `grantUpdater()` can still call `setConfidence()` and push a new `confidenceParam` to the provider.

---

### Impact Explanation

`confidenceParam` directly controls the bid/ask spread delivered to the pool at swap time. The spread formula is:

```
adjustedSpread = oracleSpread × confidenceParam
delta          = midPrice × adjustedSpread / CONFIDENCE_BASE
bid            = midPrice − delta
ask            = midPrice + delta
```

A stale updater can set `confidenceParam` to `CONFIDENCE_MAX` (up to `1_000_000`), multiplying the oracle's native spread by up to 100×. For a 5 bps oracle spread this produces a ±5% bid/ask band. Every swap executed against the pool while this inflated spread is active receives a materially worse price: sellers receive a bid that is up to 5% below mid, buyers pay an ask up to 5% above mid. This is a **bad-price execution** impact that directly harms traders' token balances without any on-chain guard preventing it.

The `AnchoredPriceProvider` variant has envelope-validated band parameters, but `confidenceParam` is a separate mutable knob that sits on top of those bounds and is not constrained by the envelope.

---

### Likelihood Explanation

The precondition is that the previous owner granted at least one updater before transferring. This is a normal operational pattern (the factory ships a `SetConfidence` deployment script that assumes a dedicated updater key). A malicious previous owner can deliberately retain an updater address they control, or a legitimately-granted updater key that was compromised after the transfer can be exploited. The new owner has no on-chain visibility into which updaters exist and no way to enumerate or bulk-revoke them without knowing each address individually.

---

### Recommendation

Clear all updater permissions on transfer, or add an owner-epoch key to the `isUpdater` mapping so that permissions granted under a previous owner are automatically invalidated:

**Option A – epoch key (mirrors the recommended fix in the reference report):**
```solidity
mapping(address provider => mapping(address owner => mapping(address updater => bool))) public isUpdater;
```
Permissions are then scoped to `(provider, currentOwner, updater)`, so they expire automatically when `providerOwner[provider]` changes.

**Option B – explicit revocation on transfer:**
```solidity
// Caller must supply the list of updaters to revoke; or maintain an enumerable set.
function transferProviderOwnership(address provider, address newOwner, address[] calldata updatersToClear) external ...
```

---

### Proof of Concept

```
1. Owner A creates a provider via PriceProviderFactory.createPriceProvider().
2. Owner A calls grantUpdater(provider, maliciousUpdater).
   → isUpdater[provider][maliciousUpdater] = true
3. Owner A calls transferProviderOwnership(provider, ownerB).
   → providerOwner[provider] = ownerB
   → isUpdater[provider][maliciousUpdater] still = true  ← BUG
4. ownerB is unaware of maliciousUpdater.
5. After CONFIDENCE_COOLDOWN elapses, maliciousUpdater calls:
       factory.setConfidence([provider], [1_000_000])
   → PriceProvider(provider).setConfidenceParam(1_000_000) succeeds
   → confidenceParam is now at maximum, widening bid/ask by up to 100×
6. Any swap against the pool that uses this provider executes at the
   inflated spread, giving traders a price up to 5% worse than mid.
```

The existing test `testOldOwnerCannotUpdateAfterTransfer` (which passes) only checks that the **previous owner** loses direct update rights. It does not test whether **previously-granted updaters** retain access after transfer — that gap is the root cause. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7) [9](#0-8) [10](#0-9)

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

**File:** smart-contracts-poc/contracts/AnchoredProviderFactory.sol (L46-46)
```text
    mapping(address provider => mapping(address updater => bool)) public isUpdater;
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

**File:** smart-contracts-poc/contracts/AnchoredProviderFactory.sol (L246-256)
```text
    function grantUpdater(address provider, address updater) external override onlyProviderOwner(provider) {
        require(_providers.contains(provider), ProviderNotTracked());
        isUpdater[provider][updater] = true;
        emit UpdaterGranted(provider, updater);
    }

    function revokeUpdater(address provider, address updater) external override onlyProviderOwner(provider) {
        require(_providers.contains(provider), ProviderNotTracked());
        isUpdater[provider][updater] = false;
        emit UpdaterRevoked(provider, updater);
    }
```

**File:** smart-contracts-poc/contracts/AnchoredProviderFactory.sol (L262-274)
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
            AnchoredPriceProvider(providers[i]).setConfidenceParam(values[i]);
        }
    }
```

**File:** smart-contracts-poc/contracts/PriceProvider.sol (L92-104)
```text
    function setConfidenceParam(uint256 newValue) external {
        require(msg.sender == factory, OnlyFactory());
        if (newValue > CONFIDENCE_MAX) {
            revert ConfidenceParamOutOfBounds();
        }
        if (block.timestamp < lastConfidenceUpdate + CONFIDENCE_COOLDOWN) {
            revert CooldownNotElapsed();
        }

        confidenceParam = newValue;
        lastConfidenceUpdate = block.timestamp;
        emit ConfidenceParamSet(newValue);
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
