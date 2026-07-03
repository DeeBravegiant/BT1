### Title
First-Depositor Inflationary Attack via Permissionless `updateRSETHPrice()` and Open ETH Receiver — (File: contracts/LRTOracle.sol, contracts/LRTDepositPool.sol)

---

### Summary

`LRTOracle.updateRSETHPrice()` is callable by any unprivileged address. `LRTDepositPool` accepts ETH from anyone via its open `receive()` function, and `getETHDistributionData()` counts `address(this).balance` directly. An attacker who is the first depositor can donate ETH to the pool, trigger a price update, and inflate the stored `rsETHPrice`, causing subsequent depositors to receive a disproportionately small amount of rsETH. The attacker recovers the donated ETH by redeeming their rsETH shares, effectively stealing the victim's deposit.

---

### Finding Description

**Step 1 — Permissionless price update.**

`LRTOracle.updateRSETHPrice()` has no role guard:

```solidity
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
``` [1](#0-0) 

**Step 2 — Zero-supply bootstrap sets price to 1:1.**

When `rsethSupply == 0`, `_updateRsETHPrice()` unconditionally sets `rsETHPrice = 1 ether` and returns, giving the first depositor a guaranteed 1:1 mint rate:

```solidity
if (rsethSupply == 0) {
    rsETHPrice = 1 ether;
    highestRsethPrice = 1 ether;
    return;
}
``` [2](#0-1) 

**Step 3 — Open ETH receiver in the deposit pool.**

`LRTDepositPool` accepts ETH from any caller with no access control:

```solidity
receive() external payable { }
``` [3](#0-2) 

**Step 4 — `address(this).balance` is used directly as TVL.**

`getETHDistributionData()` counts the raw contract balance, including any donated ETH:

```solidity
ethLyingInDepositPool = address(this).balance;
``` [4](#0-3) 

**Step 5 — Inflated price propagates to minting.**

`getRsETHAmountToMint()` divides by the stored `rsETHPrice`:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [5](#0-4) 

**Step 6 — Price-increase guard is disabled by default.**

The `pricePercentageLimit` is initialized to `0`. The upside guard is:

```solidity
bool isPriceIncreaseOffLimit =
    pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);
```

When `pricePercentageLimit == 0` the entire check short-circuits to `false`, so arbitrarily large price jumps are accepted from any caller. [6](#0-5) 

---

### Impact Explanation

**Critical — direct theft of depositor funds.**

The attacker recovers their ETH donation by redeeming rsETH shares that now represent a larger fraction of the pool (which includes the victim's deposit). The victim receives near-zero rsETH for their ETH, and the attacker redeems the inflated share to claim both their donation and the victim's ETH.

---

### Likelihood Explanation

**Medium.** The attack is most effective at protocol launch when rsETH supply is minimal. It requires the attacker to front-run the first legitimate depositor and to bear temporary ETH capital cost (recovered on redemption). No privileged role is needed; the two entry points (`updateRSETHPrice()` and `receive()`) are fully open to any EOA or contract.

---

### Recommendation

1. **Bootstrap the initial supply**: mint a non-trivial amount of rsETH to a dead address (e.g., `address(0xdead)`) during initialization so the 1:1 bootstrap window is never reachable by an unprivileged depositor.
2. **Restrict `updateRSETHPrice()`**: require at minimum a `KEEPER_ROLE` or equivalent, or enforce a minimum time-between-updates.
3. **Do not count raw `address(this).balance` as TVL**: track deposited ETH in a dedicated storage variable that is only incremented through controlled entry points (`depositETH`, `receiveFromRewardReceiver`, etc.), preventing force-sent ETH from inflating the price.
4. **Set a non-zero `pricePercentageLimit`** as part of the deployment/initialization sequence so the upside guard is active from day one.

---

### Proof of Concept

```
Precondition: protocol just deployed, rsETH totalSupply = 0, pricePercentageLimit = 0.

1. Attacker calls LRTOracle.updateRSETHPrice()
   → rsETHPrice = 1e18 (1:1, supply is 0)

2. Attacker calls LRTDepositPool.depositETH{value: minAmountToDeposit}(0, "")
   → rsethAmountToMint = minAmountToDeposit * 1e18 / 1e18 = minAmountToDeposit
   → Attacker holds minAmountToDeposit rsETH (e.g., 1e15 wei = 0.001 ETH)

3. Attacker sends 100 ETH directly to LRTDepositPool via receive()
   → address(LRTDepositPool).balance = 100.001 ETH

4. Attacker calls LRTOracle.updateRSETHPrice()
   → totalETHInProtocol = 100.001e18
   → rsethSupply = 1e15
   → newRsETHPrice = 100.001e18 * 1e18 / 1e15 = 100_001e18
   → rsETHPrice is now 100,001 ETH per rsETH

5. Victim calls LRTDepositPool.depositETH{value: 1 ether}(0, "")
   → rsethAmountToMint = 1e18 * 1e18 / 100_001e18 ≈ 9999 wei rsETH
   → Victim receives ~0.000009999 rsETH for 1 ETH

6. Attacker redeems 1e15 rsETH
   → Attacker's share = 1e15 / (1e15 + 9999) ≈ 99.999%
   → Attacker recovers ≈ 101.001 ETH (100 ETH donation + 1 ETH victim deposit + 0.001 ETH own deposit)
   → Net profit ≈ 1 ETH (victim's deposit), minus gas
```

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L218-222)
```text
        if (rsethSupply == 0) {
            rsETHPrice = 1 ether;
            highestRsethPrice = 1 ether;
            return;
        }
```

**File:** contracts/LRTOracle.sol (L256-257)
```text
            bool isPriceIncreaseOffLimit =
                pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);
```

**File:** contracts/LRTDepositPool.sol (L58-58)
```text
    receive() external payable { }
```

**File:** contracts/LRTDepositPool.sol (L480-480)
```text
        ethLyingInDepositPool = address(this).balance;
```

**File:** contracts/LRTDepositPool.sol (L520-520)
```text
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```
