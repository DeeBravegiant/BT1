### Title
Depositors Can Exploit Stale `rsETHPrice` to Dilute Existing rsETH Holders — (File: contracts/LRTDepositPool.sol)

---

### Summary
`LRTDepositPool` mints rsETH using a stale stored `rsETHPrice` from `LRTOracle`. Because ETH staking rewards accrue continuously in EigenLayer strategies and ETH pods while `rsETHPrice` is only updated on explicit calls to the public `updateRSETHPrice()`, any depositor can deposit during the staleness window to receive more rsETH than the true exchange rate warrants, stealing accrued yield from existing holders.

---

### Finding Description

`LRTDepositPool.getRsETHAmountToMint()` computes the rsETH to mint as:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

`lrtOracle.rsETHPrice()` returns the **stored** `rsETHPrice` state variable, which is only updated when `updateRSETHPrice()` is explicitly called. [1](#0-0) [2](#0-1) 

`updateRSETHPrice()` is a **public, permissionless** function callable by anyone when the contract is not paused: [3](#0-2) 

Between calls, ETH staking rewards accrue inside EigenLayer strategies and ETH pods. `_getTotalEthInProtocol()` sums all assets including `getEffectivePodShares()` and `getAssetBalance()` across all NodeDelegators, so the true TVL grows continuously while `rsETHPrice` remains frozen. [4](#0-3) 

The internal `_updateRsETHPrice()` computes the new price as `(totalETHInProtocol - protocolFeeInETH) / rsethSupply`. Until this is called, the stored price is lower than the true price. [5](#0-4) 

**Attack path:**
1. Attacker observes that staking rewards have accrued (e.g., by comparing `getTotalAssetDeposits()` against `rsETHPrice * totalSupply`). The stored `rsETHPrice` is now below the true price.
2. Attacker calls `depositETH()` or `depositAsset()`. The minting formula divides by the stale lower `rsETHPrice`, yielding **more rsETH** than the true rate warrants.
3. Attacker (or anyone) calls `updateRSETHPrice()`. The price rises to reflect accrued rewards.
4. Attacker's rsETH is now worth more than what they paid. Existing holders' rsETH is worth proportionally less — their accrued yield has been diluted.

There is no freshness check on `rsETHPrice` in the deposit path, and no mechanism forces a price update before minting. [6](#0-5) 

---

### Impact Explanation

Existing rsETH holders lose a portion of their accrued staking yield on every deposit that occurs while `rsETHPrice` is stale. The attacker captures the yield delta between the stale and true price. This is a **theft of unclaimed yield** (High impact per the allowed scope). The magnitude scales with the size of the deposit and the length of the staleness window.

---

### Likelihood Explanation

**Medium.** No special privileges are required — any depositor can execute this. The attacker only needs to observe that rewards have accrued and deposit before `updateRSETHPrice()` is called. Since `updateRSETHPrice()` is called off-chain by bots or managers on a periodic schedule, a staleness window always exists. The `pricePercentageLimit` guard only blocks non-manager callers from applying price increases above the threshold; it does not prevent deposits at the stale price, and it does not eliminate the window. [7](#0-6) 

---

### Recommendation

Compute the rsETH price on-the-fly inside `getRsETHAmountToMint()` by calling `_getTotalEthInProtocol()` directly rather than reading the stored `rsETHPrice`, or call `updateRSETHPrice()` atomically at the start of each deposit transaction. Alternatively, enforce a price-freshness check that reverts deposits if `rsETHPrice` was last updated more than N blocks ago.

---

### Proof of Concept

```
Initial state:
  totalETH = 1000 ETH, totalRsETH = 1000, rsETHPrice = 1.000e18

Step 1: 10 ETH in staking rewards accrue inside EigenLayer pods.
  True price = 1010 / 1000 = 1.010e18
  Stored rsETHPrice = 1.000e18  (stale)

Step 2: Attacker calls depositETH{value: 100 ETH}(0, "").
  getRsETHAmountToMint = 100e18 * 1e18 / 1.000e18 = 100 rsETH minted
  (At true price, attacker should receive 100e18 / 1.010e18 ≈ 99.01 rsETH)

  New state: totalETH = 1110 ETH, totalRsETH = 1100

Step 3: updateRSETHPrice() is called.
  newRsETHPrice = 1110 / 1100 ≈ 1.009e18

Step 4: Attacker holds 100 rsETH worth 100 × 1.009 = 100.9 ETH.
  Attacker paid 100 ETH → profit ≈ 0.9 ETH extracted from existing holders' yield.

  Honest depositor at the same step (post-update):
    receives 100 / 1.009 ≈ 99.1 rsETH → worth exactly 100 ETH. No profit.
```

The attacker's profit comes entirely from yield that should have accrued to the 1000 pre-existing rsETH holders. [8](#0-7) [9](#0-8)

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

**File:** contracts/LRTOracle.sol (L331-349)
```text
    function _getTotalEthInProtocol() private view returns (uint256 totalETHInProtocol) {
        address lrtDepositPoolAddr = lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL);
        address[] memory supportedAssets = lrtConfig.getSupportedAssetList();
        uint256 supportedAssetCount = supportedAssets.length;

        for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
            address asset = supportedAssets[assetIdx];
            // assetER is in 1e18 precision (1.0 = 1e18)
            uint256 assetER = getAssetPrice(asset);
            // totalAssetAmt is in 1e18 precision (standard token decimals)
            uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);

            totalETHInProtocol += totalAssetAmt.mulWad(assetER);

            unchecked {
                ++assetIdx;
            }
        }
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
