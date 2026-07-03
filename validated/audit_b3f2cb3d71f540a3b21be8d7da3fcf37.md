### Title
`highestRsethPrice` Can Be Artificially Inflated via Withdrawal Initiation, Triggering Permanent Protocol Pause - (`contracts/LRTOracle.sol`)

---

### Summary

`LRTOracle` tracks an all-time-high rsETH price in `highestRsethPrice`. If the live price ever drops more than `pricePercentageLimit` below this peak, `_updateRsETHPrice()` automatically pauses the deposit pool, withdrawal manager, and oracle. An unprivileged user can inflate `highestRsethPrice` to an unreachable level by exploiting the asymmetry between when rsETH is burned (at withdrawal initiation) and when the backing ETH leaves `totalETHInProtocol` (at withdrawal completion), then call the public `updateRSETHPrice()` after their ETH is returned to trigger the pause.

---

### Finding Description

`LRTOracle._updateRsETHPrice()` computes the rsETH price as:

```
newRsETHPrice = (totalETHInProtocol - protocolFeeInETH) / rsethSupply
```

`totalETHInProtocol` is the sum of ETH across all protocol locations, including `ethLyingInUnstakingVault` (the raw balance of `LRTUnstakingVault`) and `ethUnstakingFromEigenLayer`. [1](#0-0) 

When a user calls `initiateWithdrawal()`, rsETH is burned immediately (reducing `rsethSupply`), but the corresponding ETH remains inside the protocol — either in `LRTUnstakingVault` or still unstaking from EigenLayer — and is therefore still counted in `totalETHInProtocol`. [2](#0-1) 

This creates a window where `rsethSupply` is artificially low while `totalETHInProtocol` is unchanged, causing `newRsETHPrice` to spike. The spike updates `highestRsethPrice`: [3](#0-2) 

```solidity
// update highest price if new price exceeds it
if (newRsETHPrice > highestRsethPrice) {
    highestRsethPrice = newRsETHPrice;
}
```

Once the withdrawal completes and the ETH is returned to the user, `totalETHInProtocol` drops back to its true value. The price falls to its real level, but `highestRsethPrice` remains at the inflated peak. Any subsequent call to the public `updateRSETHPrice()` then hits the downside-protection branch: [4](#0-3) 

```solidity
if (newRsETHPrice < highestRsethPrice) {
    uint256 diff = highestRsethPrice - newRsETHPrice;
    bool isPriceDecreaseOffLimit =
        pricePercentageLimit > 0 && diff > pricePercentageLimit.mulWad(highestRsethPrice);
    if (isPriceDecreaseOffLimit) {
        if (!lrtDepositPool.paused()) lrtDepositPool.pause();
        if (!withdrawalManager.paused()) withdrawalManager.pause();
        _pause();
        return;
    }
```

The protocol is paused and only an admin can unpause it.

**Concrete example** (assuming `pricePercentageLimit` = 10%):

| Step | `totalETHInProtocol` | `rsethSupply` | `rsETHPrice` | `highestRsethPrice` |
|---|---|---|---|---|
| Baseline | 1000 ETH | 1000 rsETH | 1.00 | 1.00 |
| Attacker deposits 1000 ETH | 2000 ETH | 2000 rsETH | 1.00 | 1.00 |
| Attacker initiates withdrawal of 1000 rsETH | 2000 ETH | 1000 rsETH | **2.00** | **2.00** |
| Attacker calls `updateRSETHPrice()` | — | — | 2.00 | **2.00 locked** |
| Withdrawal completes, 1000 ETH returned | 1000 ETH | 1000 rsETH | **1.00** | 2.00 |
| Anyone calls `updateRSETHPrice()` | — | — | 1.00 | diff=1.00 > 10%×2.00=0.20 → **PAUSE** |

The attacker recovers their 1000 ETH (minus fees) and the protocol is permanently paused until admin intervention.

---

### Impact Explanation

All user deposits and withdrawals are frozen until an admin manually unpauses the three contracts (`LRTDepositPool`, `LRTWithdrawalManager`, `LRTOracle`). This constitutes a **temporary freezing of funds** for all protocol users. The attacker recovers their capital (minus a small fee), making the attack economically cheap to repeat.

---

### Likelihood Explanation

- `updateRSETHPrice()` is a public, permissionless function callable by anyone. [5](#0-4) 
- The withdrawal initiation path is a normal user-facing function with no special role requirement.
- The only cost to the attacker is the withdrawal fee and gas. The attack is repeatable after each admin unpause.
- The attack is most effective when `pricePercentageLimit` is set to a tight value (e.g., 5–20%), which is the intended operational range for the downside-protection feature.

---

### Recommendation

1. **Decouple `highestRsethPrice` from the withdrawal-inflated price.** Only update `highestRsethPrice` when the price increase is not attributable to a reduction in `rsethSupply` without a corresponding reduction in `totalETHInProtocol` (i.e., pending withdrawals).
2. **Exclude committed/pending withdrawal ETH from `totalETHInProtocol`** when computing the rsETH price, so the price is not artificially inflated during the withdrawal queue window. The `assetsCommitted` mapping in `LRTWithdrawalManager` already tracks this amount. [6](#0-5) 
3. **Add a cooldown or rate-limit** on `highestRsethPrice` updates to prevent single-block manipulation.

---

### Proof of Concept

```
1. Attacker calls LRTDepositPool.depositETH{value: N}(0, "") 
   → receives N rsETH (assuming 1:1 rate)

2. Attacker calls LRTWithdrawalManager.initiateWithdrawal(ETH, N)
   → N rsETH burned; rsethSupply drops by N
   → N ETH remains in LRTUnstakingVault (still counted in totalETHInProtocol)

3. Attacker calls LRTOracle.updateRSETHPrice()
   → newRsETHPrice = (originalTVL + N) / originalSupply  (≈ 2× if N = originalTVL)
   → highestRsethPrice = newRsETHPrice  (locked at inflated value)

4. Attacker waits for withdrawal delay (withdrawalDelayBlocks ≈ 8 days)
   → Calls LRTWithdrawalManager.completeWithdrawal(...)
   → N ETH returned to attacker; totalETHInProtocol drops back to originalTVL

5. Anyone calls LRTOracle.updateRSETHPrice()
   → newRsETHPrice = originalTVL / originalSupply  (back to 1.0)
   → diff = highestRsethPrice - newRsETHPrice  (≈ 1.0)
   → diff > pricePercentageLimit × highestRsethPrice  → TRUE (for any limit < 50%)
   → LRTDepositPool.pause(), LRTWithdrawalManager.pause(), LRTOracle._pause()
   → Protocol frozen
``` [7](#0-6) [8](#0-7)

### Citations

**File:** contracts/LRTDepositPool.sol (L480-500)
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

        address lrtConverter = lrtConfig.getContract(LRTConstants.LRT_CONVERTER);
        ethLyingInConverter = ILRTConverter(lrtConverter).ethValueInWithdrawal();
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L52-54)
```text
    // Asset amount committed to be withdrawn by users.
    mapping(address asset => uint256 amount) public assetsCommitted;

```

**File:** contracts/LRTWithdrawalManager.sol (L144-150)
```text
    /// @notice Initiates a withdrawal request for converting rsETH to a specified LST.
    /// @param asset The LST address the user wants to receive.
    /// @param rsETHUnstaked The amount of rsETH the user wishes to unstake.
    /// @dev This function is only callable by the user and is used to initiate a withdrawal request for a specific
    /// asset. Will be finalised by calling `completeWithdrawal` after the manager unlocked the request and the delay
    /// has past. There is an edge case were the user withdraws last underlying asset and that asset gets slashed.
    function initiateWithdrawal(
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L214-296)
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
```
