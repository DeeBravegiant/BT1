### Title
Stale Cross-Chain Rate Allows Over-Issuance of wrsETH, Leading to Pool Undercollateralization — (`contracts/cross-chain/CrossChainRateReceiver.sol`)

---

### Summary

`CrossChainRateReceiver.getRate()` returns the last stored rate with no staleness check. Both `RSETHPoolV2` and `RSETHPoolV3` use this rate directly to compute how much `wrsETH` to mint per ETH deposited. Because rsETH/ETH is monotonically increasing (staking rewards), any lag between an L1 rate update and the next LayerZero delivery creates a window where depositors receive more `wrsETH` than the deposited ETH can cover at the true rate, undercollateralizing the pool.

---

### Finding Description

`CrossChainRateReceiver` stores the rate and a `lastUpdated` timestamp but exposes no staleness guard: [1](#0-0) 

Both pool versions delegate pricing entirely to this oracle: [2](#0-1) [3](#0-2) 

`RSETHPoolV3` is identical in structure: [4](#0-3) [5](#0-4) 

The `deposit()` function mints `wrsETH` directly from this calculation with no additional rate-freshness guard: [6](#0-5) 

**Attack path:**

1. L1 rsETH/ETH rate increases from R0 → R1 (R1 > R0) due to staking rewards.
2. The LZ message has not yet been relayed; `RSETHRateReceiver.rate` still holds R0.
3. Attacker calls `RSETHPoolV2.deposit{value: E}()`.
4. Pool computes `rsETHAmount = E * 1e18 / R0`, which is greater than `E * 1e18 / R1`.
5. Attacker receives the excess `wrsETH` delta: `E * 1e18 * (1/R0 − 1/R1)`.
6. Attacker bridges `wrsETH` to L1, unwraps to rsETH, and redeems at R1.
7. Attacker extracts `E * (R1/R0 − 1)` ETH of value that was never deposited.

The pool has issued more `wrsETH` than the ETH it holds can redeem at the true rate, creating a structural deficit.

---

### Impact Explanation

**Protocol insolvency.** The pool's ETH reserves are insufficient to back all outstanding `wrsETH` at the true rsETH/ETH rate. Every deposit made during a stale-rate window permanently widens this gap. The `dailyMintLimit` caps per-day exposure but does not prevent the exploit; it merely bounds the rate of insolvency accumulation. [7](#0-6) 

---

### Likelihood Explanation

- rsETH/ETH increases continuously (staking rewards ~4–5% APY).
- LZ relayer delays are normal operational events; there is no on-chain SLA enforcing freshness.
- The contract stores `lastUpdated` but never uses it as a guard, so any delay — routine or adversarial (e.g., relayer censorship, gas spike) — opens the window.
- The attack requires no special role, no front-running, and no external protocol compromise: a single `deposit()` call suffices.
- Profit per deposit is small under normal conditions (~0.01%/day lag) but scales with deposit size and stale duration, and is repeatable up to the daily limit.

---

### Recommendation

Add a configurable maximum staleness threshold and revert in `getRate()` if exceeded:

```solidity
uint256 public maxStaleness; // e.g., 24 hours

function getRate() external view returns (uint256) {
    require(
        block.timestamp - lastUpdated <= maxStaleness,
        "Rate is stale"
    );
    return rate;
}
```

This causes `deposit()` to revert automatically when the oracle is stale, eliminating the exploit window without requiring any admin intervention.

---

### Proof of Concept

```solidity
// Fork test (Foundry) — run against a chain where RSETHRateReceiver is deployed
function testStaleRateOverIssuance() external {
    uint256 R0 = rateReceiver.getRate(); // e.g., 1.05e18

    // Simulate L1 rate increase without LZ delivery
    vm.warp(block.timestamp + 7 days);
    // rateReceiver.rate is still R0

    uint256 R1 = 1.06e18; // true rate after 7 days of rewards

    uint256 E = 10 ether;
    uint256 rsETHAtStale  = E * 1e18 / R0;  // more rsETH
    uint256 rsETHAtTrue   = E * 1e18 / R1;  // correct rsETH

    vm.deal(attacker, E);
    vm.prank(attacker);
    pool.deposit{value: E}("ref");

    uint256 minted = wrsETH.balanceOf(attacker);
    assertGt(minted, rsETHAtTrue, "Over-issued wrsETH");
    assertEq(minted, rsETHAtStale);

    // Excess wrsETH bridged to L1 and redeemed at R1 extracts:
    // (minted - rsETHAtTrue) * R1 / 1e18 ETH from the protocol
}
```

### Citations

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L102-105)
```text
    /// @notice Gets the last stored rate in the contract
    function getRate() external view returns (uint256) {
        return rate;
    }
```

**File:** contracts/pools/RSETHPoolV2.sol (L72-93)
```text
    modifier limitDailyMint(uint256 amount) {
        if (block.timestamp < startTimestamp) {
            revert MintBeforeStartTimestamp();
        }

        // Calculate the amount of rsETH that will be minted
        (uint256 rsETHAmount,) = viewSwapRsETHAmountAndFee(amount);
        uint256 currentDay = getCurrentDay();

        // If the current day is greater than the last mint day, reset the daily mint amount
        if (currentDay > lastMintDay) {
            lastMintDay = currentDay;
            dailyMintAmount = 0;
        }

        // Check if the daily mint amount plus the amount to mint is greater than the daily mint limit
        if (dailyMintAmount + rsETHAmount > dailyMintLimit) {
            revert DailyMintLimitExceeded();
        }

        dailyMintAmount += rsETHAmount;
        _;
```

**File:** contracts/pools/RSETHPoolV2.sol (L201-203)
```text
    function getRate() public view returns (uint256) {
        return IOracle(rsETHOracle).getRate();
    }
```

**File:** contracts/pools/RSETHPoolV2.sol (L207-218)
```text
    function deposit(string memory referralId) external payable nonReentrant whenNotPaused limitDailyMint(msg.value) {
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
```

**File:** contracts/pools/RSETHPoolV2.sol (L225-233)
```text
    function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```

**File:** contracts/pools/RSETHPoolV3.sol (L235-237)
```text
    function getRate() public view returns (uint256) {
        return IOracle(rsETHOracle).getRate();
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L299-307)
```text
    function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```
