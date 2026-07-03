The code confirms the claim exactly. At line 679, the ETH branch returns `totalAssetDeposits > lrtConfig.depositLimitByAsset(asset)` without including `amount`, while the non-ETH branch at line 681 correctly uses `totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset)`. The exploit path through the public `depositETH` function is real and unrestricted.

---

Audit Report

## Title
ETH Deposit Limit Bypass Due to Missing `amount` in Limit Check - (File: contracts/LRTDepositPool.sol)

## Summary
`_checkIfDepositAmountExceedesCurrentLimit` applies an asymmetric formula: for ETH it checks `totalAssetDeposits > limit`, omitting the incoming `amount`, while for all other assets it correctly checks `totalAssetDeposits + amount > limit`. Because `totalAssetDeposits` reflects the pre-deposit state, any ETH deposit that would push the total above the configured cap passes the check and succeeds, rendering the ETH deposit limit unenforceable.

## Finding Description
`depositETH` (L76–93) is a public payable function that calls `_beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected)` (L87). `_beforeDeposit` (L648–670) calls `_checkIfDepositAmountExceedesCurrentLimit(asset, depositAmount)` at L661 before any state change. Inside that function (L676–682):

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset)); // ← amount omitted
    }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

The ETH branch never incorporates `amount`. As long as the pre-deposit total is at or below the limit, the check returns `false` regardless of how large `amount` is, and the deposit proceeds. No other guard in `depositETH` or `_beforeDeposit` enforces the cap.

Exploit path:
1. `depositLimitByAsset(ETH_TOKEN)` = 1000 ETH; `getTotalAssetDeposits(ETH_TOKEN)` = 999 ETH.
2. Attacker calls `depositETH{value: 500 ether}(0, "")`.
3. Check: `999e18 > 1000e18` → `false` → no revert.
4. 500 ETH is accepted; post-deposit total = 1499 ETH, 49.9% above the configured limit.
5. Attacker receives rsETH for the full 500 ETH.

## Impact Explanation
The deposit limit is the primary on-chain mechanism for bounding ETH exposure. Bypassing it means the protocol does not enforce its own promised constraint. This maps to **Low — Contract fails to deliver promised behavior (enforced deposit cap)** without directly causing loss or freezing of funds.

## Likelihood Explanation
`depositETH` has no access control beyond `nonReentrant`, `whenNotPaused`, and `onlySupportedAsset`. Any unprivileged user can call it at any time. The condition is trivially reachable whenever `totalAssetDeposits <= depositLimitByAsset(ETH_TOKEN)`, which is the normal operating state. The attack is repeatable in a single transaction. Likelihood is **High**.

## Recommendation
Unify the ETH and non-ETH branches by always including `amount`:

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

## Proof of Concept
Foundry unit test outline:

```solidity
function test_ethDepositLimitBypass() public {
    // Admin sets ETH deposit limit to 1000 ether
    lrtConfig.setDepositLimitByAsset(LRTConstants.ETH_TOKEN, 1000 ether);

    // Simulate 999 ETH already deposited (mock getTotalAssetDeposits to return 999e18)
    // OR deposit 999 ETH in prior calls to reach the state

    // Attacker deposits 500 ETH — should revert but does not
    vm.deal(attacker, 500 ether);
    vm.prank(attacker);
    lrtDepositPool.depositETH{value: 500 ether}(0, "");

    // Assert total exceeds limit
    uint256 total = lrtDepositPool.getTotalAssetDeposits(LRTConstants.ETH_TOKEN);
    assertGt(total, 1000 ether); // passes: total == 1499 ether
}
``` [1](#0-0) [2](#0-1) [3](#0-2)

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
