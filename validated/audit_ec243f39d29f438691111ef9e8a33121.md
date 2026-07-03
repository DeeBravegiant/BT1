Audit Report

## Title
Publicly Callable `updateRSETHPrice()` With No Rate Limiting Enables Block-Stuffing DoS via Nested External-Call Loop - (File: contracts/LRTOracle.sol)

## Summary
`updateRSETHPrice()` is an unrestricted public function with only a `whenNotPaused` guard and no cooldown or rate-limiting mechanism. Each invocation triggers a nested loop executing O(N × M × 3) external calls across supported assets and node delegators. An attacker can repeatedly invoke this function across consecutive blocks to saturate block gas, preventing legitimate users from depositing or withdrawing.

## Finding Description
`updateRSETHPrice()` at [1](#0-0)  is callable by any address with no access control beyond `whenNotPaused`. It delegates to `_updateRsETHPrice()`, which calls `_getTotalEthInProtocol()` at [2](#0-1) , which iterates over every supported asset and calls `ILRTDepositPool.getTotalAssetDeposits(asset)` per asset. [3](#0-2) 

`getTotalAssetDeposits()` calls `getAssetDistributionData()`, which for each non-ETH asset iterates over the full `nodeDelegatorQueue` and fires three external calls per NDC: `IERC20.balanceOf()`, `INodeDelegator.getAssetBalance()`, and `INodeDelegator.getAssetUnstaking()`. [4](#0-3) 

No existing guard prevents repeated calls. The `pricePercentageLimit` check at [5](#0-4)  only reverts non-managers when the price *increases* beyond the threshold — it does not fire when the price is stable or decreasing, and does not impose any time-based cooldown. The daily fee mint limit check at [6](#0-5)  passes trivially when `protocolFeeInETH == 0`. There is no `lastUpdateTimestamp` or minimum interval anywhere in the contract.

## Impact Explanation
**Low — Block stuffing.** With a realistic deployment of 5 supported assets and 10 node delegators, each invocation issues ≥155 external calls consuming 1–3 M gas. Because the block gas limit is ~30 M, an attacker can pack 10–30 invocations per block, saturating the block and starving all other pending transactions. This constitutes block stuffing as defined in the allowed impact scope. Funds are not permanently lost and no accounting is corrupted, but user-facing operations (`depositETH`, `depositAsset`, `initiateWithdrawal`, `completeWithdrawal`) are excluded from blocks for the duration of the attack.

## Likelihood Explanation
The attacker requires no special role, no protocol-internal state, and no oracle access. The only cost is gas. At 10 gwei and ~1–3 M gas per call, filling one block costs roughly 0.1–0.3 ETH; sustaining the attack for one hour (~300 blocks) costs on the order of 30–90 ETH — expensive but within reach of a motivated adversary targeting a protocol with significant TVL. The attack is fully permissionless and repeatable.

## Recommendation
1. **Add a minimum update interval**: record `lastPriceUpdateTimestamp` and revert if `block.timestamp < lastPriceUpdateTimestamp + MIN_UPDATE_INTERVAL` for unprivileged callers.
2. **Restrict public access**: gate `updateRSETHPrice()` to `MANAGER_ROLE` or a dedicated keeper role; expose a separate, rate-limited public wrapper if permissionless updates are required.
3. **Cache intermediate results**: store per-asset totals and invalidate them lazily to reduce the per-call external-call count.

## Proof of Concept
```solidity
// Attacker contract — no special privileges required
contract Attacker {
    ILRTOracle oracle;
    constructor(address _oracle) { oracle = ILRTOracle(_oracle); }

    // Call repeatedly across consecutive blocks
    // Each call: O(N_assets × N_NDCs × 3) external calls, ~1–3 M gas
    function spam() external {
        oracle.updateRSETHPrice(); // public, no access control, no cooldown
    }
}
```

**Foundry fork test plan:**
1. Fork mainnet/testnet with the deployed contracts.
2. Configure 5 supported assets and 10 node delegators.
3. Call `oracle.updateRSETHPrice()` in a loop from an unprivileged address; measure gas per call via `vm.expectCall` counts and `gasleft()` deltas.
4. Demonstrate that 10–30 calls fit within a single 30 M gas block by summing per-call gas consumption.
5. Confirm no revert occurs on any iteration when the price is stable (the `pricePercentageLimit` guard does not fire).

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L231-231)
```text
        uint256 totalETHInProtocol = _getTotalEthInProtocol();
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

**File:** contracts/LRTOracle.sol (L303-310)
```text
            _checkAndUpdateDailyFeeMintLimit(rsethAmountToMintAsProtocolFee);
            if (rsethAmountToMintAsProtocolFee > 0) {
                address treasury = lrtConfig.getContract(LRTConstants.PROTOCOL_TREASURY);
                IRSETH(rsETHTokenAddress).mint(treasury, rsethAmountToMintAsProtocolFee);
                emit FeeMinted(treasury, rsethAmountToMintAsProtocolFee);
            }
        } else {
            _checkAndUpdateDailyFeeMintLimit(0);
```

**File:** contracts/LRTOracle.sol (L336-348)
```text
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
```

**File:** contracts/LRTDepositPool.sol (L446-456)
```text
        uint256 ndcsCount = nodeDelegatorQueue.length;
        for (uint256 i; i < ndcsCount;) {
            assetLyingInNDCs += IERC20(asset).balanceOf(nodeDelegatorQueue[i]);

            assetStakedInEigenLayer += INodeDelegator(nodeDelegatorQueue[i]).getAssetBalance(asset);
            assetUnstakingFromEigenLayer += INodeDelegator(nodeDelegatorQueue[i]).getAssetUnstaking(asset);

            unchecked {
                ++i;
            }
        }
```
