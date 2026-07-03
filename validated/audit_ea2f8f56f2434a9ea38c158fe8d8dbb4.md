Audit Report

## Title
ETH Deposit Limit Not Enforced Correctly Due to Missing Amount in Boundary Check - (File: contracts/LRTDepositPool.sol)

## Summary
`_checkIfDepositAmountExceedesCurrentLimit` uses an inconsistent comparison for ETH versus ERC-20 assets: the ETH branch omits the incoming `amount` from the limit check, while the ERC-20 branch correctly includes it. As a result, when `totalAssetDeposits` equals the configured cap exactly, any ETH deposit passes the gate, mints rsETH, and pushes the protocol above its governance-approved ceiling.

## Finding Description
In `contracts/LRTDepositPool.sol` at lines 676–682, the function reads:

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));   // amount omitted
    }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset)); // amount included
}
``` [1](#0-0) 

The ETH branch evaluates `totalAssetDeposits > limit`. When `totalAssetDeposits == limit` this returns `false`, signalling "not exceeded," so `_beforeDeposit` does not revert. [2](#0-1) 

The full unprivileged call path is:

```
depositETH{value: X}(...)          // payable, no role check
  → _beforeDeposit(ETH_TOKEN, X)
      → _checkIfDepositAmountExceedesCurrentLimit(ETH_TOKEN, X)
          // returns false when totalAssetDeposits == limit
  → _mintRsETH(rsethAmountToMint)  // mints above cap
``` [3](#0-2) 

No existing guard compensates for this omission; `_beforeDeposit` is the sole pre-mint validation. [4](#0-3) 

## Impact Explanation
The deposit cap is the protocol's primary mechanism for bounding per-asset ETH exposure. The ETH branch's missing `amount` means the cap can be exceeded by any non-zero deposit once the total reaches the limit exactly. The protocol mints rsETH beyond the governance-approved ceiling, failing to deliver the promised deposit-cap constraint. No direct loss of user funds occurs. This matches the allowed impact: **Low — contract fails to deliver promised returns**.

## Likelihood Explanation
The condition is reachable by any depositor with no special role or privilege. A depositor whose transaction brings `totalAssetDeposits` to exactly the limit, or any depositor who observes that state on-chain, can immediately exploit it. No front-running advantage, oracle dependency, or external protocol interaction is required. The step is repeatable until governance intervenes.

## Recommendation
Add `amount` to the ETH branch to mirror the ERC-20 branch:

```solidity
if (asset == LRTConstants.ETH_TOKEN) {
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
``` [5](#0-4) 

## Proof of Concept
1. Set `depositLimitByAsset(ETH_TOKEN) = 1000 ether` and arrange `getTotalAssetDeposits(ETH_TOKEN) = 1000 ether` (cap exactly reached).
2. Call `depositETH{value: 1 ether}(0, "")` from any EOA.
3. `_checkIfDepositAmountExceedesCurrentLimit(ETH_TOKEN, 1 ether)` evaluates `1000 ether > 1000 ether` → `false` → no revert.
4. `_mintRsETH` mints rsETH for the caller.
5. `getTotalAssetDeposits(ETH_TOKEN)` is now `1001 ether`, exceeding the cap.
6. Any subsequent depositor can repeat steps 2–5 indefinitely.

**Foundry test sketch:**
```solidity
function test_ethDepositExceedsCap() public {
    // fund pool to exactly the limit
    vm.deal(address(this), 1 ether);
    _setTotalETHDepositsToLimit(); // helper: set totalAssetDeposits == depositLimit
    // this call should revert but does not
    lrtDepositPool.depositETH{value: 1 ether}(0, "");
    assertGt(lrtDepositPool.getTotalAssetDeposits(LRTConstants.ETH_TOKEN),
              lrtConfig.depositLimitByAsset(LRTConstants.ETH_TOKEN));
}
```

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
