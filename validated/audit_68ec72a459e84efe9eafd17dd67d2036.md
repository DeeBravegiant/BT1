### Title
Stale `rsETHPrice` Used in Deposit Minting Calculation Without Forcing an Update - (File: contracts/LRTDepositPool.sol)

---

### Summary

`LRTDepositPool.getRsETHAmountToMint()` reads `lrtOracle.rsETHPrice()`, a stored state variable in `LRTOracle`, without first calling `updateRSETHPrice()`. Because `rsETHPrice` is only updated on explicit external calls, any depositor who acts during the staleness window — when staking rewards have accrued but the price has not yet been refreshed — receives more rsETH than the current actual rate warrants, stealing yield from existing holders.

---

### Finding Description

`LRTOracle` stores the rsETH/ETH exchange rate in the state variable `rsETHPrice`. [1](#0-0) 

This value is only updated when `updateRSETHPrice()` (public, callable by anyone) or `updateRSETHPriceAsManager()` (manager-only) is explicitly invoked. [2](#0-1) 

`LRTDepositPool.getRsETHAmountToMint()` divides by this stored value to determine how many rsETH tokens to mint for a depositor: [3](#0-2) 

Both `depositETH()` and `depositAsset()` call `_beforeDeposit()` → `getRsETHAmountToMint()` without ever calling `updateRSETHPrice()` first: [4](#0-3) [5](#0-4) 

As EigenLayer staking rewards accumulate, the actual ETH value backing each rsETH token increases continuously. However, `rsETHPrice` remains at its last-written value until someone explicitly calls `updateRSETHPrice()`. During this staleness window, the denominator in `getRsETHAmountToMint()` is artificially low, so the depositor receives more rsETH than the current fair rate entitles them to.

Additionally, `LRTOracle` enforces a `pricePercentageLimit` that causes `updateRSETHPrice()` to revert for non-managers if the price increase exceeds the threshold: [6](#0-5) 

This can extend the staleness window: if rewards have accumulated beyond the daily threshold, only a manager can push the update, meaning the stale price persists longer and the exploitable window widens.

---

### Impact Explanation

When a depositor acts while `rsETHPrice` is stale (lower than the true current rate), they receive:

```
rsETHMinted = (depositAmount × assetPrice) / staleLowerRsETHPrice
```

instead of the fair:

```
rsETHMinted = (depositAmount × assetPrice) / actualHigherRsETHPrice
```

The excess rsETH minted represents the accumulated staking yield that rightfully belongs to existing rsETH holders. Once `updateRSETHPrice()` is eventually called and the price rises to reflect the true TVL, the attacker's inflated rsETH balance is worth more than what they deposited. This is a direct theft of unclaimed yield from existing holders — **High severity**.

---

### Likelihood Explanation

Staking rewards on EigenLayer accrue continuously. Every block that passes without a price update creates a staleness window. The attack requires no special permissions: any unprivileged depositor can call `depositETH()` or `depositAsset()` at any time. The attacker does not need to collude with miners or validators — they simply need to deposit before the next `updateRSETHPrice()` call. Given that price updates are driven by off-chain bots or keepers (not enforced on-chain), gaps are routine and predictable. Likelihood is **Medium**.

---

### Recommendation

Call `updateRSETHPrice()` (or its internal equivalent `_updateRsETHPrice()`) at the start of `depositETH()` and `depositAsset()` before computing the rsETH amount to mint. This mirrors the Malt report's recommendation to inline data-refresh calls wherever sensitive values are consumed. Alternatively, expose an internal `_updateRsETHPrice()` path that bypasses the `pricePercentageLimit` revert for the deposit flow, or require that the price was updated within the current block.

---

### Proof of Concept

1. Assume `rsETHPrice = 1.00 ETH` (last updated 12 hours ago).
2. Over those 12 hours, staking rewards have accrued; the true rate is now `1.005 ETH` per rsETH.
3. Attacker calls `depositETH{value: 100 ETH}(0, "")`.
4. `getRsETHAmountToMint` computes: `100e18 * 1e18 / 1.00e18 = 100 rsETH`.
5. Fair amount at true rate: `100e18 * 1e18 / 1.005e18 ≈ 99.5 rsETH`.
6. Attacker receives `~0.5 rsETH` excess — the accumulated yield of existing holders.
7. A keeper then calls `updateRSETHPrice()`, raising `rsETHPrice` to `1.005 ETH`.
8. Attacker's 100 rsETH is now redeemable for `100 × 1.005 = 100.5 ETH`, a profit of `0.5 ETH` extracted from existing holders.

The attack scales linearly with deposit size and the length of the staleness window. With a `pricePercentageLimit` in place, the window can span multiple days if rewards exceed the daily threshold, amplifying the extractable amount. [7](#0-6) [8](#0-7)

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

**File:** contracts/LRTOracle.sol (L212-250)
```text
    /// @dev Internal function to update rsETH price
    // solhint-disable-next-line code-complexity
    function _updateRsETHPrice() internal {
        address rsETHTokenAddress = lrtConfig.rsETH();
        uint256 rsethSupply = IRSETH(rsETHTokenAddress).totalSupply();

        if (rsethSupply == 0) {
            rsETHPrice = 1 ether;
            highestRsethPrice = 1 ether;
            return;
        }

        if (highestRsethPrice == 0) {
            highestRsethPrice = rsETHPrice;
        }

        uint256 previousPrice = rsETHPrice;

        // get total ETH in the protocol (normalized to 1e18)
        uint256 totalETHInProtocol = _getTotalEthInProtocol();

        // calculate previousTVL using rsethSupply multiplied by rsETHPrice
        uint256 previousTVL = rsethSupply.mulWad(rsETHPrice);

        IPausable lrtDepositPool = IPausable(lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL));
        IPausable withdrawalManager = IPausable(lrtConfig.getContract(LRTConstants.LRT_WITHDRAW_MANAGER));

        // determine if the protocol is active (not paused)
        bool protocolPaused = lrtDepositPool.paused() || withdrawalManager.paused() || paused;

        // only take fee if TVL increased and protocol is not paused
        uint256 protocolFeeInETH = 0;
        if (!protocolPaused && totalETHInProtocol > previousTVL) {
            uint256 rewardAmount = totalETHInProtocol - previousTVL;
            protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000;
        }

        // compute new rsETH price based on total ETH minus fee
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
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

**File:** contracts/LRTDepositPool.sol (L86-92)
```text
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected);

        // interactions
        _mintRsETH(rsethAmountToMint);

        emit ETHDeposit(msg.sender, msg.value, rsethAmountToMint, referralId);
```

**File:** contracts/LRTDepositPool.sol (L110-117)
```text
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(asset, depositAmount, minRSETHAmountExpected);

        // interactions
        IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);
        _mintRsETH(rsethAmountToMint);

        emit AssetDeposit(msg.sender, asset, depositAmount, rsethAmountToMint, referralId);
```

**File:** contracts/LRTDepositPool.sol (L516-521)
```text
        address lrtOracleAddress = lrtConfig.getContract(LRTConstants.LRT_ORACLE);
        ILRTOracle lrtOracle = ILRTOracle(lrtOracleAddress);

        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

**File:** contracts/LRTDepositPool.sol (L648-670)
```text
    function _beforeDeposit(
        address asset,
        uint256 depositAmount,
        uint256 minRSETHAmountExpected
    )
        private
        view
        returns (uint256 rsethAmountToMint)
    {
        if (depositAmount == 0 || depositAmount < minAmountToDeposit) {
            revert InvalidAmountToDeposit();
        }

        if (_checkIfDepositAmountExceedesCurrentLimit(asset, depositAmount)) {
            revert MaximumDepositLimitReached();
        }

        rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);

        if (rsethAmountToMint < minRSETHAmountExpected) {
            revert MinimumAmountToReceiveNotMet();
        }
    }
```
