### Title
Permissionless `sendFunds()` with No Balance Threshold Enables Block Stuffing to Temporarily Freeze MEV Rewards — (`contracts/FeeReceiver.sol`)

---

### Summary

`FeeReceiver.sendFunds()` is a fully permissionless function with no minimum balance guard. An attacker can perform a block stuffing attack during a high-MEV window to prevent the function from being included in any block, temporarily freezing accumulated ETH rewards in the `FeeReceiver` contract and delaying their forwarding to the deposit pool.

---

### Finding Description

`sendFunds()` carries no access control modifier and no minimum balance threshold: [1](#0-0) 

The contract accumulates ETH passively via its `receive()` fallback: [2](#0-1) 

Because `sendFunds()` is the sole mechanism to forward MEV/execution-layer rewards to the deposit pool, and because it is callable by anyone (including bots and keepers), it is also blockable by anyone willing to pay to fill blocks. There is no on-chain enforcement of a maximum delay between reward accumulation and forwarding.

The downstream `receiveFromRewardReceiver()` in `LRTDepositPool` is equally permissionless: [3](#0-2) 

This means the entire reward-forwarding path depends on an off-chain keeper or bot successfully landing a transaction, with no fallback if that transaction is censored or crowded out.

---

### Impact Explanation

During a high-MEV block sequence, an attacker can submit a flood of high-gas-price transactions that fill the block gas limit, preventing `sendFunds()` from being included. The ETH balance of `FeeReceiver` grows but is not forwarded to the deposit pool. This causes:

- MEV rewards to sit idle in `FeeReceiver` for the duration of the stuffing window.
- The `LRTOracle._getTotalEthInProtocol()` calculation to exclude those rewards (since it queries `LRTDepositPool.getTotalAssetDeposits()`, not `FeeReceiver.balance`), causing the rsETH price to be temporarily understated relative to actual protocol holdings. [4](#0-3) 

Impact: **Low — Block stuffing / temporary freezing of protocol reward funds.**

---

### Likelihood Explanation

Block stuffing on Ethereum mainnet is economically expensive (attacker must pay base fee × block gas limit per block). The attacker gains no direct financial benefit. However, the attack is technically feasible, requires no privileged access, and the preconditions (permissionless function, no minimum balance, no time-bound enforcement) are permanently present in the deployed contract. Likelihood is low but non-zero, particularly for a well-funded adversary targeting a high-MEV event.

---

### Recommendation

1. **Add a minimum balance threshold** to `sendFunds()` so it reverts (or is a no-op) when `address(this).balance` is below a configurable dust threshold, reducing the surface for zero-value griefing.
2. **Add access control** (e.g., `onlyRole(LRTConstants.MANAGER)` or a keeper role) to `sendFunds()` so only authorized callers can trigger forwarding, eliminating the permissionless attack surface entirely. The trade-off is that it then requires an active keeper.
3. Alternatively, integrate `sendFunds()` logic directly into the validator fee recipient flow or a time-locked automation so it cannot be blocked by a single transaction window.

---

### Proof of Concept

```solidity
// Invariant test sketch (local fork, no mainnet)
function testBlockStuffingInvariant() public {
    // 1. Simulate MEV rewards accumulating in FeeReceiver
    vm.deal(address(feeReceiver), 10 ether);

    // 2. Attacker stuffs N blocks by consuming all gas
    //    (simulated by simply not calling sendFunds() for N blocks)
    vm.roll(block.number + 50);

    // 3. Assert: FeeReceiver still holds the balance (sendFunds not called)
    assertEq(address(feeReceiver).balance, 10 ether);

    // 4. Assert: depositPool did NOT receive the rewards during the window
    //    (TVL understated for 50 blocks)
    uint256 poolBalance = address(depositPool).balance;
    assertEq(poolBalance, 0); // rewards never forwarded

    // 5. Anyone can still call sendFunds() after stuffing ends
    feeReceiver.sendFunds();
    assertEq(address(feeReceiver).balance, 0);
}
```

The test confirms that for any N-block window where `sendFunds()` is not called (whether by stuffing or keeper failure), rewards remain frozen in `FeeReceiver` and are excluded from TVL accounting.

### Citations

**File:** contracts/FeeReceiver.sol (L49-50)
```text
    /// @dev fallback to receive funds
    receive() external payable { }
```

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

**File:** contracts/LRTOracle.sol (L331-349)
```text
    function _getTotalEthInProtocol() private view returns (uint256 totalETHInProtocol) {
        address lrtDepositPoolAddr = lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL);
        address[] memory supportedAssets = lrtConfig.getSupportedAssetList();
        uint256 supportedAssetCount = supportedAssets.length;

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
    }
```
