### Title
Stale Cached `rsETHPrice` Used in Deposit Minting Allows Yield Theft from Existing rsETH Holders - (File: contracts/LRTDepositPool.sol)

---

### Summary

`LRTDepositPool.getRsETHAmountToMint()` reads the stored state variable `LRTOracle.rsETHPrice` directly, without triggering a price refresh. Because `rsETHPrice` is a cached value updated only by explicit calls to `updateRSETHPrice()`, any depositor who deposits during a staleness window — after rewards have accrued but before the price is updated — receives more rsETH than they are entitled to, diluting the yield of all existing rsETH holders.

---

### Finding Description

`LRTOracle` stores the rsETH/ETH exchange rate as a persistent state variable: [1](#0-0) 

This value is updated only when `updateRSETHPrice()` is explicitly called: [2](#0-1) 

The function is public but is **never called atomically within the deposit flow**. When a user deposits via `depositETH()` or `depositAsset()`, the mint calculation reads the cached price directly: [3](#0-2) 

The `_updateRsETHPrice()` internal logic computes the true current price from live TVL: [4](#0-3) 

Between price updates, staking rewards accrue inside EigenLayer strategies and EigenPods, increasing `totalETHInProtocol`. The stored `rsETHPrice` therefore becomes **lower than the true current rate**. Because `rsethAmountToMint = (amount × assetPrice) / rsETHPrice`, a lower denominator inflates the rsETH minted per unit of deposit.

This is the direct Solidity analog of the reported web vulnerability: in the web case, `cache(async () => headers())` persisted a dynamic value (auth headers) that should have been fetched fresh on each request. Here, `rsETHPrice` is a persisted dynamic value that should be computed fresh on each deposit but is not.

---

### Impact Explanation

**High — Theft of unclaimed yield.**

Every unit of excess rsETH minted to a depositor during a staleness window represents a dilution of the share of TVL belonging to existing rsETH holders. The accrued staking rewards that caused the true price to rise above the stored price are effectively redistributed to the new depositor rather than remaining with existing holders. The magnitude scales with: (a) the size of the deposit, (b) the duration of the staleness window, and (c) the rate of reward accrual. In a protocol managing hundreds of millions in TVL with daily reward accrual, this is a continuous, repeatable extraction of yield.

---

### Likelihood Explanation

**Medium.** The protocol relies on off-chain bots or keepers to call `updateRSETHPrice()`. There will always be a non-zero window between reward accrual events (e.g., EigenLayer strategy balance increases, ETH staking rewards credited to EigenPods) and the next price update call. Any depositor — including a sophisticated attacker monitoring on-chain state — can observe when `rsETHPrice` is stale relative to the live TVL and deposit before the update. No privileged access, governance capture, or external protocol compromise is required.

---

### Recommendation

Call `_updateRsETHPrice()` (or its public wrapper) atomically at the start of `_beforeDeposit()` in `LRTDepositPool`, before computing `getRsETHAmountToMint()`. This ensures the price used for minting always reflects the current TVL, eliminating the staleness window entirely. Alternatively, compute the rsETH mint amount inline from live TVL rather than from the stored `rsETHPrice` state variable.

---

### Proof of Concept

1. At time T, `rsETHPrice = 1.05e18` (last updated). EigenLayer strategies accrue staking rewards; true price rises to `1.06e18`.
2. Attacker calls `LRTDepositPool.depositETH{value: 100 ether}(0, "")` before any keeper calls `updateRSETHPrice()`.
3. `getRsETHAmountToMint` executes: `rsethAmountToMint = (100e18 × 1e18) / 1.05e18 ≈ 95.238 rsETH`.
4. At the true current price of `1.06e18`, the correct mint would be: `100e18 / 1.06e18 ≈ 94.340 rsETH`.
5. The attacker receives `≈ 0.898 rsETH` excess — yield that belonged to existing holders — on a single 100 ETH deposit.
6. After `updateRSETHPrice()` is called, the new price is computed from the now-larger supply, permanently diluting existing holders. [5](#0-4) [6](#0-5)

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
