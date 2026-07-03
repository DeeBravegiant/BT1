### Title
Missing Bounds Validation on `setPricePercentageLimit` Disables rsETH Price Deviation Protection — (File: `contracts/LRTOracle.sol`)

### Summary
`LRTOracle.setPricePercentageLimit` accepts any value, including zero, with no lower or upper bound check. Because `pricePercentageLimit` is stored as a plain `uint256` that defaults to `0` on deployment, the price-deviation guard is **disabled from the moment the contract is live** until an admin explicitly sets a non-zero value. When the parameter is zero the public `updateRSETHPrice()` entry point bypasses both the upside threshold revert and the downside auto-pause, leaving all rsETH holders exposed.

### Finding Description
`LRTOracle.setPricePercentageLimit` writes the caller-supplied value directly to storage with no validation:

```solidity
// contracts/LRTOracle.sol  line 125-128
function setPricePercentageLimit(uint256 _pricePercentageLimit) external onlyLRTAdmin {
    pricePercentageLimit = _pricePercentageLimit;
    emit PricePercentageLimitUpdate(_pricePercentageLimit);
}
``` [1](#0-0) 

The parameter controls two guards inside `_updateRsETHPrice()`:

**Upside guard** — prevents non-managers from pushing a price increase beyond the threshold:
```solidity
// line 256-265
bool isPriceIncreaseOffLimit =
    pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);
if (isPriceIncreaseOffLimit) {
    if (!IAccessControl(address(lrtConfig)).hasRole(LRTConstants.MANAGER, msg.sender)) {
        revert PriceAboveDailyThreshold();
    }
}
``` [2](#0-1) 

**Downside guard** — auto-pauses the protocol when the price drops too far:
```solidity
// line 273-281
bool isPriceDecreaseOffLimit =
    pricePercentageLimit > 0 && diff > pricePercentageLimit.mulWad(highestRsethPrice);
if (isPriceDecreaseOffLimit) {
    if (!lrtDepositPool.paused()) lrtDepositPool.pause();
    if (!withdrawalManager.paused()) withdrawalManager.pause();
    _pause();
    return;
}
``` [3](#0-2) 

Both guards short-circuit to `false` whenever `pricePercentageLimit == 0`. Because Solidity zero-initialises storage, the contract is deployed in this unprotected state. There is no corresponding validation in `initialize`:

```solidity
// line 64-68
function initialize(address lrtConfigAddr) external initializer {
    UtilLib.checkNonZeroAddress(lrtConfigAddr);
    lrtConfig = ILRTConfig(lrtConfigAddr);
    emit UpdatedLRTConfig(lrtConfigAddr);
}
``` [4](#0-3) 

`updateRSETHPrice()` is an unrestricted public function:

```solidity
// line 87-89
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
``` [5](#0-4) 

### Impact Explanation
With `pricePercentageLimit == 0`:

1. **Upside bypass** — any unprivileged caller can invoke `updateRSETHPrice()` and commit an arbitrarily large price increase to `rsETHPrice` without triggering `PriceAboveDailyThreshold`. The intended design requires a manager to authorise large upward moves; that gate is silently removed.

2. **Downside bypass** — if the rsETH price falls sharply (e.g., due to EigenLayer slashing or an accounting error in `_getTotalEthInProtocol`), the auto-pause that should protect depositors and withdrawers never fires. The deposit pool and withdrawal manager remain open, allowing users to transact at a manipulated or slashed price.

The combined effect is that the protocol fails to deliver its promised price-safety guarantees. Depositors minting rsETH after a large unguarded price jump receive fewer tokens than the protocol's own threshold logic was designed to ensure; withdrawers redeeming after an unguarded price drop receive less underlying than they locked in.

**Impact class**: Low — contract fails to deliver promised returns (price-deviation protection). Escalates toward Medium (temporary freezing of funds) if the missing downside pause allows a slashing event to propagate unimpeded.

### Likelihood Explanation
The unprotected state is the **default** — it exists from block 0 of deployment without any admin action. Any operator delay in calling `setPricePercentageLimit` with a non-zero value leaves the window open. Additionally, the setter itself has no lower bound, so an admin can re-introduce the zero state at any time without the contract objecting.

### Recommendation
1. Enforce a non-zero, bounded value in both `initialize` and `setPricePercentageLimit`:

```solidity
uint256 public constant MAX_PRICE_PERCENTAGE_LIMIT = 0.5e18; // 50 %
uint256 public constant MIN_PRICE_PERCENTAGE_LIMIT = 0.001e18; // 0.1 %

function setPricePercentageLimit(uint256 _pricePercentageLimit) external onlyLRTAdmin {
    if (_pricePercentageLimit < MIN_PRICE_PERCENTAGE_LIMIT ||
        _pricePercentageLimit > MAX_PRICE_PERCENTAGE_LIMIT)
        revert InvalidPricePercentageLimit();
    pricePercentageLimit = _pricePercentageLimit;
    emit PricePercentageLimitUpdate(_pricePercentageLimit);
}
```

2. Set `pricePercentageLimit` to a safe default inside `initialize` so the guard is active from the first price update.

### Proof of Concept
1. `LRTOracle` is deployed; `pricePercentageLimit` is `0` (default).
2. Rewards accrue; `_getTotalEthInProtocol()` now returns a value 5 % above the previous TVL, implying a 5 % rsETH price increase.
3. An unprivileged EOA calls `updateRSETHPrice()`. Because `pricePercentageLimit == 0`, `isPriceIncreaseOffLimit` evaluates to `false`; the call succeeds and `rsETHPrice` is updated without manager authorisation.
4. Separately, a slashing event reduces TVL by 3 %. An unprivileged EOA calls `updateRSETHPrice()`. Because `pricePercentageLimit == 0`, `isPriceDecreaseOffLimit` evaluates to `false`; the deposit pool and withdrawal manager are **not** paused, and users continue to transact at the slashed price.
5. An admin later calls `setPricePercentageLimit(0)` (no revert); the same unprotected state is restored post-configuration.

### Citations

**File:** contracts/LRTOracle.sol (L64-68)
```text
    function initialize(address lrtConfigAddr) external initializer {
        UtilLib.checkNonZeroAddress(lrtConfigAddr);
        lrtConfig = ILRTConfig(lrtConfigAddr);
        emit UpdatedLRTConfig(lrtConfigAddr);
    }
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L125-128)
```text
    function setPricePercentageLimit(uint256 _pricePercentageLimit) external onlyLRTAdmin {
        pricePercentageLimit = _pricePercentageLimit;
        emit PricePercentageLimitUpdate(_pricePercentageLimit);
    }
```

**File:** contracts/LRTOracle.sol (L256-265)
```text
            bool isPriceIncreaseOffLimit =
                pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);

            // check if the price difference is above the threshold
            if (isPriceIncreaseOffLimit) {
                // if sender has a manager role, this doesn't revert.
                // if not, it reverts as price went above the threshold
                if (!IAccessControl(address(lrtConfig)).hasRole(LRTConstants.MANAGER, msg.sender)) {
                    revert PriceAboveDailyThreshold();
                }
```

**File:** contracts/LRTOracle.sol (L273-281)
```text
            bool isPriceDecreaseOffLimit =
                pricePercentageLimit > 0 && diff > pricePercentageLimit.mulWad(highestRsethPrice);

            // if price decrease is off limit, pause the protocol (unless it's already paused)
            if (isPriceDecreaseOffLimit) {
                if (!lrtDepositPool.paused()) lrtDepositPool.pause();
                if (!withdrawalManager.paused()) withdrawalManager.pause();
                _pause();
                return;
```
