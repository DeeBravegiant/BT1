### Title
Publicly Callable `updateRSETHPrice()` Allows Stale-Price Exploitation to Steal Accrued Yield — (File: contracts/LRTOracle.sol)

---

### Summary

`LRTOracle.updateRSETHPrice()` carries no access control beyond `whenNotPaused`, so any address may call it at will. Because deposits and withdrawals consume the **cached** `rsETHPrice` storage variable rather than computing the live rate on-the-fly, a window opens whenever staking rewards cause the true TVL-based price to exceed the stored value. An unprivileged attacker can deposit at the stale (lower) price, then trigger the price update themselves, and initiate a withdrawal at the now-correct (higher) price — capturing a portion of the accrued yield that belongs to existing rsETH holders.

---

### Finding Description

`LRTOracle.rsETHPrice` is a storage variable that is only refreshed when `_updateRsETHPrice()` executes. [1](#0-0) 

There is no access restriction on the public entry point:

```solidity
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
```

The deposit path in `LRTDepositPool` reads the **stored** `rsETHPrice` directly:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [2](#0-1) 

The withdrawal initiation path does the same:

```solidity
underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
``` [3](#0-2) 

Neither `depositETH`, `depositAsset`, nor `initiateWithdrawal` calls `updateRSETHPrice()` before consuming the stored value. [4](#0-3) 

The internal price computation reads the **live** TVL at call time:

```solidity
uint256 totalETHInProtocol = _getTotalEthInProtocol();
...
uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
``` [5](#0-4) 

This means `rsETHPrice` (stored) and the true exchange rate (live TVL / supply) **diverge** every time staking rewards accrue — the exact same two-state divergence described in the Tracer DAO report, just expressed as a price gap rather than a timestamp gap.

---

### Impact Explanation

**High — Theft of unclaimed yield.**

When rewards push the true price above the stored `rsETHPrice`, a depositor receives more rsETH than the protocol's TVL justifies. After the price is corrected upward, those extra rsETH tokens are redeemable for more underlying assets than were deposited. The surplus is extracted from the yield that should have accrued pro-rata to all existing rsETH holders.

Concrete example (simplified, 1% reward epoch):

| Step | TVL (ETH) | rsETH supply | rsETHPrice (stored) |
|------|-----------|--------------|---------------------|
| Before rewards | 1 000 | 1 000 | 1.000 |
| After rewards, before update | 1 010 | 1 000 | **1.000 (stale)** |
| Attacker deposits 100 ETH | 1 110 | **1 100** | 1.000 (stale) |
| After `updateRSETHPrice()` | 1 110 | 1 100 | **1.009** |
| Attacker withdraws 100 rsETH | — | 1 000 | 1.009 → **100.9 ETH returned** |

Profit = **0.9 ETH** extracted from the 10 ETH reward pool belonging to the original 1 000 rsETH holders.

---

### Likelihood Explanation

**Medium.**

- Staking rewards accrue continuously; the price gap opens on every reward epoch.
- No privileged access is required — any EOA can call `updateRSETHPrice()` and `depositETH` / `initiateWithdrawal`.
- The attacker only needs to monitor on-chain TVL (e.g., via `getTotalAssetDeposits`) and act when `actualPrice > rsETHPrice`.
- The 8-day withdrawal delay (`withdrawalDelayBlocks`) reduces frequency but does not eliminate profitability; the expected payout is locked at `initiateWithdrawal` time via `getExpectedAssetAmount`. [6](#0-5) 
- `pricePercentageLimit` caps single-update price jumps for non-managers but does not prevent the attack across smaller, repeated reward epochs, and may be set to zero. [7](#0-6) 

---

### Recommendation

1. **Atomically refresh the price inside deposit and withdrawal entry points** — call `_updateRsETHPrice()` (or an equivalent internal read of live TVL) before computing mint/redeem amounts, so the stored value is never stale at the moment of user interaction.
2. **Alternatively, restrict `updateRSETHPrice()` to a trusted keeper or the deposit/withdrawal contracts**, mirroring the Tracer DAO recommendation to restrict `SMAOracle.poll()` to `PoolKeeper`/`KeeperRewards`. This ensures the stored price advances only in lockstep with protocol operations.

---

### Proof of Concept

```
1. Observe: getTotalAssetDeposits(ETH) returns 1010 ETH; LRTOracle.rsETHPrice() returns 1.000e18.
   → True price ≈ 1.010e18; stored price is stale.

2. Call LRTDepositPool.depositETH{value: 100 ether}(0, "");
   → getRsETHAmountToMint mints 100 * 1e18 / 1.000e18 = 100 rsETH
     (should have minted 100 * 1e18 / 1.010e18 ≈ 99.01 rsETH).

3. Call LRTOracle.updateRSETHPrice();
   → rsETHPrice updated to (1110 ETH) / (1100 rsETH) ≈ 1.009e18.

4. Call LRTWithdrawalManager.initiateWithdrawal(ETH, 100e18, "");
   → getExpectedAssetAmount = 100 * 1.009e18 / 1e18 = 100.9 ETH locked in request.

5. After withdrawalDelayBlocks, call completeWithdrawal(ETH, "");
   → Receive 100.9 ETH; net profit ≈ 0.9 ETH extracted from existing holders' yield.
```

Entry path: `LRTDepositPool.depositETH` (public, no role) → `_beforeDeposit` → `getRsETHAmountToMint` → `lrtOracle.rsETHPrice()` (stale). [8](#0-7) 
Price update: `LRTOracle.updateRSETHPrice()` (public, no role). [1](#0-0) 
Withdrawal lock-in: `LRTWithdrawalManager.initiateWithdrawal` → `getExpectedAssetAmount` → `lrtOracle.rsETHPrice()`. [9](#0-8)

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L231-250)
```text
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

**File:** contracts/LRTDepositPool.sol (L86-92)
```text
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected);

        // interactions
        _mintRsETH(rsethAmountToMint);

        emit ETHDeposit(msg.sender, msg.value, rsethAmountToMint, referralId);
```

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
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

**File:** contracts/LRTWithdrawalManager.sol (L167-168)
```text

        uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);
```

**File:** contracts/LRTWithdrawalManager.sol (L592-594)
```text
        // calculate underlying asset amount to receive based on rsETH amount and asset exchange rate
        underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L750-753)
```text
        // Create and store the new withdrawal request.
        withdrawalRequests[requestId] = WithdrawalRequest({
            rsETHUnstaked: rsETHUnstaked, expectedAssetAmount: expectedAssetAmount, withdrawalStartBlock: block.number
        });
```
