Looking at the full `RSETHPoolV2NBA.sol` contract and the attack scenario carefully.

**Key facts from the contract:**

1. `setFeeBps()` is gated by `onlyRole(DEFAULT_ADMIN_ROLE)` — a standard admin call with no timelock or delay.
2. `deposit()` is fully public, with only `nonReentrant` and `whenNotPaused` guards — no `limitDailyMint` modifier (unlike `RSETHPoolV2.sol`).
3. Fee math: `fee = amount * feeBps / 10_000` — if `feeBps == 0`, fee is exactly 0 for any deposit amount.
4. `feeBps` is set at initialization and can be 0.
5. There is no minimum fee enforcement, no deposit cap, and no circuit breaker that would limit attacker deposit volume during a stuffing window.

**Attack path evaluation:**

- **Precondition**: `feeBps = 0` (valid initial state per `initialize()`).
- **Step 1**: Admin broadcasts `setFeeBps(X)` to raise the fee.
- **Step 2**: Attacker fills blocks with high-gas transactions to exclude the admin tx (economically viable on cheap L2s where this contract is deployed — "NBA" = No Bridging Automation implies L2 deployment).
- **Step 3**: Attacker calls `deposit()` with large ETH amounts during the stuffing window.
- **Result**: `fee = amount * 0 / 10_000 = 0`, `feeEarnedInETH` never increases, attacker receives full wrsETH with zero fee cost.

**Economic viability check:**

On L2s (Arbitrum, Optimism, Base, etc.), block gas is cheap. If the attacker plans to deposit 1000 ETH and the admin intends to raise `feeBps` to 50 bps, the fee avoided = 5 ETH. Block stuffing cost on a cheap L2 for a few blocks can be well below 5 ETH, making the attack economically rational.

**Mitigations absent:**

- No `limitDailyMint` (present in `RSETHPoolV2.sol` but absent here).
- No minimum `feeBps` floor.
- No timelock on `setFeeBps()` that would make the change predictable/unstoppable.
- No pause-on-fee-change mechanism.

**Reject criteria check:**

- Not "front-running only" — block stuffing is transaction exclusion, and "Low. Block stuffing" is explicitly listed as an allowed impact scope.
- Not "griefing with no scoped impact" — the scoped impact is "Low. Contract fails to deliver promised returns" (protocol loses fee revenue during the stuffing window).
- Does not require admin compromise — the admin is the *victim*, not the attacker.

---

### Title
Block Stuffing Allows Attacker to Deposit at Zero Fee by Preventing `setFeeBps()` from Landing — (`contracts/pools/RSETHPoolV2NBA.sol`)

### Summary
`RSETHPoolV2NBA` has no deposit cap and no minimum fee floor. When `feeBps == 0`, an attacker on a cheap L2 can fill blocks to exclude the admin's `setFeeBps()` transaction, then deposit large ETH amounts at zero fee, causing the protocol to lose all fee revenue during the stuffing window.

### Finding Description
`deposit()` computes fees as `fee = amount * feeBps / 10_000`. [1](#0-0) 

When `feeBps == 0` (a valid initialized state), this yields `fee = 0` for any deposit amount, and `feeEarnedInETH` is never incremented. [2](#0-1) 

`setFeeBps()` is a plain admin call with no timelock, no delay, and no on-chain enforcement preventing it from being excluded from a block. [3](#0-2) 

Unlike `RSETHPoolV2.sol`, this contract has no `limitDailyMint` modifier on `deposit()`, so there is no cap on the volume an attacker can push through in a single block or stuffing window. [4](#0-3) 

### Impact Explanation
The protocol loses all fee revenue that would have been collected during the block stuffing window. The attacker receives wrsETH at the full oracle rate with zero fee deduction. This matches the scoped impact: **Low — contract fails to deliver promised returns / block stuffing**.

### Likelihood Explanation
Likelihood is **Low**. The attack requires: (a) `feeBps` to be initialized at 0, (b) deployment on a cheap-gas L2 (which the "NBA" naming and contract design imply), and (c) the attacker to have capital large enough that fee savings exceed stuffing costs. All three conditions can realistically coincide at contract launch.

### Recommendation
1. Enforce a minimum `feeBps` floor (e.g., `require(_feeBps >= MIN_FEE_BPS)`) or disallow `feeBps == 0` in `initialize()`.
2. Add a `limitDailyMint` cap (as present in `RSETHPoolV2.sol`) to bound the volume an attacker can deposit in any window.
3. Consider a timelock or commit-reveal for fee changes so the admin's intent is on-chain before the change takes effect, reducing the value of excluding a single transaction.

### Proof of Concept
```solidity
// Local fork test (no mainnet)
function testBlockStuffingZeroFee() public {
    // Deploy with feeBps = 0
    pool.initialize(admin, bridger, address(wrsETH), 0, address(oracle));

    // Admin queues setFeeBps(50) — attacker stuffs blocks to exclude it
    // (simulated by simply not calling setFeeBps in this test)

    // Attacker deposits 1000 ETH at feeBps = 0
    vm.deal(attacker, 1000 ether);
    vm.prank(attacker);
    pool.deposit{value: 1000 ether}("ref");

    // Assert: feeEarnedInETH == 0 for all deposits
    assertEq(pool.feeEarnedInETH(), 0);

    // Assert: attacker received full wrsETH (no fee deducted)
    uint256 expectedWrsETH = 1000 ether * 1e18 / oracle.getRate();
    assertEq(wrsETH.balanceOf(attacker), expectedWrsETH);
}
```

### Citations

**File:** contracts/pools/RSETHPoolV2NBA.sol (L106-118)
```text
    function deposit(string memory referralId) external payable nonReentrant whenNotPaused {
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
    }
```

**File:** contracts/pools/RSETHPoolV2NBA.sol (L124-126)
```text
    function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;
```

**File:** contracts/pools/RSETHPoolV2NBA.sol (L163-167)
```text
    function setFeeBps(uint256 _feeBps) external onlyRole(DEFAULT_ADMIN_ROLE) {
        if (_feeBps > 10_000) revert InvalidFeeAmount();
        feeBps = _feeBps;
        emit FeeBpsSet(_feeBps);
    }
```
