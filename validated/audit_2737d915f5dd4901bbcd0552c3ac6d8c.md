### Title
Stale `rsETHPrice` Enables Sandwich Attack on `updateRSETHPrice()` to Extract Yield from Existing Holders — (`contracts/LRTOracle.sol`)

---

### Summary

`LRTOracle.updateRSETHPrice()` is publicly callable with no access control. Deposits in `LRTDepositPool` use the stored (potentially stale) `rsETHPrice`. An unprivileged attacker can deposit ETH at the stale lower price, trigger the price update, and immediately initiate a withdrawal at the new higher price, locking in a profit that is extracted from existing rsETH holders' accrued yield.

---

### Finding Description

**Vulnerability class**: Fee/yield theft via price manipulation (sandwich attack on a publicly callable price-increasing function).

`LRTOracle.updateRSETHPrice()` is declared `public` with only a `whenNotPaused` guard:

```solidity
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
``` [1](#0-0) 

`_updateRsETHPrice()` computes the new price from live TVL and writes it to `rsETHPrice`:

```solidity
uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
...
rsETHPrice = newRsETHPrice;
``` [2](#0-1) 

`LRTDepositPool.getRsETHAmountToMint()` uses the **stored** `rsETHPrice`, not a freshly computed value:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [3](#0-2) 

`LRTWithdrawalManager.initiateWithdrawal()` locks in `expectedAssetAmount` at the time of the request using the current (post-update) `rsETHPrice`:

```solidity
uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);
``` [4](#0-3) 

```solidity
underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
``` [5](#0-4) 

**Attack flow**:

1. Rewards accrue in the protocol (e.g., stETH daily rebase, EigenLayer rewards) but `updateRSETHPrice()` has not yet been called. The stored `rsETHPrice` is stale and **lower** than the actual value.
2. Attacker deposits `X` ETH via `LRTDepositPool.depositETH()`. Because `rsETHPrice` is stale-low, the formula `rsethAmountToMint = X * assetPrice / rsETHPrice` yields **more rsETH than fair value** (`Y` rsETH where `Y > X / P_actual`).
3. Attacker calls `LRTOracle.updateRSETHPrice()`. The price rises to `P_new` (reflecting the accrued rewards). This call succeeds as long as the price increase is within `pricePercentageLimit`.
4. Attacker immediately calls `LRTWithdrawalManager.initiateWithdrawal()`. The `expectedAssetAmount` is locked at `Y * P_new / assetPrice`, which is **greater than X**.
5. After the `withdrawalDelayBlocks` (~8 days), attacker calls `completeWithdrawal()` and receives more ETH than deposited.

**Profit formula**:

```
Profit = X * (P_new / P_old - 1) = X * (accrued_reward_rate)
```

For `X = 1000 ETH` and a 0.01% daily reward rate, profit ≈ 0.1 ETH per attack cycle. The attack is repeatable every reward period.

---

### Impact Explanation

Existing rsETH holders' accrued yield is diluted. The attacker receives rsETH at a below-fair-value price (stale rate), then redeems at the updated fair-value rate. The difference is extracted from the pool of rewards that should have accrued proportionally to all existing holders. This is **theft of unclaimed yield** (High impact).

---

### Likelihood Explanation

**Medium-High**. The conditions are:
- Rewards accrue continuously (stETH rebases daily; EigenLayer rewards are periodic). The price is always stale between updates.
- `updateRSETHPrice()` is publicly callable — the attacker controls the timing of the price update.
- The only constraint is `pricePercentageLimit`: if the price increase exceeds the configured threshold, the public call reverts. For normal daily reward rates (< 0.05%), this threshold is not triggered.
- No special permissions are required. Any depositor can execute this.

---

### Recommendation

1. **Atomically update the price on deposit**: Call `_updateRsETHPrice()` (or read live TVL directly) inside `getRsETHAmountToMint()` so deposits always use the current price, not the stored stale value.
2. **Restrict `updateRSETHPrice()` to authorized callers** (e.g., `onlyLRTManager` or a keeper), removing the attacker's ability to control the timing of the price update.
3. **Alternatively**, introduce a deposit fee that exceeds the maximum possible single-update price increase, making the sandwich unprofitable.

---

### Proof of Concept

```
State: rsETHPrice = 1.00 ETH (stale), actual TVL implies 1.001 ETH (0.1% reward accrued)

1. Attacker deposits 1000 ETH
   → rsethAmountToMint = 1000 * 1e18 / 1.000e18 = 1000 rsETH
   (fair amount at 1.001 rate would be 999.001 rsETH)

2. Attacker calls updateRSETHPrice()
   → rsETHPrice updated to 1.001 ETH

3. Attacker calls initiateWithdrawal(ETH, 1000 rsETH)
   → expectedAssetAmount = 1000 * 1.001e18 / 1e18 = 1001 ETH

4. After 8-day delay, completeWithdrawal()
   → Attacker receives 1001 ETH

Net profit: 1 ETH extracted from existing holders' yield
``` [1](#0-0) [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L250-313)
```text
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);

        if (newRsETHPrice > highestRsethPrice) {
            // check if the price is above the threshold
            uint256 priceDifference = newRsETHPrice - highestRsethPrice;
            // pricePercentageLimit is in 1e18 precision (100% = 1e18, 1% = 1e16)
            bool isPriceIncreaseOffLimit =
                pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);

            // check if the price difference is above the threshold
            if (isPriceIncreaseOffLimit) {
                // if sender has a manager role, this doesn't revert.
                // if not, it reverts as price went above the threshold
                if (!IAccessControl(address(lrtConfig)).hasRole(LRTConstants.MANAGER, msg.sender)) {
                    revert PriceAboveDailyThreshold();
                }
            }
        }

        // downside protection — pause if price drops too far
        if (newRsETHPrice < highestRsethPrice) {
            uint256 diff = highestRsethPrice - newRsETHPrice;
            // normalizing to 1e18
            bool isPriceDecreaseOffLimit =
                pricePercentageLimit > 0 && diff > pricePercentageLimit.mulWad(highestRsethPrice);

            // if price decrease is off limit, pause the protocol (unless it's already paused)
            if (isPriceDecreaseOffLimit) {
                if (!lrtDepositPool.paused()) lrtDepositPool.pause();
                if (!withdrawalManager.paused()) withdrawalManager.pause();
                _pause();
                return;
            }

            // if price has decreased compared to the previous price, emit an event to reflect that
            if (previousPrice > newRsETHPrice) {
                emit RsETHPriceDecrease(newRsETHPrice, previousPrice);
            }

            // emit an event to notify that the price is currently below the peak (all time high) price
            emit RsETHPriceBelowPeak(highestRsethPrice, newRsETHPrice);
        }

        // update highest price if new price exceeds it
        if (newRsETHPrice > highestRsethPrice) {
            highestRsethPrice = newRsETHPrice;
        }

        // mint protocol fee as rsETH if there's a fee to take
        if (protocolFeeInETH > 0) {
            // Calculate rsETH amount to mint as protocol fee
            uint256 rsethAmountToMintAsProtocolFee = protocolFeeInETH.divWad(newRsETHPrice);

            _checkAndUpdateDailyFeeMintLimit(rsethAmountToMintAsProtocolFee);
            if (rsethAmountToMintAsProtocolFee > 0) {
                address treasury = lrtConfig.getContract(LRTConstants.PROTOCOL_TREASURY);
                IRSETH(rsETHTokenAddress).mint(treasury, rsethAmountToMintAsProtocolFee);
                emit FeeMinted(treasury, rsethAmountToMintAsProtocolFee);
            }
        } else {
            _checkAndUpdateDailyFeeMintLimit(0);
        }

        rsETHPrice = newRsETHPrice;
```

**File:** contracts/LRTDepositPool.sol (L506-521)
```text
    function getRsETHAmountToMint(
        address asset,
        uint256 amount
    )
        public
        view
        override
        returns (uint256 rsethAmountToMint)
    {
        // setup oracle contract
        address lrtOracleAddress = lrtConfig.getContract(LRTConstants.LRT_ORACLE);
        ILRTOracle lrtOracle = ILRTOracle(lrtOracleAddress);

        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L150-178)
```text
    function initiateWithdrawal(
        address asset,
        uint256 rsETHUnstaked,
        string calldata referralId
    )
        external
        override
        nonReentrant
        whenNotPaused
        onlySupportedAsset(asset)
        onlySupportedStrategy(asset)
    {
        if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
            revert InvalidAmountToWithdraw();
        }

        IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);

        uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);

        if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();

        // preventing over-withdrawal.
        assetsCommitted[asset] += expectedAssetAmount;

        _addUserWithdrawalRequest(asset, rsETHUnstaked, expectedAssetAmount);

        emit ReferralIdEmitted(referralId);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L580-594)
```text
    function getExpectedAssetAmount(
        address asset,
        uint256 amount
    )
        public
        view
        override
        returns (uint256 underlyingToReceive)
    {
        // setup oracle contract
        ILRTOracle lrtOracle = ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE));

        // calculate underlying asset amount to receive based on rsETH amount and asset exchange rate
        underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
    }
```
