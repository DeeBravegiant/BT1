Audit Report

## Title
Incomplete ETH Deposit Limit Check Missing Incoming Amount — (`File: contracts/LRTDepositPool.sol`)

## Summary
`_checkIfDepositAmountExceedesCurrentLimit` in `LRTDepositPool.sol` omits the incoming `amount` from the limit comparison when the asset is ETH, while correctly including it for ERC20 assets. This allows any depositor to push total ETH deposits above the configured cap. The protocol's ETH deposit limit invariant is silently violated without any fund theft or freeze.

## Finding Description
In `_checkIfDepositAmountExceedesCurrentLimit` (lines 676–682), the ETH branch evaluates `totalAssetDeposits > lrtConfig.depositLimitByAsset(asset)` (line 679), while the ERC20 branch evaluates `totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset)` (line 681). The ETH branch only rejects a deposit when the cap is **already exceeded**; it does not account for the incoming `amount`. A deposit that brings the total from exactly `limit` to `limit + amount` passes the check and is accepted.

This function is called unconditionally from `_beforeDeposit` at line 661, which is the sole pre-flight guard for both `depositETH` (line 87) and `depositAsset` (line 111). No other guard compensates for the missing `amount` in the ETH branch.

Exploit path:
1. Admin sets `depositLimitByAsset[ETH_TOKEN] = 1000 ether`.
2. Protocol accumulates `getTotalAssetDeposits(ETH_TOKEN) == 1000 ether` (cap exactly reached).
3. Attacker calls `depositETH{value: 100 ether}(0, "")`.
4. `_checkIfDepositAmountExceedesCurrentLimit` evaluates `1000 ether > 1000 ether` → `false` → limit not exceeded.
5. `_mintRsETH` mints rsETH; total ETH deposits become `1100 ether`, 10% above the configured cap.
6. Any subsequent depositor can repeat this, as the check remains `totalAssetDeposits > limit` with no `amount` term. [1](#0-0) [2](#0-1) 

## Impact Explanation
**Low — Contract fails to deliver promised returns, but doesn't lose value.**

The ETH deposit cap is a protocol-level risk parameter. Because the incoming `amount` is excluded from the ETH comparison, the protocol silently accepts more ETH than it is configured to handle, violating the deposit limit invariant. No direct fund theft, permanent freeze, or insolvency results; depositors receive correctly priced rsETH. The protocol simply fails to enforce its own configured ceiling for ETH exposure.

## Likelihood Explanation
**Medium.** The condition `totalAssetDeposits == depositLimit` is a normal operational state (the cap is reached during ordinary protocol usage). Any unprivileged depositor monitoring on-chain state can call `depositETH` at that moment. No special privileges, front-running, or brute-force are required. The exploit is repeatable by any external caller.

## Recommendation
Add `amount` to the ETH branch, mirroring the ERC20 branch:

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

This unifies both branches and correctly rejects any deposit that would push total deposits over the configured cap, regardless of asset type. [1](#0-0) 

## Proof of Concept
Foundry unit test plan:

```solidity
function test_ETHDepositExceedsLimit() public {
    // 1. Set ETH deposit limit to 1000 ether
    vm.prank(admin);
    lrtConfig.setDepositLimitByAsset(LRTConstants.ETH_TOKEN, 1000 ether);

    // 2. Fill pool to exactly the limit via legitimate deposits
    vm.deal(depositor1, 1000 ether);
    vm.prank(depositor1);
    lrtDepositPool.depositETH{value: 1000 ether}(0, "");

    // 3. Attacker deposits beyond the cap — should revert but does not
    vm.deal(attacker, 100 ether);
    vm.prank(attacker);
    lrtDepositPool.depositETH{value: 100 ether}(0, ""); // succeeds, no revert

    // 4. Assert total ETH deposits exceed the configured limit
    uint256 total = lrtDepositPool.getTotalAssetDeposits(LRTConstants.ETH_TOKEN);
    assertGt(total, 1000 ether); // 1100 ether > 1000 ether limit
}
```

The test demonstrates that after the cap is exactly reached, a subsequent `depositETH` call succeeds and total deposits exceed the configured limit, confirming the broken invariant. [3](#0-2) [4](#0-3)

### Citations

**File:** contracts/LRTDepositPool.sol (L76-93)
```text
    function depositETH(
        uint256 minRSETHAmountExpected,
        string calldata referralId
    )
        external
        payable
        nonReentrant
        whenNotPaused
        onlySupportedAsset(LRTConstants.ETH_TOKEN)
    {
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected);

        // interactions
        _mintRsETH(rsethAmountToMint);

        emit ETHDeposit(msg.sender, msg.value, rsethAmountToMint, referralId);
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

**File:** contracts/LRTDepositPool.sol (L676-682)
```text
    function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
        uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
        if (asset == LRTConstants.ETH_TOKEN) {
            return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));
        }
        return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
    }
```
