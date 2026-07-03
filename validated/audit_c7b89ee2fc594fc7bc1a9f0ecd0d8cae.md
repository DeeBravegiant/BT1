### Title
Stale `rsETHPrice` Enables Deposit-Before-Update / Withdraw-After-Update Sandwich to Extract Yield from Existing Holders - (File: contracts/LRTOracle.sol, contracts/LRTDepositPool.sol, contracts/LRTWithdrawalManager.sol)

---

### Summary

`LRTOracle.rsETHPrice` is a stored value that is **never refreshed inside the deposit or withdrawal-initiation flow**. Because `updateRSETHPrice()` is a separate, publicly callable function, an attacker can (1) deposit at a stale-low price to receive more rsETH than deserved, (2) call `updateRSETHPrice()` to push the price to its true higher value, and (3) initiate a withdrawal at the updated price to lock in a larger asset payout — extracting yield that belongs to existing holders.

---

### Finding Description

`LRTOracle` stores the exchange rate in a mutable variable: [1](#0-0) 

This variable is updated only when `updateRSETHPrice()` is called explicitly: [2](#0-1) 

The function is `public` and `whenNotPaused` — callable by any unprivileged address at any time.

`LRTDepositPool.getRsETHAmountToMint()` divides by the **stored** (potentially stale) `rsETHPrice`: [3](#0-2) 

Neither `depositETH` nor `depositAsset` calls `updateRSETHPrice()` before computing the mint amount: [4](#0-3) 

`LRTWithdrawalManager.initiateWithdrawal()` similarly reads the stored price to lock in `expectedAssetAmount`: [5](#0-4) [6](#0-5) 

The `expectedAssetAmount` is committed at initiation time and is the **ceiling** used at unlock: [7](#0-6) 

Because the price is never refreshed inside either flow, the ordering of external calls relative to `updateRSETHPrice()` is fully attacker-controlled.

---

### Impact Explanation

**Impact: High — Theft of unclaimed yield from existing rsETH holders.**

When staking rewards accrue, the true ETH-per-rsETH ratio rises above the stored `rsETHPrice`. An attacker who deposits before the price update receives more rsETH than the protocol's actual exchange rate warrants, diluting every existing holder. After triggering the price update, the attacker initiates a withdrawal at the now-correct (higher) price, locking in a payout that exceeds their original deposit. The difference is extracted from the yield that should have accrued to pre-existing holders.

Numerical example:
- State: `totalETH = 110`, `rsethSupply = 100`, `rsETHPrice = 1.0` (stale; true rate = 1.1 due to rewards).
- Attacker deposits 1 ETH → mints `1 / 1.0 = 1 rsETH` (correct would be `1 / 1.1 ≈ 0.909`).
- Attacker calls `updateRSETHPrice()` → new price ≈ `111 / 101 ≈ 1.099`.
- Attacker calls `initiateWithdrawal` with 1 rsETH → `expectedAssetAmount = 1 × 1.099 / assetPrice ≈ 1.099 ETH`.
- After the 8-day delay, attacker receives ≈ 1.099 ETH — a profit of ≈ 0.099 ETH at the expense of the 100 original holders.

---

### Likelihood Explanation

**Likelihood: Medium.**

- Stale prices are the normal state between oracle updates; the protocol does not auto-update on every block.
- `updateRSETHPrice()` is public and costs only gas.
- The attacker needs no special role, no flash loan, and no MEV infrastructure — only the ability to sequence three transactions.
- The 8-day withdrawal delay does not prevent the attack; it only defers the payout.
- The `pricePercentageLimit` guard limits per-update price jumps but does not eliminate the window: rewards accumulate continuously, and even a 0.5 % stale gap on a large deposit is profitable. [8](#0-7) 

---

### Recommendation

Refresh `rsETHPrice` at the **start** of every deposit and withdrawal-initiation call by invoking `_updateRsETHPrice()` internally before computing mint or payout amounts. This mirrors the Maverick mitigation of moving the bin at the beginning of a swap rather than at the end.

---

### Proof of Concept

**Attacker-controlled entry path:**

1. **`LRTDepositPool.depositETH{value: 1 ether}(0, "")`** — deposits at stale-low `rsETHPrice`; receives excess rsETH. [9](#0-8) 

2. **`LRTOracle.updateRSETHPrice()`** — public call; updates `rsETHPrice` to the true (higher) value reflecting accrued rewards. [2](#0-1) 

3. **`LRTWithdrawalManager.initiateWithdrawal(asset, rsETHAmount, "")`** — locks in `expectedAssetAmount` using the now-updated higher `rsETHPrice`. [10](#0-9) 

4. After `withdrawalDelayBlocks` (≈ 8 days), **`completeWithdrawal(asset, "")`** — receives the inflated payout. [11](#0-10) 

The necessary vulnerable step is that neither `depositETH` nor `initiateWithdrawal` calls `_updateRsETHPrice()` before reading `rsETHPrice`, leaving the attacker free to choose the most favorable price snapshot for each leg of the sandwich.

### Citations

**File:** contracts/LRTOracle.sol (L28-28)
```text
    uint256 public override rsETHPrice;
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L252-266)
```text
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
```

**File:** contracts/LRTDepositPool.sol (L76-93)
```text
    function depositETH(
        uint256 minRSETHAmountExpected,
        string calldata referralId
    )
        external
        payable
        nonReentrant
        whenNotPaused
        onlySupportedAsset(LRTConstants.ETH_TOKEN)
    {
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected);

        // interactions
        _mintRsETH(rsethAmountToMint);

        emit ETHDeposit(msg.sender, msg.value, rsethAmountToMint, referralId);
    }
```

**File:** contracts/LRTDepositPool.sol (L519-520)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
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

**File:** contracts/LRTWithdrawalManager.sol (L183-185)
```text
    function completeWithdrawal(address asset, string calldata referralId) external nonReentrant whenNotPaused {
        _processWithdrawalCompletion(asset, msg.sender, referralId);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L590-593)
```text
        ILRTOracle lrtOracle = ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE));

        // calculate underlying asset amount to receive based on rsETH amount and asset exchange rate
        underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
```

**File:** contracts/LRTWithdrawalManager.sol (L832-834)
```text
    {
        uint256 currentReturn = (request.rsETHUnstaked * rsETHPrice) / assetPrice;
        return (request.expectedAssetAmount < currentReturn) ? request.expectedAssetAmount : currentReturn;
```
