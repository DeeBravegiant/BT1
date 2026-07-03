### Title
Direct ETH/LST Donation to `LRTDepositPool` Inflates `rsETHPrice` and Triggers Excess Protocol Fee Minting - (`contracts/LRTDepositPool.sol`, `contracts/LRTOracle.sol`)

---

### Summary

`LRTDepositPool.getETHDistributionData()` and `getAssetDistributionData()` measure protocol TVL using raw on-chain balances (`address(this).balance`, `IERC20(asset).balanceOf(...)`). Because `LRTDepositPool` exposes an unrestricted `receive()` function, any unprivileged actor can donate ETH (or directly transfer LST tokens) to the deposit pool or any NodeDelegator, inflating the reported TVL. When `LRTOracle.updateRSETHPrice()` is subsequently called, the inflated TVL causes an artificially high `newRsETHPrice`, which triggers excess protocol fee rsETH to be minted to the treasury — diluting all existing rsETH holders.

---

### Finding Description

`LRTOracle._updateRsETHPrice()` computes the new rsETH price as:

```
newRsETHPrice = (totalETHInProtocol - protocolFeeInETH) / rsethSupply
```

where `totalETHInProtocol` is sourced from `_getTotalEthInProtocol()`, which calls `ILRTDepositPool.getTotalAssetDeposits(asset)` for every supported asset.

For ETH, `getETHDistributionData()` returns:

```solidity
ethLyingInDepositPool = address(this).balance;          // line 480
ethLyingInNDCs += nodeDelegatorQueue[i].balance;        // line 485
ethLyingInUnstakingVault = lrtUnstakingVault.balance;   // line 496
```

For LSTs, `getAssetDistributionData()` returns:

```solidity
assetLyingInDepositPool = IERC20(asset).balanceOf(address(this));          // line 444
assetLyingInNDCs += IERC20(asset).balanceOf(nodeDelegatorQueue[i]);        // line 448
assetLyingUnstakingVault = IERC20(asset).balanceOf(lrtUnstakingVault);     // line 461
```

All of these are raw balance reads with no access control. `LRTDepositPool` has an open `receive()` function:

```solidity
receive() external payable { }   // line 58
```

An attacker can send ETH directly to `LRTDepositPool`, which immediately inflates `address(this).balance` and therefore `totalETHInProtocol`. When `updateRSETHPrice()` is called:

1. `totalETHInProtocol > previousTVL` (where `previousTVL = rsethSupply * rsETHPrice`)
2. `rewardAmount = totalETHInProtocol - previousTVL` is inflated by the donation
3. `protocolFeeInETH = rewardAmount * protocolFeeInBPS / 10_000` is inflated
4. Excess rsETH is minted to the treasury: `rsethAmountToMintAsProtocolFee = protocolFeeInETH / newRsETHPrice`
5. `rsETHPrice` is updated to the inflated value

The excess fee rsETH minted to the treasury dilutes all existing rsETH holders. Additionally, if the inflated price increase exceeds `pricePercentageLimit`, `updateRSETHPrice()` reverts for all non-manager callers, causing a DoS on price updates.

---

### Impact Explanation

**Primary — High: Theft of unclaimed yield.**
The excess protocol fee rsETH minted to the treasury represents yield that belongs to rsETH holders (it is taken from the inflated "reward" that was never actually earned). Every rsETH holder's share of the underlying ETH is diluted by the spurious fee mint. The attacker sacrifices the donated ETH but causes a permanent dilution of rsETH holders proportional to `protocolFeeInBPS * donationAmount / totalETHInProtocol`.

**Secondary — Medium: Temporary freezing of price updates.**
If the donation is large enough to push `newRsETHPrice` above `highestRsethPrice * (1 + pricePercentageLimit)`, the call to `updateRSETHPrice()` reverts for any non-manager caller, freezing the oracle until a manager intervenes.

---

### Likelihood Explanation

- The attack requires only a direct ETH transfer to `LRTDepositPool`, which is permissionless via the open `receive()` function.
- No privileged access, no front-running dependency, and no complex setup is needed.
- The cost to the attacker is the donated ETH, but the damage (excess fee minting, price inflation) is proportional to the donation and is permanent once `updateRSETHPrice()` is called.
- The `RSETHPriceFeed` contract exposes the inflated `rsETHPrice` to external integrators, amplifying the impact on any protocol consuming this feed.

---

### Recommendation

1. **Track ETH deposits explicitly** using an internal accounting variable (e.g., `totalETHDeposited`) that is incremented only through the authorized deposit paths (`depositETH`, `receiveFromRewardReceiver`, `receiveFromNodeDelegator`, `receiveFromLRTConverter`), rather than reading `address(this).balance`.
2. **Track LST balances explicitly** using internal accounting variables incremented on `depositAsset` and decremented on transfers out, rather than reading `IERC20(asset).balanceOf(address(this))`.
3. Alternatively, restrict the `receive()` function to only accept ETH from known, authorized senders (NodeDelegators, reward receivers, etc.).

---

### Proof of Concept

```
State before attack:
  rsethSupply = 1000 ETH worth of rsETH
  rsETHPrice  = 1.05e18 (1.05 ETH per rsETH)
  previousTVL = 1000 * 1.05e18 = 1050 ETH
  protocolFeeInBPS = 1000 (10%)

Attacker sends 100 ETH directly to LRTDepositPool via receive().

State when updateRSETHPrice() is called:
  totalETHInProtocol = 1050 + 100 = 1150 ETH  (inflated)
  rewardAmount       = 1150 - 1050 = 100 ETH  (entirely the donation)
  protocolFeeInETH   = 100 * 10% = 10 ETH
  newRsETHPrice      = (1150 - 10) / 1000 = 1.14e18

  rsethAmountToMintAsProtocolFee = 10 ETH / 1.14e18 ≈ 8.77 rsETH minted to treasury

Result:
  - Treasury receives ~8.77 rsETH it did not earn
  - All existing rsETH holders are diluted
  - rsETHPrice is inflated from 1.05 to 1.14 (affecting external protocols via RSETHPriceFeed)
```

**Entry path:**
1. Attacker calls `LRTDepositPool.receive()` (or sends ETH via `transfer`/`call`) — no access control
2. `getETHDistributionData()` at line 480 reads `address(this).balance`, which now includes the donation
3. `_getTotalEthInProtocol()` at line 341 calls `getTotalAssetDeposits(ETH_TOKEN)`, returning the inflated value
4. `_updateRsETHPrice()` at line 244–307 computes excess fee and mints it to treasury [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** contracts/LRTDepositPool.sol (L58-58)
```text
    receive() external payable { }
```

**File:** contracts/LRTDepositPool.sol (L444-461)
```text
        assetLyingInDepositPool = IERC20(asset).balanceOf(address(this));

        uint256 ndcsCount = nodeDelegatorQueue.length;
        for (uint256 i; i < ndcsCount;) {
            assetLyingInNDCs += IERC20(asset).balanceOf(nodeDelegatorQueue[i]);

            assetStakedInEigenLayer += INodeDelegator(nodeDelegatorQueue[i]).getAssetBalance(asset);
            assetUnstakingFromEigenLayer += INodeDelegator(nodeDelegatorQueue[i]).getAssetUnstaking(asset);

            unchecked {
                ++i;
            }
        }

        address lrtUnstakingVault = lrtConfig.getContract(LRTConstants.LRT_UNSTAKING_VAULT);

        assetLyingInConverter = 0; // assets in converter are accounted in their eth value => getETHDistributionData()
        assetLyingUnstakingVault = IERC20(asset).balanceOf(lrtUnstakingVault);
```

**File:** contracts/LRTDepositPool.sol (L480-496)
```text
        ethLyingInDepositPool = address(this).balance;

        uint256 ndcsCount = nodeDelegatorQueue.length;

        for (uint256 i; i < ndcsCount;) {
            ethLyingInNDCs += nodeDelegatorQueue[i].balance;

            ethStakedInEigenLayer += INodeDelegator(nodeDelegatorQueue[i]).getEffectivePodShares();
            ethUnstakingFromEigenLayer += INodeDelegator(nodeDelegatorQueue[i])
                .getAssetUnstaking(LRTConstants.ETH_TOKEN);
            unchecked {
                ++i;
            }
        }

        address lrtUnstakingVault = lrtConfig.getContract(LRTConstants.LRT_UNSTAKING_VAULT);
        ethLyingInUnstakingVault = lrtUnstakingVault.balance;
```

**File:** contracts/LRTOracle.sol (L244-307)
```text
        if (!protocolPaused && totalETHInProtocol > previousTVL) {
            uint256 rewardAmount = totalETHInProtocol - previousTVL;
            protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000;
        }

        // compute new rsETH price based on total ETH minus fee
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
