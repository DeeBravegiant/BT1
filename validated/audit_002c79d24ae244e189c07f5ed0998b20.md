### Title
`LRTDepositPool.depositAsset()` / `depositETH()` Mints rsETH Using Stale Cached `rsETHPrice` Without Syncing the Oracle — (File: contracts/LRTDepositPool.sol)

---

### Summary

`LRTDepositPool.depositAsset()` and `depositETH()` compute the rsETH amount to mint using `lrtOracle.rsETHPrice()`, which is a **stored/cached** value that is only updated when `LRTOracle.updateRSETHPrice()` is explicitly called. Neither deposit function triggers a price update before minting. When rewards have accrued (stETH rebase, EigenLayer yield) but the price has not yet been refreshed, the stale lower `rsETHPrice` causes depositors to receive more rsETH than they are entitled to, diluting existing holders and stealing their unclaimed yield.

---

### Finding Description

`LRTOracle` stores `rsETHPrice` as a state variable updated only by explicit calls to `updateRSETHPrice()` (public, permissionless when unpaused) or `updateRSETHPriceAsManager()` (manager-only). The true current price is computed inside `_updateRsETHPrice()` by summing all protocol TVL across EigenLayer strategies and dividing by rsETH supply. [1](#0-0) 

The deposit flow in `LRTDepositPool` is:

1. `depositAsset()` / `depositETH()` → `_beforeDeposit()` → `getRsETHAmountToMint()`
2. `getRsETHAmountToMint()` reads `lrtOracle.rsETHPrice()` — the **cached** value — without first calling `updateRSETHPrice()`. [2](#0-1) [3](#0-2) 

The minted amount formula is:

```
rsethAmountToMint = (depositAmount × assetPrice) / rsETHPrice
```

When `rsETHPrice` is stale-low (rewards have accrued but the price has not been updated), the denominator is smaller than the true current price, so the depositor receives **more rsETH than their deposit is worth at the current true exchange rate**.

`_updateRsETHPrice()` also has critical side effects that are skipped: it mints protocol fees, updates `highestRsethPrice`, and can auto-pause the protocol if the price has dropped beyond `pricePercentageLimit`. None of these occur during a deposit. [4](#0-3) [5](#0-4) 

This is the direct analog of the `LiquidationSequencer` not calling `syncGlobalAccountingAndGracePeriod`: a critical accounting sync step is missing before a state-changing calculation that depends on up-to-date protocol values.

---

### Impact Explanation

**High — Theft of unclaimed yield.**

When stETH rebases or EigenLayer rewards accrue between two `updateRSETHPrice()` calls, the true rsETH/ETH rate is higher than the stored `rsETHPrice`. A depositor who calls `depositAsset()` in this window receives rsETH computed against the stale lower price, obtaining more rsETH than their deposit is worth at the true current rate. After `updateRSETHPrice()` is eventually called, the price rises to reflect the accrued rewards — but the extra rsETH already minted to the depositor dilutes all existing holders, effectively transferring their accrued yield to the depositor.

Additionally, if a slashing event has occurred that should trigger the auto-pause (price drop beyond `pricePercentageLimit`), the pause is never triggered during a deposit, allowing continued minting at a wrong price. [6](#0-5) 

---

### Likelihood Explanation

**Medium.** `updateRSETHPrice()` is not called atomically within the deposit flow. The protocol relies on off-chain keepers to call it periodically. Any window between a reward accrual event (stETH rebase occurs every ~24 hours; EigenLayer rewards accumulate continuously) and the next keeper call is exploitable. A sophisticated depositor can monitor on-chain TVL growth versus the stored `rsETHPrice` and time their deposit to capture the maximum spread. No special privileges are required — only a standard `depositAsset()` or `depositETH()` call. [7](#0-6) 

---

### Recommendation

`LRTDepositPool.depositAsset()` and `depositETH()` should call `ILRTOracle(lrtOracleAddress).updateRSETHPrice()` at the start of the deposit flow, before computing `getRsETHAmountToMint()`. This mirrors the fix applied to `LiquidationLibrary.batchLiquidateCdps` in the referenced report, ensuring that the price used for minting reflects the current true protocol state, including accrued rewards, fee minting, and any required safety pauses. [8](#0-7) 

---

### Proof of Concept

1. At time T₀, `updateRSETHPrice()` is called. `rsETHPrice = 1.05e18` (reflecting prior rewards). Protocol TVL = 1050 ETH, rsETH supply = 1000.

2. Between T₀ and T₁, stETH rebases: protocol TVL grows to 1060 ETH. True rsETH price = 1.06e18. `rsETHPrice` in storage is still `1.05e18`.

3. Attacker calls `depositAsset(stETH, 100e18, ...)`. `getRsETHAmountToMint` computes:
   ```
   rsethAmountToMint = (100e18 × 1e18) / 1.05e18 ≈ 95.238 rsETH
   ```
   At the true price of 1.06e18, the correct amount would be:
   ```
   100e18 / 1.06e18 ≈ 94.340 rsETH
   ```
   The attacker receives **~0.898 rsETH extra** (≈ 0.95 ETH of value stolen from existing holders).

4. Keeper calls `updateRSETHPrice()`. New TVL = 1160 ETH, new supply = 1095.238. New price = 1160/1095.238 ≈ 1.0591e18. The price increase that existing holders earned is now partially captured by the attacker's extra rsETH. [3](#0-2) [9](#0-8)

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

**File:** contracts/LRTOracle.sol (L214-250)
```text
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

**File:** contracts/LRTOracle.sol (L270-282)
```text
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
```

**File:** contracts/LRTDepositPool.sol (L99-118)
```text
    function depositAsset(
        address asset,
        uint256 depositAmount,
        uint256 minRSETHAmountExpected,
        string calldata referralId
    )
        external
        nonReentrant
        whenNotPaused
        onlySupportedERC20Token(asset)
    {
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(asset, depositAmount, minRSETHAmountExpected);

        // interactions
        IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);
        _mintRsETH(rsethAmountToMint);

        emit AssetDeposit(msg.sender, asset, depositAmount, rsethAmountToMint, referralId);
    }
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
